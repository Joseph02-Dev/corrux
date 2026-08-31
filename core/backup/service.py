"""Orchestration de la sauvegarde — corrux-backup.service (§11, §11.1).

Enchaîne : garde-fou support -> pg_dump -> archive stockage -> assemblage
-> chiffrement -> renommage atomique vers le nom final -> rotation ->
traçabilité (backup_runs) + audit.

Intégrité : les artefacts intermédiaires (dump SQL en clair, archive
stockage en clair, assemblage non chiffré) vivent dans un répertoire
temporaire sur le MÊME volume que la destination finale (renommage
atomique garanti) et sont systématiquement supprimés en fin d'exécution,
succès ou échec — jamais de dump en clair laissé sur disque. Une
sauvegarde partiellement produite n'est jamais renommée vers son nom
final : elle n'existe donc jamais sous une forme "valide".
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.utils import timezone

from core.audit.service import record_audit_event
from core.backup.archive import StorageArchiveError, archive_storage
from core.backup.database import DatabaseBackupError, DatabaseConnectionParams, dump_database
from core.backup.encryption import BackupEncryptionError, encrypt_file
from core.backup.guard import BackupDestinationError, ensure_separate_volume
from core.backup.models import BackupRun
from core.backup.rotation import DEFAULT_RETENTION_COUNT, apply_retention
from core.identity.models import User

_RECOVERABLE_ERRORS = (DatabaseBackupError, StorageArchiveError, BackupEncryptionError, OSError)


@dataclass(frozen=True)
class BackupConfig:
    destination_dir: Path
    db_params: DatabaseConnectionParams
    storage_root: Path
    gpg_recipient_key_path: Path
    system_root: Path = Path("/")
    retention_count: int = DEFAULT_RETENTION_COUNT


def run_backup(config: BackupConfig, actor: User | None) -> BackupRun:
    """Exécute un cycle complet de sauvegarde et retourne le BackupRun créé
    (SUCCESS, FAILURE ou REFUSED — jamais d'exception : le résultat est
    toujours consultable en base, cf. critère TECH-009)."""
    started_at = timezone.now()

    try:
        ensure_separate_volume(config.destination_dir, config.system_root)
    except BackupDestinationError as exc:
        return _record_refusal(actor, started_at, str(exc))

    tmp_dir: Path | None = None
    try:
        tmp_dir = Path(
            tempfile.mkdtemp(prefix=".corrux-backup-tmp-", dir=config.destination_dir)
        )

        dump_path = tmp_dir / "database.dump"
        dump_database(config.db_params, dump_path)

        archive_path = tmp_dir / "storage.tar.gz"
        archive_storage(config.storage_root, archive_path)

        bundle_path = tmp_dir / "bundle.tar"
        with tarfile.open(bundle_path, "w") as bundle:
            bundle.add(dump_path, arcname=dump_path.name)
            bundle.add(archive_path, arcname=archive_path.name)

        encrypted_tmp = tmp_dir / "bundle.tar.gpg"
        encrypt_file(bundle_path, encrypted_tmp, config.gpg_recipient_key_path)

        final_name = (
            f"corrux-backup-{started_at.strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}.tar.gpg"
        )
        final_path = config.destination_dir / final_name
        encrypted_tmp.rename(final_path)  # renommage atomique : même volume

        size_bytes = final_path.stat().st_size
        finished_at = timezone.now()

        run = BackupRun.objects.create(
            started_at=started_at,
            finished_at=finished_at,
            status=BackupRun.Status.SUCCESS,
            size_bytes=size_bytes,
            location=str(final_path),
        )
        record_audit_event(
            actor=actor,
            action="backup.run",
            target=str(final_path),
            metadata={"status": "success", "size_bytes": size_bytes},
        )

        _rotate(config)
        return run

    except _RECOVERABLE_ERRORS as exc:
        finished_at = timezone.now()
        run = BackupRun.objects.create(
            started_at=started_at,
            finished_at=finished_at,
            status=BackupRun.Status.FAILURE,
            size_bytes=None,
            location="",
        )
        record_audit_event(
            actor=actor,
            action="backup.run",
            target=str(config.destination_dir),
            metadata={"status": "failure", "error": str(exc)},
        )
        return run
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _record_refusal(actor: User | None, started_at: datetime, reason: str) -> BackupRun:
    finished_at = timezone.now()
    run = BackupRun.objects.create(
        started_at=started_at,
        finished_at=finished_at,
        status=BackupRun.Status.REFUSED,
        size_bytes=None,
        location="",
    )
    record_audit_event(
        actor=actor,
        action="backup.run",
        target="destination-guard",
        metadata={"status": "refused", "reason": reason},
    )
    return run


def _rotate(config: BackupConfig) -> None:
    """Supprime les sauvegardes excédentaires. N'est appelée qu'après un
    succès (cf. run_backup) : jamais avant qu'une nouvelle sauvegarde soit
    validée."""
    existing = list(
        BackupRun.objects.filter(status=BackupRun.Status.SUCCESS)
        .exclude(location="")
        .order_by("-started_at")
        .values_list("location", flat=True)
    )
    apply_retention([Path(p) for p in existing], keep=config.retention_count)
