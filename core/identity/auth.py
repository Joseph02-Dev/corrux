"""Authentification par sessions serveur — TECH-002, instrumentée par TECH-008.

Cf. architecture-technique-v1.md §9 (sessions serveur, cookies HTTPOnly +
Secure, argon2id), §16 (protection anti-bruteforce, journalisation des
connexions/échecs de connexion). Aucun moteur RBAC ici (TECH-003) : ce
module se limite à identifier l'utilisateur, ouvrir/fermer sa session, et
journaliser ces événements.

Audit (TECH-008) : les écritures d'audit se font ici, pas dans les vues —
même choix architectural que TECH-006/TECH-009 (l'audit vit avec la
logique métier qu'il protège). Aucune écriture ici n'est enveloppée dans
`transaction.atomic()` (confirmé : `ATOMIC_REQUESTS` n'est pas activé) :
chaque `record_audit_event()` s'auto-committe indépendamment, donc
survit nécessairement à tout échec ultérieur — pas de risque de rollback
à gérer pour ce module, à la différence de TECH-006/009 qui, eux,
protégeaient une véritable transaction multi-étapes.

Le journal d'audit peut distinguer précisément la cause d'un échec
(identifiant inconnu / mot de passe incorrect / compte verrouillé /
compte inactif) : ce niveau de détail est réservé au journal interne
(consultable par un administrateur) et ne fuite jamais vers le message
d'erreur renvoyé au client, qui reste volontairement générique et
identique dans tous les cas (propriété de sécurité TECH-002 inchangée).
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from core.audit.service import record_audit_event
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

AUTH_ACTION = "auth.login"
LOGOUT_ACTION = "auth.logout"

# Raisons d'échec — usage interne (audit) uniquement, jamais exposées au
# client (cf. GENERIC_ERROR_MESSAGE).
REASON_UNKNOWN_USERNAME = "unknown_username"
REASON_INVALID_PASSWORD = "invalid_password"
REASON_ACCOUNT_LOCKED = "account_locked"
REASON_ACCOUNT_INACTIVE = "account_inactive"

# Limite défensive : un identifiant soumis par le client n'est jamais
# validé contre User.username (max_length=150) avant d'être utilisé comme
# `target` d'audit (CharField(255), contrainte stricte PostgreSQL) — un
# identifiant anormalement long ferait échouer l'écriture d'audit
# elle-même (déni de service trivial sur le mécanisme d'audit). Tronqué
# avant toute écriture.
_MAX_AUDIT_TARGET_LENGTH = 255


class AuthenticationError(Exception):
    """Échec de login. Le message ne doit jamais varier selon la cause."""


def _audit_target(username: str) -> str:
    return username[:_MAX_AUDIT_TARGET_LENGTH]


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


def _audit_failure(*, actor: User | None, username: str, reason: str) -> None:
    record_audit_event(
        actor=actor,
        action=AUTH_ACTION,
        target=_audit_target(username),
        metadata={"status": "failure", "reason": reason},
    )


def authenticate(*, username: str, raw_password: str) -> User:
    """Vérifie les identifiants et applique la protection anti-bruteforce.

    Lève AuthenticationError (message générique, identique) pour : compte
    inexistant, mot de passe incorrect, compte inactif, compte
    temporairement verrouillé. Chaque cas est journalisé dans
    core.audit_log avec sa raison précise (usage interne uniquement) ;
    un succès est également journalisé avant de retourner l'utilisateur.
    """
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        check_password(raw_password, _DUMMY_HASH)  # temps de réponse constant
        _audit_failure(actor=None, username=username, reason=REASON_UNKNOWN_USERNAME)
        raise AuthenticationError(GENERIC_ERROR_MESSAGE) from None

    if _is_locked(user):
        _audit_failure(actor=user, username=username, reason=REASON_ACCOUNT_LOCKED)
        raise AuthenticationError(GENERIC_ERROR_MESSAGE)

    password_ok = check_password(raw_password, user.password_hash)
    if not password_ok:
        _register_failed_attempt(user)
        _audit_failure(actor=user, username=username, reason=REASON_INVALID_PASSWORD)
        raise AuthenticationError(GENERIC_ERROR_MESSAGE)

    if user.status != User.Status.ACTIVE:
        _register_failed_attempt(user)
        _audit_failure(actor=user, username=username, reason=REASON_ACCOUNT_INACTIVE)
        raise AuthenticationError(GENERIC_ERROR_MESSAGE)

    _register_successful_attempt(user)
    record_audit_event(
        actor=user,
        action=AUTH_ACTION,
        target=_audit_target(username),
        metadata={"status": "success"},
    )
    return user


def login(request, user: User) -> None:
    """Ouvre une session serveur pour l'utilisateur authentifié.

    cycle_key() régénère l'identifiant de session avant d'y stocker
    l'utilisateur (protection contre la fixation de session).
    """
    request.session.cycle_key()
    request.session[SESSION_USER_ID_KEY] = user.id


def logout(request) -> None:
    """Invalide la session serveur — empêche toute réutilisation ultérieure.

    Journalise la déconnexion uniquement si une session authentifiée
    existait réellement (un logout idempotent sur une session déjà
    anonyme ne produit pas de bruit d'audit inutile).
    """
    user = get_authenticated_user(request)
    request.session.flush()
    if user is not None:
        record_audit_event(
            actor=user,
            action=LOGOUT_ACTION,
            target=_audit_target(user.username),
            metadata={"status": "success"},
        )


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
