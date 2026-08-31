"""Tests des primitives génériques de stockage fichiers — TECH-004.

Cf. architecture-technique-v1.md §8. Utilise un répertoire temporaire
isolé (fixture pytest tmp_path via override_settings) : aucune dépendance
au système de fichiers réel du poste de développement.
"""

import uuid

import pytest
from django.test import override_settings

from core.storage import files as storage
from core.storage.files import InvalidPathError, StorageError


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


class TestWriteRead:
    def test_write_then_read_returns_identical_content(self, storage_root):
        content = b"contenu binaire de test \x00\x01\xff"
        ref = storage.write("documentation", "rapport.pdf", content)
        assert storage.read(ref.module, ref.file_id, ref.filename) == content

    def test_write_generates_a_valid_uuid(self, storage_root):
        ref = storage.write("documentation", "rapport.pdf", b"x")
        uuid.UUID(ref.file_id)  # ne lève pas : c'est un UUID valide

    def test_write_respects_module_uuid_filename_structure_on_disk(self, storage_root):
        ref = storage.write("documentation", "rapport.pdf", b"x")
        expected_path = storage_root / "documentation" / ref.file_id / "rapport.pdf"
        assert expected_path.is_file()
        assert expected_path.read_bytes() == b"x"

    def test_multiple_files_are_independent(self, storage_root):
        ref1 = storage.write("documentation", "a.pdf", b"contenu A")
        ref2 = storage.write("documentation", "b.pdf", b"contenu B")

        assert ref1.file_id != ref2.file_id
        assert storage.read(ref1.module, ref1.file_id, ref1.filename) == b"contenu A"
        assert storage.read(ref2.module, ref2.file_id, ref2.filename) == b"contenu B"

    def test_multiple_modules_are_independent(self, storage_root):
        ref_doc = storage.write("documentation", "fichier.pdf", b"doc")
        ref_rh = storage.write("rh", "fichier.pdf", b"rh")

        assert storage.read("documentation", ref_doc.file_id, "fichier.pdf") == b"doc"
        assert storage.read("rh", ref_rh.file_id, "fichier.pdf") == b"rh"

    def test_valid_filename_with_spaces_and_accents(self, storage_root):
        ref = storage.write("documentation", "rapport final été.pdf", b"x")
        assert storage.read(ref.module, ref.file_id, ref.filename) == b"x"

    def test_reading_nonexistent_file_raises_storage_error(self, storage_root):
        with pytest.raises(StorageError):
            storage.read("documentation", str(uuid.uuid4()), "inconnu.pdf")


class TestPathTraversalProtection:
    def test_filename_with_parent_reference_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.write("documentation", "../evil.txt", b"x")

    def test_filename_with_double_parent_reference_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.write("documentation", "../../etc/passwd", b"x")

    def test_absolute_filename_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.write("documentation", "/etc/passwd", b"x")

    def test_filename_containing_path_separator_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.write("documentation", "sous-dossier/fichier.pdf", b"x")

    def test_filename_containing_backslash_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.write("documentation", "sous-dossier\\fichier.pdf", b"x")

    def test_filename_exactly_dotdot_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.write("documentation", "..", b"x")

    def test_module_with_path_traversal_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.write("../../etc", "fichier.pdf", b"x")

    def test_read_with_forged_filename_traversal_is_rejected(self, storage_root):
        ref = storage.write("documentation", "legit.pdf", b"x")
        with pytest.raises(InvalidPathError):
            storage.read(ref.module, ref.file_id, "../../../etc/passwd")

    def test_read_with_path_as_file_id_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.read("documentation", "../../etc/passwd", "fichier.pdf")

    def test_read_with_non_uuid_string_as_file_id_is_rejected(self, storage_root):
        with pytest.raises(InvalidPathError):
            storage.read("documentation", "not-a-uuid", "fichier.pdf")

    def test_traversal_attempt_never_creates_file_outside_storage_root(
        self, storage_root, tmp_path
    ):
        """Vérifie concrètement, sur le vrai système de fichiers temporaire,
        qu'aucun fichier n'apparaît hors du répertoire de stockage après
        une tentative de traversal."""
        outside_target = tmp_path.parent / "escaped.txt"
        outside_target.unlink(missing_ok=True)

        with pytest.raises(InvalidPathError):
            storage.write("documentation", "../escaped.txt", b"malicious")

        assert not outside_target.exists()
