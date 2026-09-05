"""Commande d'entrée pour « corrux-cert renew » (§10) — TECH-010.

§10 : « renouvellement déclenché manuellement par le technicien
(corrux-cert renew), pas d'automatisation réseau requise ». Réalisé
comme `manage.py renew_certificate` — même convention que le reste du
projet (aucun exécutable "corrux-*" séparé n'existe nulle part ailleurs
dans le dépôt ; run_backup.py, TECH-009, suit exactement ce même
principe).
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.certs.openssl import CertificateError
from core.certs.service import (
    DEFAULT_SERVER_CERT_VALIDITY_DAYS,
    CertificatePaths,
    renew_server_certificate,
)


class Command(BaseCommand):
    help = "Renouvelle le certificat serveur CORRUX (signé par la CA existante)."

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
        validity_days = int(
            os.environ.get(
                "CORRUX_CERT_SERVER_VALIDITY_DAYS", DEFAULT_SERVER_CERT_VALIDITY_DAYS
            )
        )

        try:
            renew_server_certificate(
                paths, common_name=common_name, actor=None, validity_days=validity_days
            )
        except CertificateError as exc:
            raise CommandError(f"Renouvellement du certificat échoué : {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Certificat serveur renouvelé ({common_name})."))
