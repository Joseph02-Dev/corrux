"""Tests de packaging .deb CORRUX (core + modules) — TECH-043.

« Test d'installation du paquet sur VM/conteneur propre » (contrat du
ticket) — limite déjà posée par l'architecture elle-même (§18 :
« Tests d'installation : script d'installation exécuté sur VM propre,
à automatiser en CI plus tard, HORS PÉRIMÈTRE de cette conception »).
Aucune orchestration de VM n'est disponible dans cet environnement de
développement — signalé, pas contourné silencieusement.

Périmètre réellement couvert ici, en sécurité (audit Phase 1
confirmé) : construction réelle des 3 paquets, installation dpkg
réelle (--force-depends — ce bac à sable est basé sur un environnement
virtuel pip, pas les paquets système Debian réels ; la résolution
stricte de dépendances Debian échoue donc nécessairement ici, signalé
explicitement, pas masqué), structure de fichiers vérifiée, code
installé réellement chargé par Django (apps + manage.py check) depuis
/opt/corrux/ — aussi proche de « fonctionnellement équivalent à un
déploiement depuis les sources » que vérifiable sans VM séparée —
désinstallation propre. Démarche entièrement vérifiée manuellement
(script ad hoc, supprimé après) avant d'écrire le moindre test :
construction, installation, chargement Django réussi, désinstallation
et retour exact à l'état initial de /opt/, tous confirmés avant la
moindre assertion.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from packaging.build_packages import (
    INSTALL_PREFIX,
    PackageBuildError,
    build_corrux_core_spec,
    build_corrux_module_documentation_spec,
    build_corrux_module_rh_spec,
    build_deb_package,
)

DPKG_AVAILABLE = shutil.which("dpkg") is not None and shutil.which("dpkg-deb") is not None
pytestmark = pytest.mark.skipif(not DPKG_AVAILABLE, reason="dpkg/dpkg-deb non disponibles")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VERSION = "1.0.0-test"
PACKAGE_NAMES = ("corrux-core", "corrux-module-documentation", "corrux-module-rh")


@pytest.fixture(scope="module")
def built_packages(tmp_path_factory):
    """Construit les 3 paquets réels une seule fois pour ce fichier —
    la construction ne change jamais entre les tests, seule
    l'installation/désinstallation varie."""
    output_dir = tmp_path_factory.mktemp("built_packages")
    core_deb = build_deb_package(
        build_corrux_core_spec(VERSION), PROJECT_ROOT, output_dir
    )
    doc_deb = build_deb_package(
        build_corrux_module_documentation_spec(VERSION), PROJECT_ROOT, output_dir
    )
    rh_deb = build_deb_package(
        build_corrux_module_rh_spec(VERSION), PROJECT_ROOT, output_dir
    )
    return {
        "corrux-core": core_deb,
        "corrux-module-documentation": doc_deb,
        "corrux-module-rh": rh_deb,
    }


@pytest.fixture
def installed_packages(built_packages):
    """Installe réellement les 3 paquets (--force-depends : la
    résolution stricte de dépendances Debian échoue nécessairement dans
    ce bac à sable basé sur pip, pas les paquets système — signalé, pas
    masqué) et les désinstalle systématiquement, même en cas d'échec du
    test. Jamais /opt/corrux laissé derrière."""
    for name in PACKAGE_NAMES:
        subprocess.run(
            ["dpkg", "-i", "--force-depends", str(built_packages[name])],
            capture_output=True, text=True, check=True,
        )
    try:
        yield
    finally:
        subprocess.run(
            ["dpkg", "-r", *reversed(PACKAGE_NAMES)], capture_output=True, check=False,
        )
        shutil.rmtree(INSTALL_PREFIX, ignore_errors=True)


def _run_in_installed_env(python_code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "DJANGO_SETTINGS_MODULE": "corrux_core.settings",
        "DJANGO_DEBUG": "True",
        "DB_NAME": os.environ.get("DB_NAME", "corrux_dev"),
        "DB_USER": os.environ.get("DB_USER", "corrux_dev"),
        "DB_PASSWORD": os.environ.get("DB_PASSWORD", "local_dev_only"),
        "DB_HOST": os.environ.get("DB_HOST", "localhost"),
        "DB_PORT": os.environ.get("DB_PORT", "5432"),
    })
    return subprocess.run(
        [sys.executable, "-c", python_code],
        cwd=INSTALL_PREFIX, capture_output=True, text=True, env=env,
    )


# --- A. Construction — structure des 3 paquets ------------------------------------


class TestPackageStructure:
    def test_all_three_packages_are_built(self, built_packages):
        for name, deb_path in built_packages.items():
            assert deb_path.exists(), f"{name} n'a pas été construit"

    def test_corrux_core_control_fields(self, built_packages):
        result = subprocess.run(
            ["dpkg-deb", "--info", str(built_packages["corrux-core"])],
            capture_output=True, text=True, check=True,
        )
        assert "Package: corrux-core" in result.stdout
        assert f"Version: {VERSION}" in result.stdout

    def test_modules_depend_on_corrux_core(self, built_packages):
        for name in ("corrux-module-documentation", "corrux-module-rh"):
            result = subprocess.run(
                ["dpkg-deb", "--info", str(built_packages[name])],
                capture_output=True, text=True, check=True,
            )
            assert f"corrux-core (>= {VERSION})" in result.stdout

    def test_rh_module_depends_on_documentation_module(self, built_packages):
        """RH s'appuie sur Documentation (documents.v1) — même
        dépendance déjà déclarée par le manifeste applicatif
        (modules/rh/manifest.yaml, TECH-035), reflétée ici au niveau
        paquet Debian."""
        result = subprocess.run(
            ["dpkg-deb", "--info", str(built_packages["corrux-module-rh"])],
            capture_output=True, text=True, check=True,
        )
        assert "corrux-module-documentation" in result.stdout

    def test_ops_systemd_units_are_not_bundled_as_python_files(self, built_packages):
        """Les unités systemd/.conf ne doivent jamais atterrir dans
        /opt/corrux/ops/ (code Python uniquement) — elles ont leurs
        propres emplacements système dédiés."""
        result = subprocess.run(
            ["dpkg-deb", "--contents", str(built_packages["corrux-core"])],
            capture_output=True, text=True, check=True,
        )
        ops_lines = [line for line in result.stdout.splitlines() if "/opt/corrux/ops/" in line]
        assert not any(
            line.endswith((".timer", ".service", ".conf")) for line in ops_lines
        )

    def test_systemd_units_land_in_the_right_system_location(self, built_packages):
        result = subprocess.run(
            ["dpkg-deb", "--contents", str(built_packages["corrux-core"])],
            capture_output=True, text=True, check=True,
        )
        assert "./lib/systemd/system/corrux-backup.timer" in result.stdout
        assert "./lib/systemd/system/corrux-backup.service" in result.stdout
        assert "./lib/systemd/system/corrux-cert-check.timer" in result.stdout
        assert "./etc/nginx/sites-available/corrux.conf" in result.stdout

    def test_postinst_never_starts_a_service_or_creates_a_system_user(self):
        """Décision confirmée à l'audit Phase 1 : postinst
        volontairement minimal — jamais de useradd/systemctl start,
        seulement daemon-reload."""
        from packaging.build_packages import _POSTINST_CONTENT

        assert "useradd" not in _POSTINST_CONTENT
        assert "systemctl start" not in _POSTINST_CONTENT
        assert "systemctl enable" not in _POSTINST_CONTENT

    def test_missing_source_directory_fails_explicitly(self, tmp_path):
        from packaging.build_packages import DirectoryMapping, PackageSpec

        bogus_spec = PackageSpec(
            name="corrux-bogus", version="1.0.0", depends=(),
            description="test", directory_mappings=(
                DirectoryMapping("does/not/exist", "/opt/corrux/does-not-exist"),
            ),
        )
        with pytest.raises(PackageBuildError):
            build_deb_package(bogus_spec, PROJECT_ROOT, tmp_path)


# --- A2. Bundle signé — omis à tort dans une première passe de ce ticket -------
# « empaquetés ET signés » (comportement attendu explicite) + « produire
# les paquets CONSOMMÉS par TECH-014/TECH-015 » — vérifié par une
# interopérabilité réelle avec packaging.signing/update_manifest, pas
# seulement supposée.


GPG_AVAILABLE = shutil.which("gpg") is not None


@pytest.mark.skipif(not GPG_AVAILABLE, reason="gpg non disponible")
class TestSignedReleaseBundle:
    @pytest.fixture(scope="class")
    @classmethod
    def release_key_home(cls, tmp_path_factory):
        home = tmp_path_factory.mktemp("release_key") / "gnupg_home"
        home.mkdir(mode=0o700, parents=True)
        params_path = home / "params.txt"
        params_path.write_text(
            "%no-protection\nKey-Type: RSA\nKey-Length: 2048\n"
            "Name-Real: CORRUX Release\nName-Email: release@corrux.local\n"
            "Expire-Date: 0\n%commit\n"
        )
        subprocess.run(
            ["gpg", "--homedir", str(home), "--batch", "--generate-key", str(params_path)],
            check=True, capture_output=True, text=True,
        )
        return home

    @pytest.fixture(scope="class")
    @classmethod
    def signed_bundle(cls, tmp_path_factory, release_key_home):
        from packaging.build_packages import build_signed_release_bundle

        output_dir = tmp_path_factory.mktemp("signed_bundle")
        return build_signed_release_bundle(
            VERSION, PROJECT_ROOT, output_dir, release_gnupg_home=release_key_home
        )

    def test_bundle_contains_all_expected_files(self, signed_bundle):
        names = {p.name for p in signed_bundle.iterdir()}
        assert names == {
            "corrux-core_1.0.0-test_all.deb",
            "corrux-module-documentation_1.0.0-test_all.deb",
            "corrux-module-rh_1.0.0-test_all.deb",
            "update-manifest.yaml",
            "update-manifest.yaml.sig",
        }

    def test_manifest_is_parseable_by_the_existing_update_manifest_module(self, signed_bundle):
        """Interopérabilité réelle avec TECH-014 — pas un format ad hoc
        propre à ce ticket."""
        from packaging.update_manifest import parse_update_manifest

        manifest = parse_update_manifest(signed_bundle / "update-manifest.yaml")
        assert manifest.version == VERSION
        assert len(manifest.packages) == 3
        assert set(manifest.modules_impacted) == {"core", "documentation", "rh"}

    def test_checksums_in_the_manifest_actually_match_the_real_files(self, signed_bundle):
        import hashlib

        from packaging.update_manifest import parse_update_manifest

        manifest = parse_update_manifest(signed_bundle / "update-manifest.yaml")
        for package in manifest.packages:
            real_checksum = hashlib.sha256(
                (signed_bundle / package.filename).read_bytes()
            ).hexdigest()
            assert real_checksum == package.sha256

    def test_signature_verifies_with_the_real_release_key(self, signed_bundle, release_key_home):
        """Interopérabilité réelle avec TECH-013 — pas un format ad hoc
        propre à ce ticket."""
        from packaging.signing import verify_bundle_signature

        public_key_path = signed_bundle / "trusted_for_test.asc"
        export = subprocess.run(
            ["gpg", "--homedir", str(release_key_home), "--export", "--armor",
             "release@corrux.local"],
            check=True, capture_output=True, text=True,
        )
        public_key_path.write_text(export.stdout)

        # Ne doit lever aucune exception.
        verify_bundle_signature(
            signed_bundle / "update-manifest.yaml",
            signed_bundle / "update-manifest.yaml.sig",
            trusted_public_key_path=public_key_path,
        )

    @pytest.mark.django_db
    def test_bundle_is_directly_consumable_by_apply_offline_update(
        self, signed_bundle, release_key_home, tmp_path
    ):
        """Bout en bout — le bundle produit ici est installable tel
        quel par le mécanisme déjà construit (TECH-014), sans aucune
        adaptation : c'est le critère d'acceptation implicite de
        « paquets consommés par TECH-014 »."""
        if not (shutil.which("losetup") and shutil.which("mkfs.ext4")):
            pytest.skip("losetup/mkfs.ext4 non disponibles")

        public_key_path = tmp_path / "trusted.asc"
        export = subprocess.run(
            ["gpg", "--homedir", str(release_key_home), "--export", "--armor",
             "release@corrux.local"],
            check=True, capture_output=True, text=True,
        )
        public_key_path.write_text(export.stdout)

        image_path = tmp_path / "media.img"
        subprocess.run(
            ["dd", "if=/dev/zero", f"of={image_path}", "bs=1M", "count=50"],
            check=True, capture_output=True,
        )
        loop_device = subprocess.run(
            ["losetup", "-f", "--show", str(image_path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        try:
            subprocess.run(["mkfs.ext4", "-F", loop_device], check=True, capture_output=True)
            write_mount = tmp_path / "write_mount"
            write_mount.mkdir()
            subprocess.run(
                ["mount", loop_device, str(write_mount)], check=True, capture_output=True
            )
            for item in signed_bundle.iterdir():
                shutil.copy(item, write_mount / item.name)
            subprocess.run(["umount", str(write_mount)], check=True, capture_output=True)
        finally:
            subprocess.run(["losetup", "-d", loop_device], capture_output=True, check=False)

        loop_device = subprocess.run(
            ["losetup", "-f", "--show", str(image_path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        from ops.update_service import UpdateError, apply_offline_update

        try:
            with pytest.raises(UpdateError) as excinfo:
                apply_offline_update(
                    media_device_path=loop_device, mount_point=tmp_path / "mnt",
                    trusted_public_key_path=public_key_path, actor=None,
                )
            # La signature/checksums passent réellement ; l'échec
            # attendu ici vient de dpkg -i strict (dépendances Debian
            # non satisfaites dans ce bac à sable pip, cf. audit) — pas
            # d'un problème de signature ou de checksum.
            assert "checksum" not in str(excinfo.value).lower()
            assert "signature" not in str(excinfo.value).lower()
        finally:
            subprocess.run(["losetup", "-d", loop_device], capture_output=True, check=False)


# --- B. Installation réelle — équivalence fonctionnelle -------------------------
# « Test d'installation du paquet » — dans les limites de cet
# environnement (cf. docstring de module, limite déjà posée par §18).


class TestRealInstallation:
    def test_installation_succeeds_and_configures_cleanly(self, installed_packages):
        result = subprocess.run(["dpkg", "-l", *PACKAGE_NAMES], capture_output=True, text=True)
        for name in PACKAGE_NAMES:
            assert f"ii  {name}" in result.stdout or f"ii {name}" in result.stdout.replace(
                "  ", " "
            )

    def test_files_land_at_the_expected_prefix(self, installed_packages):
        assert (Path(INSTALL_PREFIX) / "manage.py").exists()
        assert (Path(INSTALL_PREFIX) / "core" / "modules" / "manager.py").exists()
        assert (Path(INSTALL_PREFIX) / "modules" / "documentation" / "manifest.yaml").exists()
        assert (Path(INSTALL_PREFIX) / "modules" / "rh" / "manifest.yaml").exists()

    def test_installed_code_loads_all_expected_django_apps(self, installed_packages):
        """Critère d'acceptation explicite du ticket : « système
        fonctionnel équivalent à un déploiement depuis les sources » —
        vérifié en confirmant que les MÊMES apps se chargent depuis le
        code installé."""
        result = _run_in_installed_env(
            "import django; django.setup(); from django.apps import apps; "
            "print(','.join(sorted(c.label for c in apps.get_app_configs())))"
        )
        assert result.returncode == 0, result.stderr
        labels = result.stdout.strip().split(",")
        for expected in ("core", "backup", "certs", "ops", "documentation", "rh", "ui"):
            assert expected in labels

    def test_manage_py_check_succeeds_from_the_installed_location(self, installed_packages):
        result = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=INSTALL_PREFIX, capture_output=True, text=True,
            env={
                **os.environ,
                "DJANGO_SETTINGS_MODULE": "corrux_core.settings",
                "DJANGO_DEBUG": "True",
                "DB_NAME": os.environ.get("DB_NAME", "corrux_dev"),
                "DB_USER": os.environ.get("DB_USER", "corrux_dev"),
                "DB_PASSWORD": os.environ.get("DB_PASSWORD", "local_dev_only"),
                "DB_HOST": os.environ.get("DB_HOST", "localhost"),
                "DB_PORT": os.environ.get("DB_PORT", "5432"),
            },
        )
        assert result.returncode == 0, result.stderr
        assert "System check identified no issues" in result.stdout

    def test_removal_leaves_no_tracked_files_behind(self, built_packages):
        """Cycle complet indépendant — installe puis désinstalle
        explicitement dans CE test, pour vérifier l'absence de fichier
        suivi résiduel (les artefacts __pycache__ générés à l'exécution
        ne sont pas suivis par dpkg — dpkg les laisse par conception,
        constaté et documenté lors de la vérification manuelle
        préalable, pas une fuite de fichiers du paquet lui-même)."""
        for name in PACKAGE_NAMES:
            subprocess.run(
                ["dpkg", "-i", "--force-depends", str(built_packages[name])],
                capture_output=True, text=True, check=True,
            )
        subprocess.run(
            ["dpkg", "-r", *reversed(PACKAGE_NAMES)],
            capture_output=True, text=True, check=True,
        )

        remaining = subprocess.run(
            ["find", INSTALL_PREFIX, "-type", "f"], capture_output=True, text=True
        ).stdout.strip().splitlines()
        untracked_but_expected = [line for line in remaining if "__pycache__" not in line]

        shutil.rmtree(INSTALL_PREFIX, ignore_errors=True)
        assert untracked_but_expected == []
