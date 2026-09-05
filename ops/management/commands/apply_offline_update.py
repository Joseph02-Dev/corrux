"""Commande d'entrée pour corrux-update (mode hors ligne) — §19.2.

Manquait entièrement (audit Phase 1, TECH-044) : apply_offline_update()
(TECH-014) n'avait jamais été enveloppée dans une commande invocable —
seule la fonction de service existait, jamais de point d'entrée CLI
pour le technicien. Corrigé ici, même patron exact que les commandes
déjà établies (initialize_ca, renew_certificate,
check_certificate_expiry, TECH-010) : configuration lue depuis les
variables d'environnement, acteur = None par défaut (le technicien
exécute cette commande interactivement, mais aucun compte
applicatif n'est nécessairement authentifié à ce moment).
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ops.update_service import UpdateError, apply_offline_update


class Command(BaseCommand):
    help = "Applique une mise à jour CORRUX hors ligne depuis un support amovible (§19.2)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--device", required=True,
            help="Périphérique du support amovible (ex. /dev/sdb1).",
        )

    def handle(self, *args, **options):
        mount_point = Path(os.environ.get("CORRUX_UPDATE_MOUNT_POINT", "/mnt/corrux-update"))
        trusted_key = os.environ.get("CORRUX_UPDATE_TRUSTED_KEY_PATH")
        if not trusted_key:
            raise CommandError(
                "CORRUX_UPDATE_TRUSTED_KEY_PATH doit être défini (variable d'environnement)."
            )

        try:
            report = apply_offline_update(
                media_device_path=options["device"],
                mount_point=mount_point,
                trusted_public_key_path=Path(trusted_key),
                actor=None,
            )
        except UpdateError as exc:
            raise CommandError(f"Mise à jour refusée ou échouée : {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(f"Mise à jour appliquée avec succès ({report.version}).")
        )
        self.stdout.write(f"Paquets appliqués : {', '.join(report.packages_applied)}")
        self.stdout.write(f"Modules migrés : {', '.join(report.modules_migrated) or '(aucun)'}")
