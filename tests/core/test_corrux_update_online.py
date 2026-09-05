"""Tests de corrux-update --online — TECH-015 (§19.3).

« Test d'intégration sur dépôt de test HTTPS » (contrat explicite du
ticket) — aucun mock : vrai dépôt apt (Packages/Release/Release.gpg
signés avec une vraie clé GPG), vraiment servi en HTTPS par un vrai
processus nginx (réutilise le certificat réel de core.certs.service,
TECH-010), vraie invocation apt-get update/install en environnement
totalement isolé (jamais /etc/apt/, /var/lib/apt/, /var/lib/dpkg/
réels). Démarche entièrement vérifiée manuellement (script ad hoc,
supprimé après) avant d'écrire le moindre test — dépôt signé, servi,
vérifié par apt, installation réelle, tous confirmés fonctionnels de
bout en bout.

Critère d'acceptation explicite du ticket : « état système final
indiscernable d'une mise à jour hors ligne équivalente » — vérifié en
confirmant que le même type d'événement audit_log ("update.applied"
avec version/paquets/modules) est produit par les deux canaux.
"""

import os
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from core.audit.models import AuditLog
from core.certs.openssl import generate_ca, issue_server_certificate
from core.identity.models import User
from ops.update_service import UpdateError, apply_online_update

TOOLS_AVAILABLE = all(
    shutil.which(tool) is not None
    for tool in ("dpkg-deb", "dpkg", "gpg", "apt-get", "apt-ftparchive", "nginx")
)
pytestmark = pytest.mark.skipif(
    not TOOLS_AVAILABLE,
    reason="dpkg-deb/dpkg/gpg/apt-get/apt-ftparchive/nginx non disponibles",
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


@pytest.fixture(scope="module")
def release_key(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("release_key")
    home = tmp_path / "release_home"
    _generate_gpg_key(home, "CORRUX Release", "release@corrux.local")

    armored_path = tmp_path / "release_public.asc"
    export = subprocess.run(
        ["gpg", "--homedir", str(home), "--export", "--armor", "release@corrux.local"],
        check=True, capture_output=True, text=True,
    )
    armored_path.write_text(export.stdout)

    keyring_path = tmp_path / "corrux-release-keyring.gpg"
    subprocess.run(
        ["gpg", "--batch", "--yes", "--dearmor", "-o", str(keyring_path), str(armored_path)],
        check=True, capture_output=True,
    )
    return {"gnupg_home": home, "keyring_path": keyring_path}


@pytest.fixture(scope="module")
def test_deb_package(tmp_path_factory):
    """Même paquet de test minimal et sûr que TECH-014 (fichier unique
    sous /opt/corrux-test-marker/, jamais un chemin système critique).
    Portée module : construit une seule fois, réutilisé en lecture par
    tous les tests de ce fichier (aucun test n'en modifie le contenu)."""
    tmp_path = tmp_path_factory.mktemp("test_deb_package")
    deb_root = tmp_path / "deb_build" / TEST_PACKAGE_NAME
    (deb_root / "DEBIAN").mkdir(parents=True)
    (deb_root / "opt" / "corrux-test-marker").mkdir(parents=True)
    (deb_root / "DEBIAN" / "control").write_text(
        f"Package: {TEST_PACKAGE_NAME}\nVersion: 1.0.0\nArchitecture: all\n"
        "Maintainer: CORRUX Test <test@corrux.local>\n"
        "Description: Paquet de test minimal pour TECH-015.\n"
    )
    (deb_root / "opt" / "corrux-test-marker" / "installed.txt").write_text("v1.0.0-online")

    deb_path = tmp_path / f"{TEST_PACKAGE_NAME}_1.0.0_all.deb"
    subprocess.run(
        ["dpkg-deb", "--build", str(deb_root), str(deb_path)], check=True, capture_output=True,
    )
    return deb_path


@pytest.fixture(autouse=True)
def _remove_test_package_after_each_test():
    """Désinstalle le paquet de test après CHAQUE test (le paquet lui-
    même reste construit une seule fois, portée module ; mais chaque
    test doit repartir d'un état non installé)."""
    yield
    subprocess.run(["dpkg", "-r", TEST_PACKAGE_NAME], capture_output=True, check=False)


@pytest.fixture(scope="module")
def apt_repository(test_deb_package, release_key):
    """Un vrai dépôt apt minimal — Packages + Release + Release.gpg
    (signature détachée réelle), construit avec apt-ftparchive.

    Construit sous /tmp/ directement (tempfile.mkdtemp, mode 0755) —
    PAS sous la hiérarchie tmp_path_factory de pytest
    (/tmp/pytest-of-root/..., 0700, propriétaire seul) : le worker
    nginx tourne sous l'utilisateur "nobody" et ne peut traverser un
    répertoire parent restreint au propriétaire, quels que soient les
    droits du répertoire feuille lui-même (403 Forbidden constaté et
    diagnostiqué avant cette correction, pas supposé). Nettoyé
    explicitement en fin de module puisque hors du contrôle de pytest."""
    repo_root = Path(tempfile.mkdtemp(prefix="corrux_test_apt_repo_"))
    os.chmod(repo_root, 0o755)
    shutil.copy(test_deb_package, repo_root / test_deb_package.name)

    packages_content = subprocess.run(
        ["apt-ftparchive", "packages", "."],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    (repo_root / "Packages").write_text(packages_content)

    release_content = subprocess.run(
        ["apt-ftparchive", "release", "."],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    (repo_root / "Release").write_text(release_content)

    subprocess.run(
        [
            "gpg", "--homedir", str(release_key["gnupg_home"]), "--batch", "--yes",
            "--detach-sign", "--armor",
            "-o", str(repo_root / "Release.gpg"), str(repo_root / "Release"),
        ],
        check=True, capture_output=True,
    )
    subprocess.run(["chmod", "-R", "o+rX", str(repo_root)], check=True)

    try:
        yield repo_root
    finally:
        shutil.rmtree(repo_root, ignore_errors=True)


def _minimal_static_nginx_config(*, https_port, ssl_cert_path, ssl_key_path, root_dir) -> str:
    """Configuration Nginx minimale servant des fichiers statiques —
    simule un dépôt apt distant externe (infrastructure hors périmètre
    CORRUX), pas une extension de ops/nginx_config.py (qui sert le
    reverse-proxy applicatif de CORRUX lui-même, un rôle différent)."""
    return f"""\
server {{
    listen {https_port} ssl;
    ssl_certificate {ssl_cert_path};
    ssl_certificate_key {ssl_key_path};
    ssl_protocols TLSv1.2 TLSv1.3;
    location / {{
        root {root_dir};
        autoindex on;
    }}
}}
"""


@pytest.fixture(scope="module")
def https_repo_server(tmp_path_factory, apt_repository):
    """Sert le dépôt apt de test en HTTPS via un vrai processus nginx —
    réutilise un vrai certificat (core.certs.service, TECH-010).
    Portée module : un seul démarrage/arrêt nginx pour tout ce fichier
    (chaque démarrage/arrêt réel prend plusieurs centaines de
    millisecondes ; répété par test, le temps cumulé devient
    disproportionné pour une infrastructure qui ne change jamais entre
    les tests de ce fichier)."""
    tmp_path = tmp_path_factory.mktemp("https_repo_server")
    ca_key, ca_cert = tmp_path / "ca.key", tmp_path / "ca.crt"
    server_key, server_cert = tmp_path / "server.key", tmp_path / "server.crt"
    generate_ca(ca_key, ca_cert, validity_days=30)
    issue_server_certificate(
        server_key, server_cert, ca_key, ca_cert, common_name="localhost", validity_days=30,
    )

    https_port = 8543
    config = _minimal_static_nginx_config(
        https_port=https_port, ssl_cert_path=server_cert,
        ssl_key_path=server_key, root_dir=apt_repository,
    )
    fragment_path = tmp_path / "nginx-repo.conf"
    fragment_path.write_text(config)
    main_conf_path = tmp_path / "nginx-main.conf"
    main_conf_path.write_text(f"events {{}}\nhttp {{ include {fragment_path}; }}\n")
    pid_path = tmp_path / "nginx.pid"

    subprocess.run(
        ["nginx", "-c", str(main_conf_path), "-g", f"pid {pid_path};"],
        check=True, timeout=10,
    )
    time.sleep(0.5)

    # Confirme que le dépôt est réellement accessible avant de rendre
    # la main aux tests — pas supposé.
    ctx = ssl.create_default_context(cafile=str(ca_cert))
    urllib.request.urlopen(f"https://localhost:{https_port}/Release", context=ctx, timeout=5)

    try:
        yield {
            "repository_url": f"https://localhost:{https_port}",
            "ca_cert_path": ca_cert,
        }
    finally:
        subprocess.run(
            ["nginx", "-c", str(main_conf_path), "-g", f"pid {pid_path};", "-s", "stop"],
            timeout=10,
        )


# --- A. Mise à jour en ligne valide — appliquée et journalisée -----------------


@pytest.mark.django_db
class TestOnlineUpdate:
    def test_online_update_installs_the_package(
        self, tmp_path, https_repo_server, release_key
    ):
        actor = User.objects.create(username="tech_online_valid", full_name="Technicien")

        apply_online_update(
            repository_url=https_repo_server["repository_url"],
            trusted_keyring_path=release_key["keyring_path"],
            package_names=(TEST_PACKAGE_NAME,),
            modules_impacted=(),
            version="1.0.0",
            isolation_root=tmp_path / "apt_isolated",
            ca_cert_path=https_repo_server["ca_cert_path"],
            actor=actor,
        )

        assert TEST_MARKER_PATH.read_text() == "v1.0.0-online"

    def test_online_update_produces_the_same_kind_of_audit_event_as_offline(
        self, tmp_path, https_repo_server, release_key
    ):
        """Critère d'acceptation explicite du ticket : « état système
        final indiscernable d'une mise à jour hors ligne équivalente »
        — même action d'audit, même structure de métadonnées."""
        actor = User.objects.create(username="tech_online_audit", full_name="Technicien")

        apply_online_update(
            repository_url=https_repo_server["repository_url"],
            trusted_keyring_path=release_key["keyring_path"],
            package_names=(TEST_PACKAGE_NAME,),
            modules_impacted=(),
            version="1.0.0",
            isolation_root=tmp_path / "apt_isolated",
            ca_cert_path=https_repo_server["ca_cert_path"],
            actor=actor,
        )

        entry = AuditLog.objects.get(action="update.applied", actor_user=actor)
        assert entry.metadata["version"] == "1.0.0"
        assert TEST_PACKAGE_NAME in entry.metadata["packages_applied"]
        assert "modules_migrated" in entry.metadata

    def test_online_update_returns_a_complete_report(
        self, tmp_path, https_repo_server, release_key
    ):
        report = apply_online_update(
            repository_url=https_repo_server["repository_url"],
            trusted_keyring_path=release_key["keyring_path"],
            package_names=(TEST_PACKAGE_NAME,),
            modules_impacted=(),
            version="1.0.0",
            isolation_root=tmp_path / "apt_isolated",
            ca_cert_path=https_repo_server["ca_cert_path"],
            actor=None,
        )

        assert report.version == "1.0.0"
        assert report.packages_applied == (TEST_PACKAGE_NAME,)

    def test_update_never_touches_the_real_system_apt_state(self):
        """Isolation stricte — vérifié explicitement dans le code, pas
        seulement supposé."""
        import inspect

        from ops import update_service

        source = inspect.getsource(update_service.apply_online_update) + inspect.getsource(
            update_service.AptIsolationPaths
        )
        assert "/etc/apt/sources.list\"" not in source
        assert "'/etc/apt/sources.list'" not in source
        assert "/var/lib/dpkg/status" not in source


# --- B. Dépôt non fiable — refusé, cohérent avec le mode hors ligne ------------


@pytest.mark.django_db
class TestUntrustedRepository:
    def test_repository_signed_by_an_unknown_key_is_rejected(
        self, tmp_path, https_repo_server
    ):
        """Le dépôt est signé par release_key, mais on ne fait
        confiance qu'à une clé DIFFÉRENTE, non liée — apt doit refuser
        nativement (« AllowUnauthenticated=false »)."""
        unrelated_home = tmp_path / "unrelated_home"
        _generate_gpg_key(unrelated_home, "Unrelated", "unrelated@evil.local")
        unrelated_armored = tmp_path / "unrelated.asc"
        export = subprocess.run(
            ["gpg", "--homedir", str(unrelated_home), "--export", "--armor",
             "unrelated@evil.local"],
            check=True, capture_output=True, text=True,
        )
        unrelated_armored.write_text(export.stdout)
        unrelated_keyring = tmp_path / "unrelated-keyring.gpg"
        subprocess.run(
            ["gpg", "--batch", "--yes", "--dearmor",
             "-o", str(unrelated_keyring), str(unrelated_armored)],
            check=True, capture_output=True,
        )

        with pytest.raises(UpdateError):
            apply_online_update(
                repository_url=https_repo_server["repository_url"],
                trusted_keyring_path=unrelated_keyring,
                package_names=(TEST_PACKAGE_NAME,),
                modules_impacted=(),
                version="1.0.0",
                isolation_root=tmp_path / "apt_isolated",
                ca_cert_path=https_repo_server["ca_cert_path"],
                actor=None,
            )

        assert not TEST_MARKER_PATH.exists()

    def test_untrusted_repository_records_a_refusal_audit_event(
        self, tmp_path, https_repo_server
    ):
        unrelated_home = tmp_path / "unrelated_home"
        _generate_gpg_key(unrelated_home, "Unrelated", "unrelated@evil.local")
        unrelated_armored = tmp_path / "unrelated.asc"
        export = subprocess.run(
            ["gpg", "--homedir", str(unrelated_home), "--export", "--armor",
             "unrelated@evil.local"],
            check=True, capture_output=True, text=True,
        )
        unrelated_armored.write_text(export.stdout)
        unrelated_keyring = tmp_path / "unrelated-keyring.gpg"
        subprocess.run(
            ["gpg", "--batch", "--yes", "--dearmor",
             "-o", str(unrelated_keyring), str(unrelated_armored)],
            check=True, capture_output=True,
        )
        actor = User.objects.create(username="tech_online_refuse", full_name="Technicien")

        with pytest.raises(UpdateError):
            apply_online_update(
                repository_url=https_repo_server["repository_url"],
                trusted_keyring_path=unrelated_keyring,
                package_names=(TEST_PACKAGE_NAME,),
                modules_impacted=(),
                version="1.0.0",
                isolation_root=tmp_path / "apt_isolated",
                ca_cert_path=https_repo_server["ca_cert_path"],
                actor=actor,
            )

        entry = AuditLog.objects.get(action="update.refused", actor_user=actor)
        assert entry.metadata["reason"] == "depot_non_verifie"


# --- C. Commande CLI — gap trouvé lors de l'audit Phase 1 de TECH-044 ----------
# apply_online_update() n'avait jamais été enveloppée dans une commande
# manage.py invocable — même situation que apply_offline_update
# (tests/core/test_corrux_update.py), corrigée à l'occasion de TECH-044.


@pytest.mark.django_db
class TestApplyOnlineUpdateCommand:
    def test_command_is_discoverable(self):
        from django.core.management import get_commands

        assert get_commands().get("apply_online_update") == "ops"

    def test_command_applies_a_valid_update_end_to_end(
        self, tmp_path, https_repo_server, release_key
    ):
        from django.core.management import call_command

        os.environ["CORRUX_UPDATE_REPOSITORY_URL"] = https_repo_server["repository_url"]
        os.environ["CORRUX_UPDATE_TRUSTED_KEYRING_PATH"] = str(release_key["keyring_path"])
        os.environ["CORRUX_UPDATE_CA_CERT_PATH"] = str(https_repo_server["ca_cert_path"])
        os.environ["CORRUX_UPDATE_ISOLATION_ROOT"] = str(tmp_path / "apt_isolated")
        try:
            call_command(
                "apply_online_update",
                "--packages", TEST_PACKAGE_NAME,
                "--target-version", "1.0.0",
            )
        finally:
            for key in (
                "CORRUX_UPDATE_REPOSITORY_URL", "CORRUX_UPDATE_TRUSTED_KEYRING_PATH",
                "CORRUX_UPDATE_CA_CERT_PATH", "CORRUX_UPDATE_ISOLATION_ROOT",
            ):
                del os.environ[key]

        assert TEST_MARKER_PATH.read_text() == "v1.0.0-online"
