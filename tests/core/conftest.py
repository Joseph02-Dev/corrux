"""Fixtures partagées — tests core (TECH-009 notamment).

Fournit un vrai volume tmpfs monté (st_dev réellement distinct) et une
vraie paire de clés GPG générée dans un trousseau isolé, pour des tests
qui exercent le comportement réel plutôt que des simulations.
"""

import subprocess

import pytest


@pytest.fixture
def real_separate_volume(tmp_path):
    """Monte un vrai tmpfs (st_dev garanti différent de /) et le démonte
    à la fin du test. Skip si le montage n'est pas permis dans cet
    environnement plutôt que de simuler."""
    mount_point = tmp_path / "separate_volume"
    mount_point.mkdir()
    result = subprocess.run(
        ["mount", "-t", "tmpfs", "-o", "size=20m", "tmpfs", str(mount_point)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Montage tmpfs impossible dans cet environnement : {result.stderr.strip()}")
    try:
        yield mount_point
    finally:
        subprocess.run(["umount", str(mount_point)], capture_output=True, text=True)


@pytest.fixture
def gpg_keypair(tmp_path):
    """Génère une vraie paire de clés GPG dans un trousseau temporaire
    isolé (GNUPGHOME dédié) et exporte la clé publique en fichier brut."""
    gnupg_home = tmp_path / "gnupg-home"
    gnupg_home.mkdir(mode=0o700)

    key_params = tmp_path / "key-params.txt"
    key_params.write_text(
        "%no-protection\n"
        "Key-Type: RSA\nKey-Length: 2048\n"
        "Name-Real: CORRUX Test\nName-Email: test@corrux.local\n"
        "Expire-Date: 0\n%commit\n"
    )
    subprocess.run(
        ["gpg", "--homedir", str(gnupg_home), "--batch", "--generate-key", str(key_params)],
        check=True, capture_output=True, text=True,
    )

    public_key_path = tmp_path / "public.key"
    export = subprocess.run(
        ["gpg", "--homedir", str(gnupg_home), "--export", "--armor", "test@corrux.local"],
        check=True, capture_output=True, text=True,
    )
    public_key_path.write_text(export.stdout)

    return {"gnupg_home": gnupg_home, "public_key_path": public_key_path}
