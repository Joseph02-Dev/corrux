"""Tests d'archivage du stockage documentaire (archive_storage) — TECH-009."""

import tarfile

import pytest

from core.backup.archive import StorageArchiveError, archive_storage


class TestArchiveStorage:
    def test_successful_archive_contains_all_files(self, tmp_path):
        storage_root = tmp_path / "storage"
        (storage_root / "documentation" / "abc-uuid").mkdir(parents=True)
        (storage_root / "documentation" / "abc-uuid" / "rapport.pdf").write_bytes(b"contenu pdf")
        (storage_root / "rh" / "def-uuid").mkdir(parents=True)
        (storage_root / "rh" / "def-uuid" / "contrat.pdf").write_bytes(b"contenu contrat")

        destination = tmp_path / "archive.tar.gz"
        result = archive_storage(storage_root, destination)

        assert result == destination
        assert destination.exists()

        with tarfile.open(destination, "r:gz") as tar:
            names = tar.getnames()
            assert any(name.endswith("rapport.pdf") for name in names)
            assert any(name.endswith("contrat.pdf") for name in names)
            extracted = tar.extractfile(
                next(n for n in names if n.endswith("rapport.pdf"))
            ).read()
            assert extracted == b"contenu pdf"

    def test_archiving_nonexistent_storage_root_raises(self, tmp_path):
        with pytest.raises(StorageArchiveError):
            archive_storage(tmp_path / "n-existe-pas", tmp_path / "archive.tar.gz")

    def test_failed_archive_leaves_no_partial_file(self, tmp_path):
        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        # Destination dans un répertoire parent inexistant : échec réel d'écriture.
        bad_destination = tmp_path / "chemin-invalide" / "archive.tar.gz"

        with pytest.raises(StorageArchiveError):
            archive_storage(storage_root, bad_destination)

        assert not bad_destination.exists()

    def test_archive_with_special_characters_in_paths(self, tmp_path):
        storage_root = tmp_path / "storage"
        target_dir = storage_root / "documentation" / "uuid-1"
        target_dir.mkdir(parents=True)
        (target_dir / "rapport final été (v2).pdf").write_bytes(b"x")

        destination = tmp_path / "archive.tar.gz"
        archive_storage(storage_root, destination)

        with tarfile.open(destination, "r:gz") as tar:
            assert any("rapport final été (v2).pdf" in name for name in tar.getnames())
