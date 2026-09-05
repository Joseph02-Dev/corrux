"""Tests de corrux-update (mode hors ligne) — TECH-014 (§19.2).

« Test d'intégration (bundle valide, altéré, signature invalide) »
(contrat explicite du ticket) — aucun mock : vrai périphérique loopback
(jamais un disque système réel), vrai paquet .deb minimal et sûr
(installe un seul fichier inoffensif sous /opt/corrux-test-marker/,
jamais un chemin système critique), vraie paire de clés GPG, vraie
installation dpkg et désinstallation en nettoyage. Démarche entièrement
vérifiée manuellement (script ad hoc) avant d'écrire le moindre test —
bundle valide et bundle avec checksum invalide tous deux confirmés
fonctionnels de bout en bout, y compris le refus total (paquet jamais
installé) avant d'écrire la moindre assertion.
"""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from core.audit.models import AuditLog
from core.identity.models import User
from ops.update_service import UpdateError, apply_offline_update
from packaging.signing import sign_bundle

TOOLS_AVAILABLE = all(
    shutil.which(tool) is not None
    for tool in ("losetup", "mkfs.ext4", "dpkg-deb", "dpkg", "gpg")
)
pytestmark = pytest.mark.skipif(
    not TOOLS_AVAILABLE, reason="losetup/mkfs.ext4/dpkg-deb/dpkg/gpg non disponibles"
)

TEST_PACKAGE_NAME = "corrux-test-pkg"
TEST_MARKER_PATH = Path("/opt/corrux-test-marker/installed.txt")


def _generate_gpg_key(home: Path, name: str, email: str) -> None:
    home.mkdir(mode=0o700, exist_ok=True)
    params_path = home / "params.txt"
    params_path.write_text(
        f"%no-protection\nKey-Type: RSA\nKey-Length: 2048\n"
        f"Name-Real: {name}\nName-Email: {email}\nExpire-Date: 0\n%commit\n"
    )
    subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--generate-key", str(params_path)],
        check=True, capture_output=True, text=True,
    )


@pytest.fixture
def release_key(tmp_path):
    home = tmp_path / "release_home"
    _generate_gpg_key(home, "CORRUX Release", "release@corrux.local")
    public_key_path = tmp_path / "trusted.asc"
    export = subprocess.run(
        ["gpg", "--homedir", str(home), "--export", "--armor", "release@corrux.local"],
        check=True, capture_output=True, text=True,
    )
    public_key_path.write_text(export.stdout)
    return {"gnupg_home": home, "public_key_path": public_key_path}


@pytest.fixture
def unrelated_key(tmp_path):
    home = tmp_path / "unrelated_home"
    _generate_gpg_key(home, "Unrelated Signer", "unrelated@evil.local")
    return {"gnupg_home": home}


@pytest.fixture
def test_deb_package(tmp_path):
    """Un vrai paquet .deb minimal et sûr — installe un unique fichier
    inoffensif sous /opt/corrux-test-marker/, jamais un chemin système
    critique. Désinstallé systématiquement en nettoyage, même en cas
    d'échec du test."""
    deb_root = tmp_path / "deb_build" / TEST_PACKAGE_NAME
    (deb_root / "DEBIAN").mkdir(parents=True)
    (deb_root / "opt" / "corrux-test-marker").mkdir(parents=True)
    (deb_root / "DEBIAN" / "control").write_text(
        f"Package: {TEST_PACKAGE_NAME}\nVersion: 1.0.0\nArchitecture: all\n"
        "Maintainer: CORRUX Test <test@corrux.local>\n"
        "Description: Paquet de test minimal pour TECH-014.\n"
    )
    (deb_root / "opt" / "corrux-test-marker" / "installed.txt").write_text("v1.0.0")

    deb_path = tmp_path / f"{TEST_PACKAGE_NAME}_1.0.0_all.deb"
    subprocess.run(
        ["dpkg-deb", "--build", str(deb_root), str(deb_path)],
        check=True, capture_output=True,
    )
    try:
        yield deb_path
    finally:
        subprocess.run(["dpkg", "-r", TEST_PACKAGE_NAME], capture_output=True, check=False)


def _build_update_media(
    tmp_path, deb_path, *, gnupg_home, checksum=None, sign=True,
):
    """Construit un périphérique loopback contenant un support de mise
    à jour complet (manifeste + signature + .deb), puis le détache —
    prêt à être remonté en lecture seule par le code testé, exactement
    comme un technicien insérerait une clé USB."""
    if checksum is None:
        checksum = hashlib.sha256(deb_path.read_bytes()).hexdigest()

    image_path = tmp_path / "media.img"
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={image_path}", "bs=1M", "count=20"],
        check=True, capture_output=True,
    )
    loop_device = subprocess.run(
        ["losetup", "-f", "--show", str(image_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["mkfs.ext4", "-F", loop_device], check=True, capture_output=True)

    write_mount = tmp_path / "write_mount"
    write_mount.mkdir(exist_ok=True)
    subprocess.run(["mount", loop_device, str(write_mount)], check=True, capture_output=True)

    shutil.copy(deb_path, write_mount / deb_path.name)
    manifest_path = write_mount / "update-manifest.yaml"
    manifest_path.write_text(
        f'version: "1.0.0"\n'
        f"packages:\n"
        f"  - filename: {deb_path.name}\n"
        f"    sha256: {checksum}\n"
        f"modules_impacted: []\n"
    )
    if sign:
        signature_path = write_mount / "update-manifest.yaml.sig"
        sign_bundle(manifest_path, signature_path, gnupg_home=gnupg_home)

    subprocess.run(["umount", str(write_mount)], check=True, capture_output=True)
    subprocess.run(["losetup", "-d", loop_device], check=True, capture_output=True)

    return str(image_path)


@pytest.fixture
def valid_media(tmp_path, test_deb_package, release_key):
    image_path = _build_update_media(
        tmp_path, test_deb_package, gnupg_home=release_key["gnupg_home"]
    )
    device_path = subprocess.run(
        ["losetup", "-f", "--show", image_path], capture_output=True, text=True, check=True
    ).stdout.strip()
    try:
        yield device_path
    finally:
        subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)


# --- A. Bundle valide — appliqué et journalisé -----------------------------------


@pytest.mark.django_db
class TestValidBundle:
    def test_valid_update_installs_the_package(self, tmp_path, valid_media, release_key):
        actor = User.objects.create(username="tech_update_valid", full_name="Technicien")

        apply_offline_update(
            media_device_path=valid_media, mount_point=tmp_path / "mnt",
            trusted_public_key_path=release_key["public_key_path"], actor=actor,
        )

        assert TEST_MARKER_PATH.read_text() == "v1.0.0"

    def test_valid_update_records_an_audit_event(self, tmp_path, valid_media, release_key):
        actor = User.objects.create(username="tech_update_audit", full_name="Technicien")

        apply_offline_update(
            media_device_path=valid_media, mount_point=tmp_path / "mnt",
            trusted_public_key_path=release_key["public_key_path"], actor=actor,
        )

        entry = AuditLog.objects.get(action="update.applied", actor_user=actor)
        assert entry.metadata["version"] == "1.0.0"
        assert "corrux-test-pkg_1.0.0_all.deb" in entry.metadata["packages_applied"]

    def test_valid_update_returns_a_complete_report(self, tmp_path, valid_media, release_key):
        report = apply_offline_update(
            media_device_path=valid_media, mount_point=tmp_path / "mnt",
            trusted_public_key_path=release_key["public_key_path"], actor=None,
        )

        assert report.version == "1.0.0"
        assert report.packages_applied == ("corrux-test-pkg_1.0.0_all.deb",)

    def test_media_is_mounted_read_only(self, tmp_path, valid_media, release_key):
        """§19.2 étape 1 : montage en lecture seule — vérifié
        directement sur la commande mount exécutée, pas supposé."""
        captured_commands = []
        real_run = subprocess.run

        def spy(command, *args, **kwargs):
            captured_commands.append(command)
            return real_run(command, *args, **kwargs)

        apply_offline_update(
            media_device_path=valid_media, mount_point=tmp_path / "mnt",
            trusted_public_key_path=release_key["public_key_path"], actor=None,
            runner=spy,
        )

        mount_commands = [cmd for cmd in captured_commands if cmd[0] == "mount"]
        assert len(mount_commands) == 1
        assert "-o" in mount_commands[0] and "ro" in mount_commands[0]


# --- B. Bundle altéré (checksum invalide) — refus total et atomique -----------


@pytest.mark.django_db
class TestTamperedBundle:
    def test_invalid_checksum_is_rejected(self, tmp_path, test_deb_package, release_key):
        wrong_checksum = "0" * 64
        image_path = _build_update_media(
            tmp_path, test_deb_package, gnupg_home=release_key["gnupg_home"],
            checksum=wrong_checksum,
        )
        device_path = subprocess.run(
            ["losetup", "-f", "--show", image_path], capture_output=True, text=True, check=True
        ).stdout.strip()

        try:
            with pytest.raises(UpdateError):
                apply_offline_update(
                    media_device_path=device_path, mount_point=tmp_path / "mnt",
                    trusted_public_key_path=release_key["public_key_path"], actor=None,
                )
        finally:
            subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)

    def test_invalid_checksum_installs_nothing(self, tmp_path, test_deb_package, release_key):
        """Critère d'acceptation explicite du ticket : « bundle altéré
        rejeté sans aucun paquet appliqué »."""
        wrong_checksum = "0" * 64
        image_path = _build_update_media(
            tmp_path, test_deb_package, gnupg_home=release_key["gnupg_home"],
            checksum=wrong_checksum,
        )
        device_path = subprocess.run(
            ["losetup", "-f", "--show", image_path], capture_output=True, text=True, check=True
        ).stdout.strip()

        try:
            with pytest.raises(UpdateError):
                apply_offline_update(
                    media_device_path=device_path, mount_point=tmp_path / "mnt",
                    trusted_public_key_path=release_key["public_key_path"], actor=None,
                )
        finally:
            subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)

        assert not TEST_MARKER_PATH.exists()

    def test_invalid_checksum_records_a_refusal_audit_event(
        self, tmp_path, test_deb_package, release_key
    ):
        wrong_checksum = "0" * 64
        image_path = _build_update_media(
            tmp_path, test_deb_package, gnupg_home=release_key["gnupg_home"],
            checksum=wrong_checksum,
        )
        device_path = subprocess.run(
            ["losetup", "-f", "--show", image_path], capture_output=True, text=True, check=True
        ).stdout.strip()
        actor = User.objects.create(username="tech_update_refuse", full_name="Technicien")

        try:
            with pytest.raises(UpdateError):
                apply_offline_update(
                    media_device_path=device_path, mount_point=tmp_path / "mnt",
                    trusted_public_key_path=release_key["public_key_path"], actor=actor,
                )
        finally:
            subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)

        entry = AuditLog.objects.get(action="update.refused", actor_user=actor)
        assert entry.metadata["reason"] == "checksum_invalide"


# --- C. Signature invalide (clé inconnue) — refus total et atomique -----------


@pytest.mark.django_db
class TestInvalidSignature:
    def test_bundle_signed_by_unknown_key_is_rejected(
        self, tmp_path, test_deb_package, release_key, unrelated_key
    ):
        image_path = _build_update_media(
            tmp_path, test_deb_package, gnupg_home=unrelated_key["gnupg_home"],
        )
        device_path = subprocess.run(
            ["losetup", "-f", "--show", image_path], capture_output=True, text=True, check=True
        ).stdout.strip()

        try:
            with pytest.raises(UpdateError):
                apply_offline_update(
                    media_device_path=device_path, mount_point=tmp_path / "mnt",
                    trusted_public_key_path=release_key["public_key_path"], actor=None,
                )
        finally:
            subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)

        assert not TEST_MARKER_PATH.exists()

    def test_unsigned_bundle_is_rejected(self, tmp_path, test_deb_package, release_key):
        image_path = _build_update_media(
            tmp_path, test_deb_package, gnupg_home=release_key["gnupg_home"], sign=False,
        )
        device_path = subprocess.run(
            ["losetup", "-f", "--show", image_path], capture_output=True, text=True, check=True
        ).stdout.strip()

        try:
            with pytest.raises(UpdateError):
                apply_offline_update(
                    media_device_path=device_path, mount_point=tmp_path / "mnt",
                    trusted_public_key_path=release_key["public_key_path"], actor=None,
                )
        finally:
            subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)

        assert not TEST_MARKER_PATH.exists()

    def test_invalid_signature_records_a_refusal_audit_event(
        self, tmp_path, test_deb_package, release_key, unrelated_key
    ):
        image_path = _build_update_media(
            tmp_path, test_deb_package, gnupg_home=unrelated_key["gnupg_home"],
        )
        device_path = subprocess.run(
            ["losetup", "-f", "--show", image_path], capture_output=True, text=True, check=True
        ).stdout.strip()
        actor = User.objects.create(username="tech_update_sig_refuse", full_name="Technicien")

        try:
            with pytest.raises(UpdateError):
                apply_offline_update(
                    media_device_path=device_path, mount_point=tmp_path / "mnt",
                    trusted_public_key_path=release_key["public_key_path"], actor=actor,
                )
        finally:
            subprocess.run(["losetup", "-d", device_path], capture_output=True, check=False)

        entry = AuditLog.objects.get(action="update.refused", actor_user=actor)
        assert entry.metadata["reason"] == "signature_invalide"


# --- D. Commande CLI — gap trouvé lors de l'audit Phase 1 de TECH-044 ----------
# apply_offline_update() n'avait jamais été enveloppée dans une
# commande manage.py invocable — seule la fonction de service
# existait, aucun point d'entrée technicien. Corrigé à l'occasion de
# TECH-044 (nécessaire pour documenter honnêtement une procédure
# réellement exécutable).


@pytest.mark.django_db
class TestApplyOfflineUpdateCommand:
    def test_command_is_discoverable(self):
        from django.core.management import get_commands

        assert get_commands().get("apply_offline_update") == "ops"

    def test_command_applies_a_valid_update_end_to_end(self, tmp_path, valid_media, release_key):
        from django.core.management import call_command

        os.environ["CORRUX_UPDATE_MOUNT_POINT"] = str(tmp_path / "mnt")
        os.environ["CORRUX_UPDATE_TRUSTED_KEY_PATH"] = str(release_key["public_key_path"])
        try:
            call_command("apply_offline_update", "--device", valid_media)
        finally:
            del os.environ["CORRUX_UPDATE_MOUNT_POINT"]
            del os.environ["CORRUX_UPDATE_TRUSTED_KEY_PATH"]

        assert TEST_MARKER_PATH.read_text() == "v1.0.0"
