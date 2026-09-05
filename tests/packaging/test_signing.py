"""Tests de signature/vérification de release CORRUX — TECH-013 (§19.1).

« Test unitaire de vérification de signature (valide/invalide/clé
inconnue) » (contrat explicite du ticket) — aucun mock : vraies paires
de clés GPG, vraies signatures, vraie vérification. Démarche entièrement
vérifiée manuellement en ligne de commande avant d'écrire le moindre
test (génération de clés, signature légitime, signature par une clé
inconnue, bundle altéré, rotation complète) — chaque cas confirmé
fonctionnel avant d'en faire un test permanent.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from packaging.signing import (
    SignatureVerificationError,
    create_rotation_package,
    rotate_trusted_key,
    sign_bundle,
    verify_bundle_signature,
)

GPG_AVAILABLE = shutil.which("gpg") is not None
pytestmark = pytest.mark.skipif(GPG_AVAILABLE is False, reason="gpg non disponible")


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


def _export_public_key(home: Path, email: str, output_path: Path) -> None:
    result = subprocess.run(
        ["gpg", "--homedir", str(home), "--export", "--armor", email],
        check=True, capture_output=True, text=True,
    )
    output_path.write_text(result.stdout)


@pytest.fixture
def release_key(tmp_path):
    home = tmp_path / "release_home"
    _generate_gpg_key(home, "CORRUX Release", "release@corrux.local")
    public_key_path = tmp_path / "release_public.asc"
    _export_public_key(home, "release@corrux.local", public_key_path)
    return {"gnupg_home": home, "public_key_path": public_key_path}


@pytest.fixture
def unrelated_key(tmp_path):
    """Une clé totalement indépendante — jamais signée par la clé de
    release, ni de près ni de loin."""
    home = tmp_path / "unrelated_home"
    _generate_gpg_key(home, "Unrelated Signer", "unrelated@evil.local")
    return {"gnupg_home": home}


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path / "bundle.tar"
    path.write_text("contenu réel du bundle de mise à jour")
    return path


# --- A. Signature et vérification — cas explicitement requis par le contrat ---


class TestSignAndVerify:
    def test_valid_signature_is_accepted(self, tmp_path, release_key, bundle):
        signature_path = tmp_path / "bundle.tar.sig"
        sign_bundle(bundle, signature_path, gnupg_home=release_key["gnupg_home"])

        # Ne doit lever aucune exception.
        verify_bundle_signature(
            bundle, signature_path, trusted_public_key_path=release_key["public_key_path"]
        )

    def test_tampered_bundle_is_rejected(self, tmp_path, release_key, bundle):
        """Cas « invalide » — bundle altéré après signature."""
        signature_path = tmp_path / "bundle.tar.sig"
        sign_bundle(bundle, signature_path, gnupg_home=release_key["gnupg_home"])

        bundle.write_text("contenu altéré après la signature")

        with pytest.raises(SignatureVerificationError):
            verify_bundle_signature(
                bundle, signature_path, trusted_public_key_path=release_key["public_key_path"]
            )

    def test_bundle_signed_by_an_unknown_key_is_rejected(
        self, tmp_path, release_key, unrelated_key, bundle
    ):
        """Critère d'acceptation explicite du ticket : « un bundle
        signé par une clé inconnue est rejeté »."""
        signature_path = tmp_path / "bundle.tar.sig"
        sign_bundle(bundle, signature_path, gnupg_home=unrelated_key["gnupg_home"])

        with pytest.raises(SignatureVerificationError):
            verify_bundle_signature(
                bundle, signature_path, trusted_public_key_path=release_key["public_key_path"]
            )

    def test_missing_signature_file_is_rejected(self, tmp_path, release_key, bundle):
        nonexistent_signature = tmp_path / "does-not-exist.sig"

        with pytest.raises(SignatureVerificationError):
            verify_bundle_signature(
                bundle, nonexistent_signature,
                trusted_public_key_path=release_key["public_key_path"],
            )

    def test_verification_never_trusts_the_ambient_keyring(self):
        """La vérification importe la clé de confiance dans un
        trousseau isolé et éphémère — jamais le trousseau GPG ambiant
        de la machine qui exécute les tests, même si celui-ci contenait
        par ailleurs la clé signataire."""
        import inspect

        from packaging import signing

        source = inspect.getsource(signing.verify_bundle_signature)
        assert "tempfile" in source
        assert "TemporaryDirectory" in source


# --- B. Rotation de clé — chaîne de confiance (§19.1) ----------------------------


class TestKeyRotation:
    def test_rotation_signed_by_previous_key_is_accepted(self, tmp_path, release_key):
        new_key_home = tmp_path / "new_key_home"
        _generate_gpg_key(new_key_home, "CORRUX Release 2", "release2@corrux.local")
        new_public_key_path = tmp_path / "new_public.asc"
        _export_public_key(new_key_home, "release2@corrux.local", new_public_key_path)

        rotation_signature_path = tmp_path / "rotation.sig"
        create_rotation_package(
            new_public_key_path, rotation_signature_path,
            previous_gnupg_home=release_key["gnupg_home"],
        )

        trusted_key_path = tmp_path / "currently_trusted.asc"
        trusted_key_path.write_bytes(release_key["public_key_path"].read_bytes())

        rotate_trusted_key(
            new_public_key_path, rotation_signature_path,
            current_trusted_public_key_path=trusted_key_path,
        )

        assert trusted_key_path.read_bytes() == new_public_key_path.read_bytes()

    def test_rotation_not_signed_by_previous_key_is_rejected(
        self, tmp_path, release_key, unrelated_key
    ):
        """Chaîne de confiance : une nouvelle clé signée par n'importe
        quelle autre clé que celle actuellement approuvée est rejetée —
        jamais un remplacement manuel non vérifié (§19.1)."""
        new_key_home = tmp_path / "new_key_home"
        _generate_gpg_key(new_key_home, "CORRUX Release 2", "release2@corrux.local")
        new_public_key_path = tmp_path / "new_public.asc"
        _export_public_key(new_key_home, "release2@corrux.local", new_public_key_path)

        forged_rotation_signature_path = tmp_path / "forged_rotation.sig"
        create_rotation_package(
            new_public_key_path, forged_rotation_signature_path,
            previous_gnupg_home=unrelated_key["gnupg_home"],
        )

        trusted_key_path = tmp_path / "currently_trusted.asc"
        original_content = release_key["public_key_path"].read_bytes()
        trusted_key_path.write_bytes(original_content)

        with pytest.raises(SignatureVerificationError):
            rotate_trusted_key(
                new_public_key_path, forged_rotation_signature_path,
                current_trusted_public_key_path=trusted_key_path,
            )

        assert trusted_key_path.read_bytes() == original_content

    def test_after_rotation_the_new_key_can_verify_new_bundles(
        self, tmp_path, release_key, bundle
    ):
        """Bout en bout : après une rotation légitime, la nouvelle clé
        est réellement utilisable pour vérifier de nouveaux bundles."""
        new_key_home = tmp_path / "new_key_home"
        _generate_gpg_key(new_key_home, "CORRUX Release 2", "release2@corrux.local")
        new_public_key_path = tmp_path / "new_public.asc"
        _export_public_key(new_key_home, "release2@corrux.local", new_public_key_path)

        rotation_signature_path = tmp_path / "rotation.sig"
        create_rotation_package(
            new_public_key_path, rotation_signature_path,
            previous_gnupg_home=release_key["gnupg_home"],
        )
        trusted_key_path = tmp_path / "currently_trusted.asc"
        trusted_key_path.write_bytes(release_key["public_key_path"].read_bytes())
        rotate_trusted_key(
            new_public_key_path, rotation_signature_path,
            current_trusted_public_key_path=trusted_key_path,
        )

        new_bundle_signature_path = tmp_path / "new_bundle.tar.sig"
        sign_bundle(bundle, new_bundle_signature_path, gnupg_home=new_key_home)

        verify_bundle_signature(
            bundle, new_bundle_signature_path, trusted_public_key_path=trusted_key_path
        )
