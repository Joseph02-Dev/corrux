"""Authentification par sessions serveur — TECH-002.

Cf. architecture-technique-v1.md §9 (sessions serveur, cookies HTTPOnly +
Secure, argon2id) et §16 (protection anti-bruteforce). Aucun moteur RBAC
ici (TECH-003) : ce module se limite à identifier l'utilisateur et ouvrir/
fermer sa session.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from core.identity.models import User

SESSION_USER_ID_KEY = "corrux_user_id"

# Non spécifiés par l'architecture (qui exige seulement une protection
# « réellement fonctionnelle ») : valeurs par défaut raisonnables,
# surchargeables via settings si besoin ultérieur (ex. corrux-setup).
MAX_FAILED_ATTEMPTS = getattr(settings, "CORRUX_MAX_FAILED_LOGIN_ATTEMPTS", 5)
LOCKOUT_DURATION = timedelta(
    minutes=getattr(settings, "CORRUX_LOGIN_LOCKOUT_MINUTES", 15)
)

# Ne révèle jamais si l'identifiant existe, si le mot de passe est
# incorrect, ou si le compte est inactif/verrouillé (même message dans
# tous les cas, cf. critère d'acceptation TECH-002).
GENERIC_ERROR_MESSAGE = "Identifiant ou mot de passe incorrect."

# Hash de référence utilisé uniquement pour égaliser le temps de calcul
# entre "compte inexistant" et "mot de passe incorrect" (limite, sans la
# garantir totalement, la fuite d'information par mesure de temps de
# réponse).
_DUMMY_HASH = make_password("corrux-constant-time-placeholder")


class AuthenticationError(Exception):
    """Échec de login. Le message ne doit jamais varier selon la cause."""


def set_user_password(user: User, raw_password: str) -> None:
    """Hash le mot de passe (Argon2id, cf. AUTH_PASSWORD_HASHERS) et l'assigne."""
    user.password_hash = make_password(raw_password)


def _is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > timezone.now())


def _register_failed_attempt(user: User) -> None:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
        user.locked_until = timezone.now() + LOCKOUT_DURATION
    user.save(update_fields=["failed_login_attempts", "locked_until"])


def _register_successful_attempt(user: User) -> None:
    if user.failed_login_attempts or user.locked_until:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_attempts", "locked_until"])


def authenticate(*, username: str, raw_password: str) -> User:
    """Vérifie les identifiants et applique la protection anti-bruteforce.

    Lève AuthenticationError (message générique, identique) pour : compte
    inexistant, mot de passe incorrect, compte inactif, compte
    temporairement verrouillé.
    """
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        check_password(raw_password, _DUMMY_HASH)  # temps de réponse constant
        raise AuthenticationError(GENERIC_ERROR_MESSAGE) from None

    if _is_locked(user):
        raise AuthenticationError(GENERIC_ERROR_MESSAGE)

    password_ok = check_password(raw_password, user.password_hash)
    if not password_ok or user.status != User.Status.ACTIVE:
        _register_failed_attempt(user)
        raise AuthenticationError(GENERIC_ERROR_MESSAGE)

    _register_successful_attempt(user)
    return user


def login(request, user: User) -> None:
    """Ouvre une session serveur pour l'utilisateur authentifié.

    cycle_key() régénère l'identifiant de session avant d'y stocker
    l'utilisateur (protection contre la fixation de session).
    """
    request.session.cycle_key()
    request.session[SESSION_USER_ID_KEY] = user.id


def logout(request) -> None:
    """Invalide la session serveur — empêche toute réutilisation ultérieure."""
    request.session.flush()


def get_authenticated_user(request) -> User | None:
    """Résout l'utilisateur CORRUX courant depuis la session, ou None."""
    user_id = request.session.get(SESSION_USER_ID_KEY)
    if user_id is None:
        return None
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        request.session.flush()
        return None
    if user.status != User.Status.ACTIVE:
        request.session.flush()
        return None
    return user
