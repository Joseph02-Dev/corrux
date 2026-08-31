"""Configuration Django du processus applicatif unique `corrux-core`.

Référence : architecture-technique-v1.md §0, §1, §6, §7, §9.
Aucune valeur secrète n'est codée en dur ici : tout passe par des variables
d'environnement (cf. .env.example). En V1, seuls Documentation et RH sont
enregistrés comme apps métier ; les sous-paquets de `core/` (identity, authz,
storage, modules, backup) seront transformés en apps Django au fil des
tickets TECH-001 et suivants.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Sécurité / environnement ---------------------------------------------

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        # Clé de développement uniquement : ne jamais utiliser hors DEBUG.
        SECRET_KEY = "insecure-dev-key-do-not-use-in-production"
    else:
        raise RuntimeError(
            "DJANGO_SECRET_KEY doit être défini par variable d'environnement "
            "dès lors que DJANGO_DEBUG n'est pas activé."
        )

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# --- Applications -----------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # Platform Core (squelette — modèles ajoutés à partir de TECH-001)
    "core",
    # Modules métier V1 (squelettes vides, cf. INIT-001)
    "modules.documentation",
    "modules.rh",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "corrux_core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "corrux_core.wsgi.application"
ASGI_APPLICATION = "corrux_core.asgi.application"

# --- Base de données ---------------------------------------------------------
# Un seul connecteur PostgreSQL ; isolation logique par schéma (core /
# documentation / rh) obtenue en qualifiant explicitement `db_table` sur
# chaque modèle métier (ex. `"core"."users"`), conformément à §7 de
# architecture-technique-v1.md. Le search_path reste au défaut PostgreSQL
# (public) : les apps Django génériques (auth, admin, sessions,
# contenttypes) restent dans `public`, sans configuration particulière.
# L'isolation par rôle PostgreSQL dédié par module (défense en profondeur,
# §16) est traitée dans les tickets ultérieurs (packaging / ops), pas ici.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "corrux"),
        "USER": os.environ.get("DB_USER", "corrux"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentification ---------------------------------------------------------
# Argon2id en premier hasher, conformément à architecture-technique-v1.md §16.
# Nécessite le paquet `argon2-cffi` (cf. requirements.txt).

AUTH_PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# --- Internationalisation -----------------------------------------------------

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

# --- Fichiers statiques --------------------------------------------------------

STATIC_URL = "static/"
