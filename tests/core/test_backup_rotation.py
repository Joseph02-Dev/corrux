"""Tests de la politique de rétention (apply_retention) — TECH-009."""

from core.backup.rotation import apply_retention


class TestApplyRetention:
    def test_keeps_the_n_most_recent_backups(self, tmp_path):
        files = []
        for i in range(10):
            f = tmp_path / f"backup-{i}.tar.gpg"
            f.write_bytes(b"x")
            files.append(f)
        # Ordre du plus récent au plus ancien (convention imposée à l'appelant).
        ordered = list(reversed(files))

        deleted = apply_retention(ordered, keep=7)

        assert len(deleted) == 3
        remaining = [f for f in ordered if f.exists()]
        assert len(remaining) == 7
        # Les 7 conservés sont bien les plus récents (index 0 à 6 de `ordered`).
        assert {f.name for f in remaining} == {f.name for f in ordered[:7]}

    def test_fewer_backups_than_retention_deletes_nothing(self, tmp_path):
        files = []
        for i in range(3):
            f = tmp_path / f"backup-{i}.tar.gpg"
            f.write_bytes(b"x")
            files.append(f)

        deleted = apply_retention(files, keep=7)

        assert deleted == []
        assert all(f.exists() for f in files)

    def test_missing_file_does_not_raise(self, tmp_path):
        missing = tmp_path / "deja-supprime.tar.gpg"  # n'existe jamais
        existing = tmp_path / "existe.tar.gpg"
        existing.write_bytes(b"x")

        deleted = apply_retention([existing, missing], keep=0)

        assert not existing.exists()
        assert missing in deleted  # tenté, sans erreur (unlink missing_ok)
