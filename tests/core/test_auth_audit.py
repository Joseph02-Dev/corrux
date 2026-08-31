"""Tests d'instrumentation d'audit de l'authentification — TECH-008.

Cf. architecture-technique-v1.md §16. Réutilise strictement
core.audit_log et record_audit_event (TECH-006/TECH-009) — aucun
deuxième mécanisme d'audit. Vérifie l'état réel en base (pas seulement
les valeurs de retour), et la non-régression de l'anti-bruteforce
(TECH-002).
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.audit.models import AuditLog
from core.identity import auth
from core.identity.models import User

LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"
CSRF_URL = "/api/auth/csrf/"

SECRET_PASSWORD = "MonMotDePasseTresSecret123!"


@pytest.fixture
def active_user(db):
    user = User.objects.create(
        username="jdupont", full_name="Jean Dupont", status=User.Status.ACTIVE
    )
    auth.set_user_password(user, SECRET_PASSWORD)
    user.save()
    return user


def _csrf_client():
    return Client(enforce_csrf_checks=True)


def _login_with_csrf(client, username, password):
    client.get(CSRF_URL)
    token = client.cookies["csrftoken"].value
    return client.post(
        LOGIN_URL,
        {"username": username, "password": password},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )


def _logout_with_csrf(client):
    client.get(CSRF_URL)
    token = client.cookies["csrftoken"].value
    return client.post(LOGOUT_URL, content_type="application/json", HTTP_X_CSRFTOKEN=token)


@pytest.mark.django_db
class TestSuccessfulLoginAudit:
    def test_successful_login_creates_audit_entry(self, active_user):
        auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)

        entry = AuditLog.objects.get(action=auth.AUTH_ACTION, metadata__status="success")
        assert entry.actor_user == active_user
        assert entry.target == "jdupont"

    def test_actor_is_correctly_identified_on_success(self, active_user):
        auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)

        entry = AuditLog.objects.get(action=auth.AUTH_ACTION, metadata__status="success")
        assert entry.actor_user is not None
        assert entry.actor_user.pk == active_user.pk


@pytest.mark.django_db
class TestFailedLoginAudit:
    def test_unknown_username_creates_audit_entry_with_actor_none(self):
        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="n-existe-pas", raw_password="whatever")

        entry = AuditLog.objects.get(
            action=auth.AUTH_ACTION, metadata__reason=auth.REASON_UNKNOWN_USERNAME
        )
        assert entry.actor_user is None
        assert entry.target == "n-existe-pas"
        assert entry.metadata["status"] == "failure"

    def test_wrong_password_creates_audit_entry_with_correct_reason(self, active_user):
        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="jdupont", raw_password="mauvais-mot-de-passe")

        entry = AuditLog.objects.get(
            action=auth.AUTH_ACTION, metadata__reason=auth.REASON_INVALID_PASSWORD
        )
        assert entry.actor_user == active_user

    def test_locked_account_creates_audit_entry_with_correct_reason(self, active_user):
        active_user.failed_login_attempts = auth.MAX_FAILED_ATTEMPTS
        active_user.locked_until = timezone.now() + timedelta(minutes=15)
        active_user.save()

        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)

        entry = AuditLog.objects.get(
            action=auth.AUTH_ACTION, metadata__reason=auth.REASON_ACCOUNT_LOCKED
        )
        assert entry.actor_user == active_user

    def test_inactive_account_creates_audit_entry_with_correct_reason(self, active_user):
        active_user.status = User.Status.INACTIVE
        active_user.save()

        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)

        entry = AuditLog.objects.get(
            action=auth.AUTH_ACTION, metadata__reason=auth.REASON_ACCOUNT_INACTIVE
        )
        assert entry.actor_user == active_user

    def test_failure_reasons_are_distinguishable_from_each_other(self, active_user):
        """Les 3 causes d'échec produisent des entrées distinctes en base,
        même si le message HTTP renvoyé au client reste identique."""
        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="jdupont", raw_password="mauvais")
        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="inconnu", raw_password="whatever")

        reasons = set(
            AuditLog.objects.filter(action=auth.AUTH_ACTION).values_list(
                "metadata__reason", flat=True
            )
        )
        assert auth.REASON_INVALID_PASSWORD in reasons
        assert auth.REASON_UNKNOWN_USERNAME in reasons

    def test_http_error_message_stays_generic_regardless_of_audited_reason(self, active_user):
        """Le journal distingue les causes en interne, mais le client ne
        voit jamais que le message générique (propriété TECH-002 préservée)."""
        client = _csrf_client()
        response_unknown = _login_with_csrf(client, "inconnu", "whatever")
        response_wrong_pw = _login_with_csrf(_csrf_client(), "jdupont", "mauvais")

        assert response_unknown.json()["detail"] == response_wrong_pw.json()["detail"]
        assert "unknown_username" not in response_unknown.content.decode()
        assert "invalid_password" not in response_wrong_pw.content.decode()


@pytest.mark.django_db
class TestLogoutAudit:
    def test_logout_of_authenticated_session_creates_audit_entry(self, active_user):
        client = _csrf_client()
        _login_with_csrf(client, "jdupont", SECRET_PASSWORD)

        _logout_with_csrf(client)

        entry = AuditLog.objects.get(action=auth.LOGOUT_ACTION)
        assert entry.actor_user == active_user
        assert entry.metadata["status"] == "success"

    def test_logout_without_active_session_creates_no_audit_entry(self):
        client = _csrf_client()
        _logout_with_csrf(client)  # jamais connecté

        assert not AuditLog.objects.filter(action=auth.LOGOUT_ACTION).exists()


@pytest.mark.django_db
class TestNoSecretsInAuditMetadata:
    def test_password_never_appears_in_any_audit_entry(self, active_user):
        auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)  # succès
        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="jdupont", raw_password="mauvais-" + SECRET_PASSWORD)

        for entry in AuditLog.objects.all():
            assert SECRET_PASSWORD not in str(entry.metadata)
            assert SECRET_PASSWORD not in entry.target
            assert "mauvais-" + SECRET_PASSWORD not in str(entry.metadata)

    def test_password_hash_never_appears_in_audit_entries(self, active_user):
        auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)

        for entry in AuditLog.objects.all():
            assert active_user.password_hash not in str(entry.metadata)


@pytest.mark.django_db
class TestAuditSurvivesEvenWhenBusinessOperationFails:
    def test_failed_login_audit_entry_persists_despite_the_raised_exception(self, active_user):
        """L'audit d'un échec doit exister en base même si l'appelant ne
        rattrape jamais l'exception jusqu'au bout (aucune transaction
        ambiante ne peut l'annuler, cf. analyse TECH-008)."""
        try:
            auth.authenticate(username="jdupont", raw_password="mauvais")
        except auth.AuthenticationError:
            pass  # simule un appelant qui échoue ensuite pour une autre raison

        assert AuditLog.objects.filter(
            action=auth.AUTH_ACTION, metadata__reason=auth.REASON_INVALID_PASSWORD
        ).exists()

    def test_audit_entry_for_no_matching_account_has_no_dangling_reference(self):
        """actor_user=None : aucune ligne User n'existe pour ce cas, la
        contrainte de clé étrangère nullable le permet correctement."""
        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="fantome", raw_password="x")

        entry = AuditLog.objects.get(metadata__reason=auth.REASON_UNKNOWN_USERNAME)
        assert entry.actor_user_id is None


@pytest.mark.django_db
class TestOversizedUsernameDoesNotBreakAudit:
    def test_extremely_long_username_does_not_crash_login(self):
        long_username = "x" * 10_000
        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username=long_username, raw_password="whatever")

        entry = AuditLog.objects.get(metadata__reason=auth.REASON_UNKNOWN_USERNAME)
        assert len(entry.target) <= 255


@pytest.mark.django_db
class TestBruteForceRegression:
    """Non-régression explicite : l'instrumentation d'audit ne doit rien
    changer au comportement anti-bruteforce déjà validé en TECH-002."""

    def test_account_still_locks_after_max_failed_attempts(self, active_user):
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            with pytest.raises(auth.AuthenticationError):
                auth.authenticate(username="jdupont", raw_password="mauvais")

        active_user.refresh_from_db()
        assert active_user.failed_login_attempts == auth.MAX_FAILED_ATTEMPTS
        assert active_user.locked_until is not None

    def test_locked_account_attempt_does_not_further_increment_counter(self, active_user):
        active_user.failed_login_attempts = auth.MAX_FAILED_ATTEMPTS
        active_user.locked_until = timezone.now() + timedelta(minutes=15)
        active_user.save()

        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)

        active_user.refresh_from_db()
        assert active_user.failed_login_attempts == auth.MAX_FAILED_ATTEMPTS  # inchangé

    def test_successful_login_still_resets_counter(self, active_user):
        active_user.failed_login_attempts = 2
        active_user.save()

        auth.authenticate(username="jdupont", raw_password=SECRET_PASSWORD)

        active_user.refresh_from_db()
        assert active_user.failed_login_attempts == 0
        assert active_user.locked_until is None
