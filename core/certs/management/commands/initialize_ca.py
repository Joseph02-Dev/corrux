"""Commande d'entrée pour l'initialisation CA + premier certificat —
appelée par corrux-setup (TECH-012, hors périmètre de ce ticket) lors
de l'installation initiale — TECH-010.

Même convention que run_backup.py (TECH-009) : configuration lue depuis
les variables d'environnement, acteur = None (exécution automatisée,
sans utilisateur interactif au moment de l'installation).
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.certs.openssl import CertificateError
from core.certs.service import (
    DEFAULT_CA_VALIDITY_DAYS,
    DEFAULT_SERVER_CERT_VALIDITY_DAYS,
    CertificatePaths,
    initialize_ca_and_server_certificate,
)


class Command(BaseCommand):
    help = "Génère la CA interne CORRUX et émet le premier certificat serveur (§10)."

    def handle(self, *args, **options):
        ca_key = os.environ.get("CORRUX_CERT_CA_KEY_PATH")
        ca_cert = os.environ.get("CORRUX_CERT_CA_CERT_PATH")
        server_key = os.environ.get("CORRUX_CERT_SERVER_KEY_PATH")
        server_cert = os.environ.get("CORRUX_CERT_SERVER_CERT_PATH")
        common_name = os.environ.get("CORRUX_CERT_COMMON_NAME")
        if not all([ca_key, ca_cert, server_key, server_cert, common_name]):
            raise CommandError(
                "CORRUX_CERT_CA_KEY_PATH, CORRUX_CERT_CA_CERT_PATH, "
                "CORRUX_CERT_SERVER_KEY_PATH, CORRUX_CERT_SERVER_CERT_PATH et "
                "CORRUX_CERT_COMMON_NAME doivent être définis (variables "
                "d'environnement)."
            )

        paths = CertificatePaths(
            ca_key=Path(ca_key), ca_cert=Path(ca_cert),
            server_key=Path(server_key), server_cert=Path(server_cert),
        )
        ca_validity_days = int(
            os.environ.get("CORRUX_CERT_CA_VALIDITY_DAYS", DEFAULT_CA_VALIDITY_DAYS)
        )
        server_validity_days = int(
            os.environ.get(
                "CORRUX_CERT_SERVER_VALIDITY_DAYS", DEFAULT_SERVER_CERT_VALIDITY_DAYS
            )
        )

        try:
            initialize_ca_and_server_certificate(
                paths, common_name=common_name, actor=None,
                ca_validity_days=ca_validity_days,
                server_validity_days=server_validity_days,
            )
        except CertificateError as exc:
            raise CommandError(f"Initialisation de la CA échouée : {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(f"CA interne et certificat serveur générés ({common_name}).")
        )
