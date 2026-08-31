"""Tests du garde-fou support de sauvegarde — TECH-009 (§11.1).

Utilise un VRAI montage tmpfs pour obtenir un st_dev réellement distinct
du disque système — pas un booléen simulé.
"""

import os

import pytest

from core.backup.guard import BackupDestinationError, ensure_separate_volume, is_separate_volume


class TestStDevComparison:
    def test_real_separate_volume_has_different_st_dev_than_system_root(
        self, real_separate_volume
    ):
        assert os.stat(real_separate_volume).st_dev != os.stat("/").st_dev

    def test_is_separate_volume_true_for_real_distinct_mount(self, real_separate_volume, tmp_path):
        # tmp_path (racine du test) est sur le même volume que "/" dans ce
        # sandbox ; real_separate_volume est un vrai point de montage distinct.
        assert is_separate_volume(real_separate_volume, tmp_path) is True

    def test_is_separate_volume_false_for_subdirectory_of_same_disk(self, tmp_path):
        subdir = tmp_path / "sous-dossier"
        subdir.mkdir()
        # tmp_path lui-même sert de "disque système" de référence pour ce test :
        # subdir est un simple sous-dossier, même st_dev.
        assert is_separate_volume(subdir, tmp_path) is False


class TestEnsureSeparateVolume:
    def test_valid_destination_is_accepted(self, real_separate_volume, tmp_path):
        ensure_separate_volume(real_separate_volume, tmp_path)  # ne lève pas

    def test_destination_on_system_disk_is_refused(self, tmp_path):
        subdir = tmp_path / "sous-dossier-disque-systeme"
        subdir.mkdir()
        with pytest.raises(BackupDestinationError, match="même.*volume"):
            ensure_separate_volume(subdir, tmp_path)

    def test_nonexistent_destination_is_refused(self, tmp_path):
        with pytest.raises(BackupDestinationError):
            ensure_separate_volume(tmp_path / "n-existe-pas", tmp_path)
