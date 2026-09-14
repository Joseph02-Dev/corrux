"""Tests statiques du build ISO (BUILD-003).

Ne télécharge jamais l'image Debian officielle (trop lourd/lent pour
une suite de tests) : vérifie uniquement (a) la constitution du
dépôt local (rapide, sans réseau), (b) la présence des garde-fous de
sécurité dans build_iso.sh (mot de passe requis, pas de valeur par
défaut), (c) la structure du preseed.

Le test de bout en bout réel (téléchargement + assemblage + boot VM)
est un ticket distinct (BUILD-004), hors périmètre de cette suite.
"""

import os
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


def test_preseed_disables_cdrom_eject():
    # Blocage réel constaté (BUILD-005) : sans cette directive,
    # l'installation se fige indéfiniment après l'écriture de la liste
    # de sources apt (cycle éjecter/réinsérer impossible en VM).
    content = (ISO_DIR / "preseed" / "corrux.preseed").read_text()
    assert "cdrom-detect/eject boolean false" in content


def test_preseed_registers_install_media_as_apt_source():
    content = (ISO_DIR / "preseed" / "corrux.preseed").read_text()
    assert "apt-setup/cdrom/set-first boolean true" in content


def test_build_iso_uses_dvd_not_netinst():
    # Décision BUILD-005 : netinst ne peut pas installer le système de
    # base sans miroir réseau, ce qui contredit l'exigence offline.
    content = (ISO_DIR / "build_iso.sh").read_text()
    assert "iso-dvd" in content
    assert "DVD-1" in content


def test_kvm_install_test_script_requires_hardware_acceleration():
    # Le test d'installation doit refuser de tourner sans KVM plutôt
    # que de partir dans un run de plusieurs heures (leçon BUILD-005).
    content = (ISO_DIR / "test_boot" / "run_install_test.sh").read_text()
    assert "/dev/kvm" in content
    assert "-accel kvm" in content


def test_kvm_install_test_verifies_packages_on_disk_not_installer_output():
    # Leçon BUILD-005 : le marqueur <ERR> de l'interface texte est un
    # faux positif — la vérification doit porter sur le disque produit.
    content = (ISO_DIR / "test_boot" / "run_install_test.sh").read_text()
    assert "dpkg-query" in content
    assert "corrux-core" in content


def test_password_injection_is_literal_not_sed():
    # Bug critique trouvé en revue de code : `sed s|token|$HASH|`
    # interprète `&` comme « le motif trouvé », ce qui corrompt
    # silencieusement un hash contenant ce caractère (mot de passe
    # technicien inutilisable, sans aucune erreur). Le remplacement
    # doit être strictement littéral.
    content = (ISO_DIR / "build_iso.sh").read_text()
    assert "sed \"s|__CORRUX_TECH_PASSWORD_HASH__|" not in content
    assert "ENVIRON[\"CORRUX_TECH_PASSWORD_HASH\"]" in content


def test_password_injection_handles_ampersand(tmp_path):
    """Test fonctionnel : un hash contenant `&` doit être injecté tel quel."""
    preseed = tmp_path / "sample.preseed"
    preseed.write_text(
        "d-i passwd/user-password-crypted password __CORRUX_TECH_PASSWORD_HASH__\n"
    )
    tricky_hash = "$6$ab&cd$ef&gh"

    # Réutilise exactement le programme awk du script de build.
    build_script = (ISO_DIR / "build_iso.sh").read_text()
    start = build_script.index("awk '\n    BEGIN { token")
    awk_program = build_script[start + len("awk '") :].split("'", 1)[0]

    result = subprocess.run(
        ["awk", awk_program, str(preseed)],
        env={**os.environ, "CORRUX_TECH_PASSWORD_HASH": tricky_hash},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert tricky_hash in result.stdout
    assert "__CORRUX_TECH_PASSWORD_HASH__" not in result.stdout


def test_build_iso_fails_when_no_boot_entry_patched():
    # Sans ce garde-fou, un changement de chemin côté Debian
    # produirait une ISO sans preseed, sans aucun signal au build.
    content = (ISO_DIR / "build_iso.sh").read_text()
    assert "BOOT_ENTRIES_PATCHED" in content
    assert 'if [ "${BOOT_ENTRIES_PATCHED}" -eq 0 ]' in content


def test_download_uses_curl_fail_flag():
    # Sans --fail, une page d'erreur HTTP serait écrite dans le .iso.
    content = (ISO_DIR / "build_iso.sh").read_text()
    assert "curl -fsSL" in content


def test_md5sum_regeneration_uses_batched_exec():
    # `-exec ... \;` lance un process par fichier : sur une image DVD
    # (~15 000 fichiers) c'est ~180x plus lent que `-exec ... +`
    # (mesuré), pour un résultat identique.
    content = (ISO_DIR / "build_iso.sh").read_text()
    assert "-exec md5sum {} +" in content
    assert "-exec md5sum {} \\;" not in content


def test_install_test_cleans_up_disk_but_keeps_on_failure():
    # Le disque de test pèse plusieurs Go : nettoyé en cas de succès,
    # conservé en cas d'échec pour permettre le diagnostic.
    content = (ISO_DIR / "test_boot" / "run_install_test.sh").read_text()
    assert "trap cleanup EXIT" in content
    assert "CORRUX_TEST_KEEP" in content


def test_password_hash_exposure_is_documented():
    # Le hash est lisible dans l'ISO (contrainte inhérente au preseed).
    # Cette propriété de sécurité doit être explicitement documentée
    # pour qui exploite le système, pas seulement connue des auteurs.
    readme = (ISO_DIR / "README.md").read_text()
    assert "lisible dans l'ISO" in readme
    assert "hors ligne" in readme

    preseed = (ISO_DIR / "preseed" / "corrux.preseed").read_text()
    assert "LISIBLE dans l'ISO" in preseed


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
