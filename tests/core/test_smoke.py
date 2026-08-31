"""Tests de fumée INIT-001.

Critères d'acceptation couverts (plan-implementation-v1.md) :
- le projet démarre et répond sur une route de healthcheck minimale ;
- les 3 schémas PostgreSQL (core, documentation, rh) existent après
  exécution de la commande de bootstrap.
"""

import pytest
from django.core.management import call_command
from django.db import connection


def test_healthz_endpoint(client):
    """La route /healthz/ répond 200 avec un statut ok, sans dépendance DB."""
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_create_schemas_command_creates_the_three_schemas():
    """La commande create_schemas crée les 3 schémas requis, de façon idempotente."""
    call_command("create_schemas")
    call_command("create_schemas")  # deuxième exécution : ne doit pas échouer

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name IN ('core', 'documentation', 'rh');"
        )
        found = {row[0] for row in cursor.fetchall()}

    assert found == {"core", "documentation", "rh"}


def test_documentation_and_rh_apps_are_installed_without_models():
    """Documentation et RH sont bien enregistrées, sans modèle en INIT-001."""
    from django.apps import apps

    documentation_app = apps.get_app_config("documentation")
    rh_app = apps.get_app_config("rh")

    assert list(documentation_app.get_models()) == []
    assert list(rh_app.get_models()) == []
