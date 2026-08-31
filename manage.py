#!/usr/bin/env python
"""Utilitaire en ligne de commande Django pour corrux-core."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "corrux_core.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django est introuvable. Vérifiez que l'environnement virtuel "
            "est activé et que les dépendances sont installées "
            "(pip install -r requirements.txt)."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
