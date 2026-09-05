"""Tests de la configuration Nginx — TECH-011 (§5, §8, §10).

« Vérification statique de configuration + test d'intégration »
(contrat explicite du ticket) — aucun mock : vrai certificat (réutilise
core.certs.service, TECH-010), vrai nginx démarré comme processus réel
sur des ports non privilégiés, vraies requêtes HTTP/HTTPS.

Démarche déjà vérifiée manuellement avant d'écrire ce fichier (génération
de certificat, démarrage nginx, redirection, HTTPS, en-tête HSTS, arrêt
propre) — un script de test ad hoc a d'abord confirmé que chaque étape
fonctionne réellement, avant d'en faire un test pytest permanent.
"""

import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from core.certs.service import CertificatePaths, initialize_ca_and_server_certificate
from ops.nginx_config import render_nginx_config

NGINX_AVAILABLE = shutil.which("nginx") is not None
pytestmark = pytest.mark.skipif(
    not NGINX_AVAILABLE, reason="nginx non installé dans cet environnement"
)


class _NoRedirectHandler(urllib.request.HTTPErrorProcessor):
    """Empêche urllib de suivre automatiquement une redirection — pour
    vérifier le code 301 et l'en-tête Location eux-mêmes, pas la page
    de destination."""

    def http_response(self, request, response):
        return response

    https_response = http_response


@pytest.fixture
def real_certificate(tmp_path, db):
    paths = CertificatePaths(
        ca_key=tmp_path / "ca.key", ca_cert=tmp_path / "ca.crt",
        server_key=tmp_path / "server.key", server_cert=tmp_path / "server.crt",
    )
    initialize_ca_and_server_certificate(paths, common_name="localhost", actor=None)
    return paths


@pytest.fixture
def dummy_upstream(tmp_path):
    """Un vrai serveur HTTP minimal servant de cible au proxy — le
    scope de ce ticket est le comportement de Nginx lui-même, pas
    corrux-core au complet (déjà couvert par TECH-042)."""
    (tmp_path / "index.html").write_text("contenu-upstream-corrux-core")
    process = subprocess.Popen(
        ["python3", "-m", "http.server", "8091", "--bind", "127.0.0.1",
         "--directory", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    yield "127.0.0.1:8091"
    process.terminate()
    process.wait(timeout=5)


@pytest.fixture
def running_nginx(tmp_path, real_certificate, dummy_upstream):
    """Démarre un vrai processus nginx (mode démon natif, pas
    `daemon off` bloquant) sur des ports non privilégiés, et l'arrête
    systématiquement en fin de test — y compris en cas d'échec."""
    config = render_nginx_config(
        https_port=8443, http_port=8080,
        ssl_cert_path=str(real_certificate.server_cert),
        ssl_key_path=str(real_certificate.server_key),
        upstream_address=dummy_upstream,
    )
    fragment_path = tmp_path / "nginx-corrux-test.conf"
    fragment_path.write_text(config)
    main_conf_path = tmp_path / "nginx-main.conf"
    main_conf_path.write_text(f"events {{}}\nhttp {{ include {fragment_path}; }}\n")
    pid_path = tmp_path / "nginx.pid"

    subprocess.run(
        ["nginx", "-c", str(main_conf_path), "-g", f"pid {pid_path};"],
        check=True, timeout=10,
    )
    time.sleep(0.5)
    try:
        yield {"https_port": 8443, "http_port": 8080, "ca_cert": real_certificate.ca_cert}
    finally:
        subprocess.run(
            ["nginx", "-c", str(main_conf_path), "-g", f"pid {pid_path};", "-s", "stop"],
            timeout=10,
        )


# --- A. Vérification statique — contrat explicite du ticket --------------------


class TestNginxConfigStatic:
    def test_tls_1_2_and_1_3_only(self):
        config = render_nginx_config(ssl_cert_path="/tmp/x.crt", ssl_key_path="/tmp/x.key")
        assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
        assert "TLSv1.1" not in config
        assert "TLSv1 " not in config
        assert "TLSv1;" not in config

    def test_hsts_header_present(self):
        config = render_nginx_config(ssl_cert_path="/tmp/x.crt", ssl_key_path="/tmp/x.key")
        assert "Strict-Transport-Security" in config

    def test_http_redirects_to_https_in_config(self):
        config = render_nginx_config(ssl_cert_path="/tmp/x.crt", ssl_key_path="/tmp/x.key")
        assert "return 301 https://" in config

    def test_no_storage_location_ever_served_directly(self):
        """Critère d'acceptation explicite : « stockage documentaire
        jamais accessible hors de l'application » (§8) — aucun
        location /storage/ ni équivalent, un seul point d'entrée
        applicatif."""
        config = render_nginx_config(ssl_cert_path="/tmp/x.crt", ssl_key_path="/tmp/x.key")
        assert "location /storage" not in config
        assert config.count("location ") == 1
        assert "location / {" in config

    def test_upstream_targets_localhost_only(self):
        """§5 : corrux-core n'écoute que sur localhost."""
        config = render_nginx_config(
            ssl_cert_path="/tmp/x.crt", ssl_key_path="/tmp/x.key",
            upstream_address="127.0.0.1:8000",
        )
        assert "proxy_pass http://127.0.0.1:8000;" in config

    def test_the_real_production_file_matches_the_generator_output(self):
        """Le fichier livré (ops/nginx-corrux.conf) n'est jamais édité
        à la main séparément de la fonction qui le génère — vérifié
        directement, pas supposé."""
        production_file = (
            Path(__file__).resolve().parent.parent.parent / "ops" / "nginx-corrux.conf"
        )
        expected = render_nginx_config(
            ssl_cert_path="/etc/corrux/tls/server.crt",
            ssl_key_path="/etc/corrux/tls/server.key",
        )
        assert production_file.read_text() == expected

    def test_nginx_syntax_check_passes_with_a_real_certificate(
        self, tmp_path, real_certificate, db
    ):
        """« Vérification statique » réelle — nginx -t exécuté pour de
        vrai, pas une relecture de texte seule."""
        config = render_nginx_config(
            https_port=18443, http_port=18080,
            ssl_cert_path=str(real_certificate.server_cert),
            ssl_key_path=str(real_certificate.server_key),
        )
        fragment_path = tmp_path / "nginx-corrux-test.conf"
        fragment_path.write_text(config)
        main_conf_path = tmp_path / "nginx-main.conf"
        main_conf_path.write_text(f"events {{}}\nhttp {{ include {fragment_path}; }}\n")

        result = subprocess.run(
            ["nginx", "-t", "-c", str(main_conf_path)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr


# --- B. Test d'intégration réel — contrat explicite du ticket -------------------


@pytest.mark.django_db
class TestNginxIntegration:
    def test_http_redirects_to_https(self, running_nginx):
        """Critère d'acceptation explicite : « aucune requête non
        chiffrée servie »."""
        opener = urllib.request.build_opener(_NoRedirectHandler)
        response = opener.open(
            f"http://localhost:{running_nginx['http_port']}/index.html", timeout=5
        )
        assert response.status == 301
        assert response.headers.get("Location") == (
            f"https://localhost:{running_nginx['https_port']}/index.html"
        )

    def test_https_serves_the_real_upstream_content(self, running_nginx):
        ctx = ssl.create_default_context(cafile=str(running_nginx["ca_cert"]))
        response = urllib.request.urlopen(
            f"https://localhost:{running_nginx['https_port']}/index.html",
            context=ctx, timeout=5,
        )
        assert response.status == 200
        assert response.read() == b"contenu-upstream-corrux-core"

    def test_https_response_includes_hsts_header(self, running_nginx):
        ctx = ssl.create_default_context(cafile=str(running_nginx["ca_cert"]))
        response = urllib.request.urlopen(
            f"https://localhost:{running_nginx['https_port']}/index.html",
            context=ctx, timeout=5,
        )
        assert "max-age=" in response.headers.get("Strict-Transport-Security", "")

    def test_https_request_fails_without_trusting_the_ca(self, running_nginx):
        """Le certificat est bien signé par la CA interne — un client
        qui ne fait pas confiance à cette CA doit être rejeté, jamais
        un certificat public/auto-signé accepté par défaut."""
        default_ctx = ssl.create_default_context()
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(
                f"https://localhost:{running_nginx['https_port']}/index.html",
                context=default_ctx, timeout=5,
            )
