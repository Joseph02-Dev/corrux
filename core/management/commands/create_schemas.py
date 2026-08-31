"""Crée les schémas PostgreSQL dédiés à chaque module.

Référence : architecture-technique-v1.md §7 — schémas séparés `core`,
`documentation`, `rh`, chacun destiné à porter les tables du module
correspondant. Idempotent : peut être exécutée plusieurs fois sans effet
de bord.
"""

from django.core.management.base import BaseCommand
from django.db import connection

SCHEMAS = ["core", "documentation", "rh"]


class Command(BaseCommand):
    help = "Crée les schémas PostgreSQL core, documentation et rh s'ils n'existent pas."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            for schema in SCHEMAS:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
                self.stdout.write(self.style.SUCCESS(f"Schéma « {schema} » prêt."))
