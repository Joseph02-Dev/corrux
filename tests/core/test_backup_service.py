"""Tests de bout en bout de l'orchestration de sauvegarde — TECH-009.

Utilise un vrai PostgreSQL, un vrai volume tmpfs (st_dev réel) et une
vraie paire de clés GPG. Vérifie l'état réel en base (backup_runs,
audit_log) et sur le système de fichiers — pas seulement les exceptions
Python.
"""

import os
import subprocess
import tarfile

import pytest

from core.audit.models import AuditLog
from core.backup.database import DatabaseConnectionParams
from core.backup.models import BackupRun
from core.backup.service import BackupConfig, run_backup


def _db_params(**overrides) -> DatabaseConnectionParams:
    base = dict(
        name=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
    )
    base.update(overrides)
    return DatabaseConnectionParams(**base)


def _make_storage(root, filename="rapport.pdf", content=b"contenu du stockage documentaire"):
    target = root / "documentation" / "uuid-1234"
    target.mkdir(parents=True)
    (target / filename).write_bytes(content)
    return root


def _decrypt_and_open(backup_path, gpg_keypair):
    decrypted = subprocess.run(
        ["gpg", "--homedir", str(gpg_keypair["gnupg_home"]),
         "--batch", "--yes", "--decrypt", str(backup_path)],
        check=True, capture_output=True,
    )
    return decrypted.stdout


@pytest.mark.django_db
class TestSuccessfulBackup:
    def test_successful_backup_end_to_end(self, tmp_path, real_separate_volume, gpg_keypair):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume,
            db_params=_db_params(),
            storage_root=storage_root,
            gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )

        run = run_backup(config, actor=None)

        assert run.status == BackupRun.Status.SUCCESS
        assert run.size_bytes is not None and run.size_bytes > 0
        assert run.location != ""
        from pathlib import Path
        assert Path(run.location).exists()
        assert Path(run.location).parent == real_separate_volume

    def test_backup_run_row_reflects_success_in_database(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)

        stored = BackupRun.objects.get(pk=run.pk)
        assert stored.status == BackupRun.Status.SUCCESS
        assert stored.finished_at >= stored.started_at

    def test_successful_backup_is_audited(self, tmp_path, real_separate_volume, gpg_keypair):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run_backup(config, actor=None)

        entry = AuditLog.objects.get(action="backup.run", metadata__status="success")
        assert entry.actor_user is None  # exécution automatisée
        assert entry.metadata["size_bytes"] > 0

    def test_no_temporary_artifacts_left_after_success(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run_backup(config, actor=None)

        leftovers = [
            p for p in real_separate_volume.iterdir() if p.name.startswith(".corrux-backup-tmp-")
        ]
        assert leftovers == []

    def test_final_archive_contains_the_documentary_storage_correctly(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(
            tmp_path / "storage", filename="original.pdf", content=b"contenu original exact"
        )
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)

        from pathlib import Path

        plain_bundle = _decrypt_and_open(Path(run.location), gpg_keypair)
        bundle_path = tmp_path / "decrypted-bundle.tar"
        bundle_path.write_bytes(plain_bundle)

        with tarfile.open(bundle_path, "r") as bundle:
            names = bundle.getnames()
            assert "database.dump" in names
            assert "storage.tar.gz" in names
            storage_archive_bytes = bundle.extractfile("storage.tar.gz").read()

        storage_archive_path = tmp_path / "extracted-storage.tar.gz"
        storage_archive_path.write_bytes(storage_archive_bytes)
        with tarfile.open(storage_archive_path, "r:gz") as storage_tar:
            member = next(n for n in storage_tar.getnames() if n.endswith("original.pdf"))
            content = storage_tar.extractfile(member).read()
            assert content == b"contenu original exact"

    def test_paths_with_spaces_and_accents_are_handled_correctly(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(
            tmp_path / "storage été", filename="rapport final (v2).pdf"
        )
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)
        assert run.status == BackupRun.Status.SUCCESS


@pytest.mark.django_db
class TestDatabaseFailure:
    def test_pg_dump_failure_results_in_failure_status(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume,
            db_params=_db_params(password="mot-de-passe-incorrect"),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)

        assert run.status == BackupRun.Status.FAILURE
        assert run.size_bytes is None
        assert run.location == ""

    def test_pg_dump_failure_produces_no_final_backup_file(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume,
            db_params=_db_params(password="mot-de-passe-incorrect"),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run_backup(config, actor=None)
        assert list(real_separate_volume.glob("corrux-backup-*.tar.gpg")) == []

    def test_pg_dump_failure_leaves_no_temporary_artifacts(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume,
            db_params=_db_params(password="mot-de-passe-incorrect"),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run_backup(config, actor=None)
        leftovers = list(real_separate_volume.glob(".corrux-backup-tmp-*"))
        assert leftovers == []

    def test_pg_dump_failure_is_audited(self, tmp_path, real_separate_volume, gpg_keypair):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume,
            db_params=_db_params(password="mot-de-passe-incorrect"),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run_backup(config, actor=None)

        entry = AuditLog.objects.get(action="backup.run", metadata__status="failure")
        assert "mot-de-passe-incorrect" not in str(entry.metadata)


@pytest.mark.django_db
class TestStorageArchiveFailure:
    def test_missing_storage_root_results_in_failure_status(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=tmp_path / "n-existe-pas",
            gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)
        assert run.status == BackupRun.Status.FAILURE


@pytest.mark.django_db
class TestEncryptionFailure:
    def test_missing_encryption_key_results_in_failure_status(
        self, tmp_path, real_separate_volume
    ):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root,
            gpg_recipient_key_path=tmp_path / "cle-inexistante.key",
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)
        assert run.status == BackupRun.Status.FAILURE
        assert list(real_separate_volume.glob("corrux-backup-*.tar.gpg")) == []


@pytest.mark.django_db
class TestDestinationGuardRefusal:
    def test_destination_on_system_disk_is_refused(self, tmp_path, gpg_keypair):
        on_system_disk = tmp_path / "sous-dossier-disque-systeme"
        on_system_disk.mkdir()
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=on_system_disk, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)

        assert run.status == BackupRun.Status.REFUSED
        assert run.location == ""
        assert list(on_system_disk.iterdir()) == []  # rien écrit du tout

    def test_refusal_is_audited(self, tmp_path, gpg_keypair):
        on_system_disk = tmp_path / "sous-dossier-disque-systeme"
        on_system_disk.mkdir()
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=on_system_disk, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run_backup(config, actor=None)

        entry = AuditLog.objects.get(action="backup.run", metadata__status="refused")
        assert "volume" in entry.metadata["reason"]

    def test_separate_volume_destination_is_allowed(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path,
        )
        run = run_backup(config, actor=None)
        assert run.status == BackupRun.Status.SUCCESS


@pytest.mark.django_db
class TestRotation:
    def test_rotation_keeps_only_configured_count_after_several_successes(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        for _ in range(5):
            config = BackupConfig(
                destination_dir=real_separate_volume, db_params=_db_params(),
                storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
                system_root=tmp_path, retention_count=2,
            )
            run = run_backup(config, actor=None)
            assert run.status == BackupRun.Status.SUCCESS

        remaining_files = list(real_separate_volume.glob("corrux-backup-*.tar.gpg"))
        assert len(remaining_files) == 2
        # Les 5 exécutions restent toutes tracées en base (historique conservé).
        assert BackupRun.objects.filter(status=BackupRun.Status.SUCCESS).count() == 5

    def test_failed_backup_does_not_delete_previous_successful_backups(
        self, tmp_path, real_separate_volume, gpg_keypair
    ):
        storage_root = _make_storage(tmp_path / "storage")
        good_config = BackupConfig(
            destination_dir=real_separate_volume, db_params=_db_params(),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path, retention_count=7,
        )
        first_run = run_backup(good_config, actor=None)
        assert first_run.status == BackupRun.Status.SUCCESS
        from pathlib import Path

        assert Path(first_run.location).exists()

        bad_config = BackupConfig(
            destination_dir=real_separate_volume,
            db_params=_db_params(password="mot-de-passe-incorrect"),
            storage_root=storage_root, gpg_recipient_key_path=gpg_keypair["public_key_path"],
            system_root=tmp_path, retention_count=7,
        )
        second_run = run_backup(bad_config, actor=None)
        assert second_run.status == BackupRun.Status.FAILURE

        # La sauvegarde réussie précédente est toujours là.
        assert Path(first_run.location).exists()
