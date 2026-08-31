"""Point d'entrée WSGI de corrux-core."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "corrux_core.settings")

application = get_wsgi_application()
