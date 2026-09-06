"""Tests statiques du build ISO (BUILD-003).

Ne télécharge jamais l'image Debian officielle (trop lourd/lent pour
une suite de tests) : vérifie uniquement (a) la constitution du
dépôt local (rapide, sans réseau), (b) la présence des garde-fous de
sécurité dans build_iso.sh (mot de passe requis, pas de valeur par
défaut), (c) la structure du preseed.

Le test de bout en bout réel (téléchargement + assemblage + boot VM)
est un ticket distinct (BUILD-004), hors périmètre de cette suite.
"""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ISO_DIR = PROJECT_ROOT / "iso"


def test_preseed_has_no_cleartext_password_and_uses_placeholder():
    content = (ISO_DIR / "preseed" / "corrux.preseed").read_text()
    assert "__CORRUX_TECH_PASSWORD_HASH__" in content
    assert "password-crypted" in content
    # Aucune ligne "password password <valeur>" en clair.
    assert "passwd/user-password password" not in content


def test_preseed_disables_root_login_and_uses_sudo_account():
    content = (ISO_DIR / "preseed" / "corrux.preseed").read_text()
    assert "passwd/root-login boolean false" in content


def test_preseed_uses_local_repository_only_no_network_mirror():
    content = (ISO_DIR / "preseed" / "corrux.preseed").read_text()
    assert "apt-setup/local1/repository" in content
    assert "file:/cdrom/corrux-repo" in content


def test_preseed_disables_network_ntp_sync():
    # Bug réel constaté lors de BUILD-004 : la synchronisation horaire
    # réseau (NTP) bouclait indéfiniment en environnement sans sortie
    # réseau fiable pour ce service. Cohérent aussi avec le principe
    # produit « CORRUX fonctionne sans accès Internet garanti ».
    content = (ISO_DIR / "preseed" / "corrux.preseed").read_text()
    assert "clock-setup/ntp boolean false" in content


def test_build_iso_script_injects_early_language_country_locale_params():
    # Bug réel constaté lors de BUILD-004 : sans ces paramètres sur la
    # ligne de commande noyau (en plus du fichier preseed), l'installeur
    # reste bloqué sur l'écran interactif "Select a language" avant même
    # de pouvoir charger le preseed depuis /cdrom.
    content = (ISO_DIR / "build_iso.sh").read_text()
    assert "debian-installer/language=en" in content
    assert "debian-installer/country=US" in content
    assert "debian-installer/locale=en_US.UTF-8" in content


def test_build_iso_script_requires_password_hash_env_var(tmp_path):
    env_without_hash = {"PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [
            str(ISO_DIR / "build_iso.sh"),
            "1.0.0",
            str(tmp_path / "pubkey.asc"),
            str(tmp_path / "work"),
        ],
        env=env_without_hash,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "CORRUX_TECH_PASSWORD_HASH" in (result.stdout + result.stderr)


def test_build_repo_produces_valid_apt_repository(tmp_path):
    out_dir = tmp_path / "repo"
    result = subprocess.run(
        [str(ISO_DIR / "build_repo.sh"), "1.0.0", str(out_dir)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    debs = sorted(p.name for p in out_dir.glob("*.deb"))
    assert debs == [
        "corrux-core_1.0.0_all.deb",
        "corrux-module-documentation_1.0.0_all.deb",
        "corrux-module-rh_1.0.0_all.deb",
    ]
    assert (out_dir / "Packages").exists()
    assert (out_dir / "Packages.gz").exists()
    assert (out_dir / "Release").exists()

    packages_content = (out_dir / "Packages").read_text()
    assert "Package: corrux-core" in packages_content
    assert "Package: corrux-module-documentation" in packages_content
    assert "Package: corrux-module-rh" in packages_content
