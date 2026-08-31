"""Test trivial de configuration — INIT-002.

Vérifie que l'outillage de test (pytest-django) est correctement câblé sur
le projet, sans dépendre d'une fonctionnalité métier ultérieure.
"""

from django.conf import settings


def test_django_settings_are_configured():
    assert settings.configured is True
    assert settings.ROOT_URLCONF == "corrux_core.urls"
