"""Commande d'entrée pour corrux-backup.service (systemd) — TECH-009.

Lit la configuration depuis les variables d'environnement (même
convention que le reste du projet). Acteur = None (exécution automatisée
par le timer systemd, sans utilisateur interactif) — cf. correction
apportée à core.audit_log en TECH-009 pour permettre ce cas.
"""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.backup.database import DatabaseConnectionParams
from core.backup.models import BackupRun
from core.backup.rotation import DEFAULT_RETENTION_COUNT
from core.backup.service import BackupConfig, run_backup


class Command(BaseCommand):
    help = "Exécute une sauvegarde CORRUX (pg_dump + stockage, chiffrée, garde-fou support)."

    def handle(self, *args, **options):
        destination_dir = os.environ.get("CORRUX_BACKUP_DESTINATION")
        recipient_key_path = os.environ.get("CORRUX_BACKUP_GPG_RECIPIENT_KEY_PATH")
        if not destination_dir or not recipient_key_path:
            raise CommandError(
                "CORRUX_BACKUP_DESTINATION et CORRUX_BACKUP_GPG_RECIPIENT_KEY_PATH "
                "doivent être définis (variables d'environnement)."
            )

        db = settings.DATABASES["default"]
        config = BackupConfig(
            destination_dir=Path(destination_dir),
            db_params=DatabaseConnectionParams(
                name=db["NAME"], user=db["USER"], password=db["PASSWORD"],
                host=db["HOST"], port=str(db["PORT"]),
            ),
            storage_root=Path(settings.CORRUX_STORAGE_ROOT),
            gpg_recipient_key_path=Path(recipient_key_path),
            retention_count=int(
                os.environ.get("CORRUX_BACKUP_RETENTION_COUNT", DEFAULT_RETENTION_COUNT)
            ),
        )

        run = run_backup(config, actor=None)
        if run.status != BackupRun.Status.SUCCESS:
            raise CommandError(f"Sauvegarde en échec/refusée (status={run.status}).")
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde réussie : {run.location}"))
