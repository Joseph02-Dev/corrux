"""Commande d'entrée pour corrux-cert-check.timer (systemd) — TECH-010.

Même convention que run_backup.py (TECH-009) : configuration lue
depuis les variables d'environnement, acteur = None (exécution
automatisée par le timer systemd, sans utilisateur interactif).
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.certs.openssl import CertificateError
from core.certs.service import DEFAULT_ALERT_THRESHOLD_DAYS, check_certificate_expiry


class Command(BaseCommand):
    help = "Vérifie l'expiration du certificat serveur CORRUX et journalise une alerte si proche."

    def handle(self, *args, **options):
        server_cert = os.environ.get("CORRUX_CERT_SERVER_CERT_PATH")
        if not server_cert:
            raise CommandError(
                "CORRUX_CERT_SERVER_CERT_PATH doit être défini (variable d'environnement)."
            )

        alert_threshold_days = int(
            os.environ.get(
                "CORRUX_CERT_ALERT_THRESHOLD_DAYS", DEFAULT_ALERT_THRESHOLD_DAYS
            )
        )

        try:
            alerted = check_certificate_expiry(
                Path(server_cert), actor=None, alert_threshold_days=alert_threshold_days
            )
        except CertificateError as exc:
            raise CommandError(f"Vérification de l'expiration échouée : {exc}") from exc

        if alerted:
            self.stdout.write(
                self.style.WARNING(
                    f"Certificat proche de l'expiration (seuil {alert_threshold_days} jours) "
                    "— entrée audit_log créée."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Certificat valide, aucune alerte nécessaire."))
