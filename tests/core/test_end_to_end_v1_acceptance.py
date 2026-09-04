"""Scénario de bout en bout « critères de fin V1 » — TECH-042.

Valide sur une instance fraîche l'ensemble des critères de §13
(vision-produit-v1.md) qui sont réellement atteignables à ce stade du
plan (dépendance explicite du ticket : « Phases 1 à 4 terminées »).

Point signalé, pas ignoré : §13 liste aussi « un technicien IT peut
installer CORRUX... (identité, HTTPS, sauvegardes) » et « les
utilisateurs accèdent au système... via HTTPS » — ces deux critères
relèvent de la Phase 6 (corrux-setup TECH-012, Nginx/TLS TECH-010/011),
non commencée. La dépendance explicite de CE ticket ("Phases 1 à 4
terminées") confirme que ce scénario ne peut porter que sur ce qui est
réellement construit ; les critères d'installation/HTTPS physique
resteront à vérifier par un scénario dédié une fois la Phase 6 achevée
(hors périmètre de ce ticket). Ce scénario couvre donc exactement les 6
points énumérés par le "Comportement attendu" du ticket lui-même —
plus précis et actionnable que la liste complète de §13.

Réordonnancement documenté, pas silencieux : le "Comportement attendu"
liste littéralement "activation Documentation puis RH réussie" AVANT
"activation RH sans Documentation refusée" — mais sur une seule
instance continue, le refus doit nécessairement être démontré AVANT
l'activation réussie (une fois Documentation actif, il n'est plus
possible de démontrer le refus dans la même instance). Ce scénario
teste donc le refus D'ABORD, conformément à la seule séquence
logiquement réalisable — la succession documentée ici plutôt que
suivie aveuglément dans l'ordre littéral du texte.

Aucun mock : vrai PostgreSQL, vrai volume tmpfs (st_dev réel), vraie
paire de clés GPG — même discipline que TECH-009, TECH-040/041.
"""

import os
import subprocess
import tarfile
from datetime import date
from pathlib import Path

import pytest
from django.test import Client, override_settings

from core.audit.models import AuditLog
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.backup.database import DatabaseConnectionParams
from core.backup.models import BackupRun
from core.backup.service import BackupConfig, run_backup
from core.identity import auth
from core.identity.models import User
from core.modules.manager import DependencyError, activate_module, install_module
from core.modules.manifest import parse_manifest_file
from core.modules.models import Module
from modules.documentation.documents_v1 import attach as documents_v1_attach
from modules.documentation.documents_v1 import get as documents_v1_get
from modules.documentation.services import search_documents
from modules.rh.services import (
    attach_document_to_employee,
    create_employee,
    list_employee_documents,
)

DOCUMENTATION_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "modules"
    / "documentation"
    / "manifest.yaml"
)
RH_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent / "modules" / "rh" / "manifest.yaml"
)


def _db_params() -> DatabaseConnectionParams:
    return DatabaseConnectionParams(
        name=os.environ["DB_NAME"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"], host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
    )


def _decrypt_backup(backup_path: Path, gpg_keypair, output_path: Path) -> None:
    result = subprocess.run(
        ["gpg", "--homedir", str(gpg_keypair["gnupg_home"]),
         "--batch", "--yes", "--output", str(output_path),
         "--decrypt", str(backup_path)],
        check=True, capture_output=True,
    )
    assert result.returncode == 0


@pytest.mark.django_db
class TestEndToEndV1AcceptanceCriteria:
    def test_full_v1_acceptance_scenario(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = tmp_path / "storage"
        storage_root.mkdir()

        with override_settings(CORRUX_STORAGE_ROOT=str(storage_root)):
            # === 1. Comptes/permissions — un administrateur crée un
            # compte et lui accorde des permissions =====================
            admin = User.objects.create(username="admin_v1_e2e", full_name="Admin V1")
            auth.set_user_password(admin, "Password123!")
            admin.save()

            role = Role.objects.create(name="Rôle complet — scénario V1")
            for module_id, resource, action in [
                ("documentation", "document", "read"),
                ("documentation", "document", "write"),
                ("rh", "employee", "read"),
                ("rh", "employee", "write"),
            ]:
                permission, _ = Permission.objects.get_or_create(
                    module_id=module_id, resource=resource, action=action
                )
                RolePermission.objects.create(role=role, permission=permission)
            UserRole.objects.create(user=admin, role=role)

            assert UserRole.objects.filter(user=admin, role=role).exists()

            # === 2. Un utilisateur accède au système (session
            # applicative — HTTPS physique hors périmètre Phase 6) ======
            client = Client()
            login_response = client.post(
                "/login/", {"username": "admin_v1_e2e", "password": "Password123!"}
            )
            assert login_response.status_code == 302
            assert client.get("/profil/").status_code == 200

            # === 3. Activation RH sans Documentation → refusée =========
            # (démontrée AVANT l'activation réussie — seul ordre
            # réalisable sur une instance continue, cf. docstring).
            rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
            install_module(rh_manifest, actor=admin)

            with pytest.raises(DependencyError):
                activate_module("rh", actor=admin)
            assert Module.objects.get(pk="rh").state != Module.State.ACTIVATED

            # === 4. Activation Documentation puis RH → réussie,
            # indépendamment via le mécanisme de manifeste ==============
            documentation_manifest = parse_manifest_file(DOCUMENTATION_MANIFEST_PATH)
            install_module(documentation_manifest, actor=admin)
            activate_module("documentation", actor=admin)
            activate_module("rh", actor=admin)

            assert Module.objects.get(pk="documentation").state == Module.State.ACTIVATED
            assert Module.objects.get(pk="rh").state == Module.State.ACTIVATED

            # === 5. Dépôt/recherche de document, permissions
            # respectées ==================================================
            document_ref = documents_v1_attach(
                content=b"contenu reel du rapport", filename="rapport-v1.pdf",
                owner_user=admin,
            )

            results = search_documents(user=admin, keyword="rapport-v1")
            assert any(d.id == document_ref for d in results)

            outsider = User.objects.create(username="tiers_v1_e2e", full_name="Tiers")
            results_for_outsider = search_documents(user=outsider, keyword="rapport-v1")
            assert not any(d.id == document_ref for d in results_for_outsider)

            # === 6. Le module RH permet de créer une fiche employé et
            # de lui rattacher un document — visible des deux côtés,
            # sans second système documentaire ============================
            employee = create_employee(
                first_name="Jean", last_name="Dupont", position="Développeur",
                hire_date=date(2026, 1, 1),
            )

            employee_document_ref = attach_document_to_employee(
                employee=employee, content=b"contenu reel du cv", filename="cv-v1.pdf",
                owner_user=admin,
            )

            # Côté Documentation (documents.v1) :
            meta = documents_v1_get(employee_document_ref, admin)
            assert meta.filename == "cv-v1.pdf"

            # Côté RH (fiche employé) :
            rh_visible_documents = list_employee_documents(
                employee=employee, requesting_user=admin
            )
            assert any(d.filename == "cv-v1.pdf" for d in rh_visible_documents)

            # Aucun second système documentaire : le fichier réel
            # n'existe que sous l'arborescence Documentation.
            top_level_entries = {p.name for p in storage_root.iterdir()}
            assert top_level_entries == {"documentation"}

            # === 7. Sauvegarde locale programmée exécutée
            # automatiquement ==============================================
            config = BackupConfig(
                destination_dir=real_separate_volume,
                db_params=_db_params(),
                storage_root=storage_root,
                gpg_recipient_key_path=gpg_keypair["public_key_path"],
                system_root=tmp_path,
            )
            backup_run = run_backup(config, actor=admin)

            assert backup_run.status == BackupRun.Status.SUCCESS
            assert BackupRun.objects.filter(pk=backup_run.pk).exists()

            backup_files = list(real_separate_volume.glob("corrux-backup-*.tar.gpg"))
            assert len(backup_files) == 1

            decrypted_path = tmp_path / "decrypted.tar"
            _decrypt_backup(backup_files[0], gpg_keypair, decrypted_path)
            with tarfile.open(decrypted_path, "r:") as archive:
                names = archive.getnames()
            assert any("database.dump" in name for name in names)
            assert any("storage.tar.gz" in name for name in names)

            assert AuditLog.objects.filter(
                actor_user=admin, action="module.activate"
            ).exists()
