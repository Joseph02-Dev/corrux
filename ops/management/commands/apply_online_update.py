"""Commande d'entrée pour corrux-update --online — §19.3.

Manquait entièrement (audit Phase 1, TECH-044) : apply_online_update()
(TECH-015) n'avait jamais été enveloppée dans une commande invocable —
même situation que apply_offline_update (ops/management/commands/
apply_offline_update.py), corrigée ici selon le même patron.
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ops.update_service import UpdateError, apply_online_update


class Command(BaseCommand):
    help = "Applique une mise à jour CORRUX en ligne depuis le dépôt apt distant (§19.3)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--packages", required=True,
            help="Noms de paquets corrux-* à mettre à jour, séparés par des virgules.",
        )
        parser.add_argument(
            "--modules", default="",
            help="Modules impactés (app_label Django), séparés par des virgules.",
        )
        parser.add_argument(
            "--target-version", required=True, help="Version cible, pour traçabilité."
        )

    def handle(self, *args, **options):
        repository_url = os.environ.get("CORRUX_UPDATE_REPOSITORY_URL")
        trusted_keyring = os.environ.get("CORRUX_UPDATE_TRUSTED_KEYRING_PATH")
        ca_cert = os.environ.get("CORRUX_UPDATE_CA_CERT_PATH")
        if not all([repository_url, trusted_keyring, ca_cert]):
            raise CommandError(
                "CORRUX_UPDATE_REPOSITORY_URL, CORRUX_UPDATE_TRUSTED_KEYRING_PATH et "
                "CORRUX_UPDATE_CA_CERT_PATH doivent être définis (variables d'environnement)."
            )

        isolation_root = Path(
            os.environ.get("CORRUX_UPDATE_ISOLATION_ROOT", "/var/lib/corrux/apt-isolated")
        )
        package_names = tuple(p.strip() for p in options["packages"].split(",") if p.strip())
        modules_impacted = tuple(
            m.strip() for m in options["modules"].split(",") if m.strip()
        )

        try:
            report = apply_online_update(
                repository_url=repository_url,
                trusted_keyring_path=Path(trusted_keyring),
                package_names=package_names,
                modules_impacted=modules_impacted,
                version=options["target_version"],
                isolation_root=isolation_root,
                ca_cert_path=Path(ca_cert),
                actor=None,
            )
        except UpdateError as exc:
            raise CommandError(f"Mise à jour refusée ou échouée : {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(f"Mise à jour appliquée avec succès ({report.version}).")
        )
        self.stdout.write(f"Paquets appliqués : {', '.join(report.packages_applied)}")
        self.stdout.write(f"Modules migrés : {', '.join(report.modules_migrated) or '(aucun)'}")
