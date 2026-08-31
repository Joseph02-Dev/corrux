"""Tests de chiffrement de la sauvegarde (encrypt_file) — TECH-009.

Utilise une VRAIE paire de clés GPG (générée dans un trousseau temporaire
isolé) pour chiffrer puis déchiffrer réellement — pas de simulation.
"""

import subprocess

import pytest

from core.backup.encryption import BackupEncryptionError, encrypt_file


class TestEncryptFile:
    def test_successful_encryption_produces_output_file(self, tmp_path, gpg_keypair):
        source = tmp_path / "plain.txt"
        source.write_bytes(b"contenu sensible de sauvegarde")
        destination = tmp_path / "chiffre.gpg"

        result = encrypt_file(source, destination, gpg_keypair["public_key_path"])

        assert result == destination
        assert destination.exists()
        assert destination.read_bytes() != source.read_bytes()  # réellement chiffré

    def test_encrypted_content_can_be_decrypted_back_to_original(self, tmp_path, gpg_keypair):
        original_content = b"contenu original a verifier apres dechiffrement"
        source = tmp_path / "plain.txt"
        source.write_bytes(original_content)
        destination = tmp_path / "chiffre.gpg"
        encrypt_file(source, destination, gpg_keypair["public_key_path"])

        decrypted = subprocess.run(
            ["gpg", "--homedir", str(gpg_keypair["gnupg_home"]),
             "--batch", "--yes", "--decrypt", str(destination)],
            check=True, capture_output=True,
        )
        assert decrypted.stdout == original_content

    def test_encryption_fails_with_missing_key_file(self, tmp_path):
        source = tmp_path / "plain.txt"
        source.write_bytes(b"contenu")
        with pytest.raises(BackupEncryptionError):
            encrypt_file(source, tmp_path / "out.gpg", tmp_path / "cle-inexistante.key")

    def test_failed_encryption_leaves_no_output_file(self, tmp_path):
        source = tmp_path / "plain.txt"
        source.write_bytes(b"contenu")
        destination = tmp_path / "out.gpg"
        with pytest.raises(BackupEncryptionError):
            encrypt_file(source, destination, tmp_path / "cle-inexistante.key")
        assert not destination.exists()

    def test_error_message_never_contains_key_material(self, tmp_path, gpg_keypair):
        # Force un échec (source inexistante) et vérifie qu'aucun contenu
        # de clé n'apparaît dans le message d'erreur.
        source = tmp_path / "n-existe-pas.txt"
        destination = tmp_path / "out.gpg"
        key_content = gpg_keypair["public_key_path"].read_text()

        with pytest.raises(BackupEncryptionError) as exc_info:
            encrypt_file(source, destination, gpg_keypair["public_key_path"])

        assert key_content not in str(exc_info.value)
