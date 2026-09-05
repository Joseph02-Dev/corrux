"""Tests de corrux-setup — assistant d'installation initiale (§4, §11.1)
— TECH-012.

« Test d'installation sur environnement propre (scénario scripté) »
(contrat explicite du ticket) — aucun mock : vrai périphérique
loopback (fichier-support, jamais un disque système réel) réellement
formaté et monté, vraie CA/certificat (TECH-010), vrai cycle de
sauvegarde chiffré (TECH-009), vraie activation de modules (TECH-006/
007). Démarche entièrement vérifiée manuellement (script ad hoc) avant
d'en faire des tests pytest permanents — chaque étape confirmée
fonctionnelle de bout en bout avant d'écrire le moindre test.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client

from core.audit.models import AuditLog
from core.authz.engine import has_permission
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.backup.database import DatabaseConnectionParams
from core.backup.models import BackupRun
from core.certs.service import CertificatePaths
from core.identity.models import User
from core.modules.models import Module
from ops.setup_service import (
    CorruxSetupConfig,
    SetupError,
    complete_administrator_bootstrap,
    create_initial_admin,
    detect_candidate_volumes,
    format_volume,
    mount_volume_persistently,
    run_corrux_setup,
)

LOSETUP_AVAILABLE = shutil.which("losetup") is not None and shutil.which("mkfs.ext4") is not None
pytestmark = pytest.mark.skipif(
    not LOSETUP_AVAILABLE, reason="losetup/mkfs.ext4 non disponibles dans cet environnement"
)


def _db_params() -> DatabaseConnectionParams:
    return DatabaseConnectionParams(
        name=os.environ["DB_NAME"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"], host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
    )


@pytest.fixture
def loop_device(tmp_path):
    """Un vrai périphérique loopback (fichier-support de 20 Mo) —
    jamais un disque système réel. Nettoyage garanti (démontage +
    détachement) même en cas d'échec du test."""
    image_path = tmp_path / "backup_volume.img"
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={image_path}", "bs=1M", "count=20"],
        check=True, capture_output=True,
    )
    device_path = subprocess.run(
        ["losetup", "-f", "--show", str(image_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    try:
        yield device_path
    finally:
        subprocess.run(["umount", device_path], capture_output=True, check=False)
        subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)


@pytest.fixture
def full_setup_config(tmp_path, loop_device, gpg_keypair) -> CorruxSetupConfig:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    return CorruxSetupConfig(
        admin_username="admin_setup", admin_password="Password123!",
        admin_full_name="Admin Setup",
        certificate_common_name="corrux-test.local",
        certificate_paths=CertificatePaths(
            ca_key=tmp_path / "ca.key", ca_cert=tmp_path / "ca.crt",
            server_key=tmp_path / "server.key", server_cert=tmp_path / "server.crt",
        ),
        backup_device_path=loop_device,
        backup_mount_point=tmp_path / "mnt",
        backup_fstab_path=tmp_path / "fake_fstab",
        format_backup_volume=True,
        documentation_manifest_path=Path("modules/documentation/manifest.yaml"),
        rh_manifest_path=Path("modules/rh/manifest.yaml"),
        backup_storage_root=storage_root,
        backup_gpg_recipient_key_path=gpg_keypair["public_key_path"],
        backup_db_params=_db_params(),
    )


# --- A. Détection de volumes -------------------------------------------------------


@pytest.mark.django_db
class TestDetectCandidateVolumes:
    def test_loop_device_appears_as_a_candidate(self, loop_device):
        candidates = detect_candidate_volumes()
        assert loop_device in [c.device_path for c in candidates]

    def test_root_device_never_appears_as_a_candidate(self, loop_device):
        """§11.1 : jamais le disque système."""
        root_result = subprocess.run(
            ["findmnt", "-no", "SOURCE", "/"], capture_output=True, text=True, check=True
        )
        root_device = root_result.stdout.strip()

        candidates = detect_candidate_volumes()

        assert root_device not in [c.device_path for c in candidates]

    def test_candidate_reports_correct_size_and_no_fstype_before_formatting(
        self, loop_device
    ):
        candidates = detect_candidate_volumes()
        candidate = next(c for c in candidates if c.device_path == loop_device)
        assert candidate.fstype is None
        assert candidate.already_mounted_at is None


# --- B. Formatage et montage -------------------------------------------------------


@pytest.mark.django_db
class TestFormatAndMount:
    def test_format_volume_actually_formats_it(self, loop_device):
        format_volume(loop_device, actor=None)

        # blkid reflète immédiatement le nouveau système de fichiers ;
        # lsblk peut afficher un cache non rafraîchi juste après mkfs
        # (vérifié directement, pas supposé).
        result = subprocess.run(["blkid", loop_device], capture_output=True, text=True)
        assert 'TYPE="ext4"' in result.stdout

    def test_format_records_an_audit_event(self, loop_device):
        actor = User.objects.create(username="tech_format_test", full_name="Technicien")
        format_volume(loop_device, actor=actor)

        assert AuditLog.objects.filter(
            actor_user=actor, action="setup.volume_formatted", target=loop_device
        ).exists()

    def test_mount_persists_a_real_fstab_entry(self, tmp_path, loop_device):
        format_volume(loop_device, actor=None)
        mount_point = tmp_path / "mnt"
        fstab_path = tmp_path / "fake_fstab"
        fstab_path.write_text("")

        mount_volume_persistently(
            loop_device, mount_point, fstab_path=fstab_path, actor=None,
        )

        assert loop_device in fstab_path.read_text()
        assert str(mount_point) in fstab_path.read_text()

    def test_mount_never_touches_the_real_system_fstab(self):
        """Chemin fstab toujours injectable — jamais /etc/fstab codé en
        dur, vérifié explicitement pour ne jamais régresser."""
        import inspect

        from ops import setup_service

        source = inspect.getsource(setup_service)
        assert '"/etc/fstab"' not in source
        assert "'/etc/fstab'" not in source

    def test_volume_is_actually_mounted_and_writable(self, tmp_path, loop_device):
        format_volume(loop_device, actor=None)
        mount_point = tmp_path / "mnt"
        fstab_path = tmp_path / "fake_fstab"
        fstab_path.write_text("")

        mount_volume_persistently(
            loop_device, mount_point, fstab_path=fstab_path, actor=None,
        )

        test_file = mount_point / "ecriture-reelle.txt"
        test_file.write_text("contenu")
        assert test_file.read_text() == "contenu"


# --- C. Verrou de démarrage résolu — point trouvé lors de l'audit --------------


@pytest.mark.django_db
class TestAdministratorBootstrap:
    def test_administrator_role_starts_empty(self, db):
        """Confirme le verrou de démarrage tel que découvert — avant
        correction, ce rôle est réellement vide."""
        role = Role.objects.get(name="Administrateur")
        assert not RolePermission.objects.filter(role=role).exists()

    def test_core_platform_permissions_are_seeded_by_migration(self, db):
        """Découverte majeure de l'audit Phase 1 de ce ticket : les
        permissions core.* (gestion utilisateurs/rôles/modules/
        sauvegardes/audit) n'étaient enregistrées nulle part — corrigé
        par la migration 0007. Vérifié explicitement ici, pas seulement
        supposé appliqué."""
        expected = {
            ("core", "user", "read"), ("core", "user", "write"),
            ("core", "module", "read"), ("core", "module", "write"),
            ("core", "role", "write"),
            ("core", "backup", "read"),
            ("core", "audit", "read"),
        }
        actual = set(
            Permission.objects.filter(module_id="core").values_list(
                "module_id", "resource", "action"
            )
        )
        assert expected <= actual

    def test_bootstrap_grants_all_currently_registered_permissions(self, db):
        """7 permissions core.* déjà seedées par la migration 0007
        (audit Phase 1, TECH-012) + les 2 ajoutées ici explicitement."""
        permissions_before = Permission.objects.count()
        Permission.objects.get_or_create(module_id="rh", resource="employee", action="read")
        Permission.objects.get_or_create(module_id="rh", resource="employee", action="write")
        actor = User.objects.create(username="admin_bootstrap_test", full_name="Admin")

        granted_count = complete_administrator_bootstrap(actor=actor)

        role = Role.objects.get(name="Administrateur")
        assert granted_count == permissions_before + 2
        assert RolePermission.objects.filter(role=role).count() == permissions_before + 2

    def test_bootstrap_records_an_audit_event(self, db):
        actor = User.objects.create(username="admin_bootstrap_test2", full_name="Admin")
        complete_administrator_bootstrap(actor=actor)

        assert AuditLog.objects.filter(
            actor_user=actor, action="setup.administrator_bootstrap_completed"
        ).exists()


# --- D. Compte administrateur initial ----------------------------------------------


@pytest.mark.django_db
class TestCreateInitialAdmin:
    def test_creates_a_real_user(self, db):
        user = create_initial_admin(
            username="admin_init_test", password="Password123!", full_name="Admin Init"
        )
        assert User.objects.filter(pk=user.pk).exists()

    def test_assigns_the_administrator_role(self, db):
        user = create_initial_admin(
            username="admin_init_test2", password="Password123!", full_name="Admin Init 2"
        )
        assert UserRole.objects.filter(user=user, role__name="Administrateur").exists()

    def test_the_created_account_can_actually_log_in(self, db):
        create_initial_admin(
            username="admin_login_test", password="Password123!", full_name="Admin Login"
        )
        client = Client()
        response = client.post(
            "/login/", {"username": "admin_login_test", "password": "Password123!"}
        )
        assert response.status_code == 302


# --- E. Scénario d'installation complet, sur environnement propre -------------
# Cas explicitement requis par le contrat du ticket.


@pytest.mark.django_db
class TestFullInstallationScenario:
    def test_full_setup_succeeds_end_to_end(self, full_setup_config):
        result = run_corrux_setup(full_setup_config)

        assert result.documentation_module.state == Module.State.ACTIVATED
        assert result.rh_module.state == Module.State.ACTIVATED
        assert result.backup_run.status == BackupRun.Status.SUCCESS
        assert result.permissions_granted_to_administrator > 0

    def test_https_certificate_is_functional_after_setup(self, full_setup_config):
        """Critère d'acceptation explicite : « HTTPS fonctionne »."""
        result = run_corrux_setup(full_setup_config)

        verify = subprocess.run(
            [
                "openssl", "verify",
                "-CAfile", str(full_setup_config.certificate_paths.ca_cert),
                str(full_setup_config.certificate_paths.server_cert),
            ],
            capture_output=True, text=True,
        )
        assert verify.returncode == 0
        assert result.admin is not None

    def test_administrator_can_use_the_system_immediately_after_setup(
        self, full_setup_config
    ):
        """Résout le verrou de démarrage : l'administrateur peut agir
        tout de suite après l'installation, pas seulement se
        connecter."""
        result = run_corrux_setup(full_setup_config)

        assert has_permission(result.admin, "rh.employee.write")
        assert has_permission(result.admin, "documentation.document.read")
        assert has_permission(result.admin, "core.module.write")

    def test_backup_volume_is_genuinely_separate_from_system_disk(self, full_setup_config):
        """§11.1 revérifié bout en bout — pas seulement supposé."""
        run_corrux_setup(full_setup_config)

        assert (
            os.stat(full_setup_config.backup_mount_point).st_dev != os.stat("/").st_dev
        )

    def test_setup_fails_atomically_without_a_designated_volume(self, tmp_path):
        """Critère d'acceptation explicite : « ne peut pas se terminer
        sans support désigné »."""
        config = CorruxSetupConfig(
            admin_username="admin_no_volume", admin_password="Password123!",
            admin_full_name="Admin",
            certificate_common_name="corrux-test.local",
            certificate_paths=CertificatePaths(
                ca_key=tmp_path / "ca.key", ca_cert=tmp_path / "ca.crt",
                server_key=tmp_path / "server.key", server_cert=tmp_path / "server.crt",
            ),
            backup_device_path="/dev/nonexistent-device-abc123",
            backup_mount_point=tmp_path / "mnt",
            backup_fstab_path=tmp_path / "fake_fstab",
            format_backup_volume=False,
            documentation_manifest_path=Path("modules/documentation/manifest.yaml"),
            rh_manifest_path=Path("modules/rh/manifest.yaml"),
            backup_storage_root=tmp_path / "storage",
            backup_gpg_recipient_key_path=tmp_path / "no-key",
            backup_db_params=_db_params(),
        )

        with pytest.raises(SetupError):
            run_corrux_setup(config)

        assert not User.objects.filter(username="admin_no_volume").exists()


# --- F. Commande CLI invocable réellement -----------------------------------------


@pytest.mark.django_db
class TestCorruxSetupCommand:
    def test_command_is_discoverable(self):
        from django.core.management import get_commands

        assert get_commands().get("corrux_setup") == "ops"

    def test_command_runs_end_to_end_in_scripted_mode(self, tmp_path, loop_device, gpg_keypair):
        """« Scénario scripté » — cas explicitement requis par le
        contrat du ticket : tous les paramètres fournis en ligne de
        commande, aucun prompt interactif."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        db_params = _db_params()

        call_command(
            "corrux_setup",
            "--admin-username=admin_cli_test",
            "--admin-password=Password123!",
            "--admin-full-name=Admin CLI",
            "--certificate-common-name=corrux-cli-test.local",
            f"--ca-key-path={tmp_path / 'ca.key'}",
            f"--ca-cert-path={tmp_path / 'ca.crt'}",
            f"--server-key-path={tmp_path / 'server.key'}",
            f"--server-cert-path={tmp_path / 'server.crt'}",
            f"--backup-device-path={loop_device}",
            f"--backup-mount-point={tmp_path / 'mnt'}",
            f"--backup-fstab-path={tmp_path / 'fake_fstab'}",
            "--format-backup-volume",
            "--skip-format-confirmation",
            "--documentation-manifest-path=modules/documentation/manifest.yaml",
            "--rh-manifest-path=modules/rh/manifest.yaml",
            f"--backup-storage-root={storage_root}",
            f"--backup-gpg-recipient-key-path={gpg_keypair['public_key_path']}",
            f"--db-name={db_params.name}",
            f"--db-user={db_params.user}",
            f"--db-password={db_params.password}",
            f"--db-host={db_params.host}",
            f"--db-port={db_params.port}",
        )

        assert User.objects.filter(username="admin_cli_test").exists()
        assert Module.objects.get(pk="rh").state == Module.State.ACTIVATED
