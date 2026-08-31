"""Tests d'authentification — TECH-002.

Couvre : login/logout, session serveur, cookie sécurisé, non-divulgation
de la cause d'échec, et protection anti-bruteforce réellement testée.
Aucun test RBAC (TECH-003).
"""

from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from core.identity import auth
from core.identity.models import User

LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"
CSRF_URL = "/api/auth/csrf/"


@pytest.fixture
def active_user(db):
    user = User.objects.create(
        username="jdupont", full_name="Jean Dupont", status=User.Status.ACTIVE
    )
    auth.set_user_password(user, "CorrectHorseBatteryStaple1!")
    user.save()
    return user


@pytest.fixture
def inactive_user(db):
    user = User.objects.create(
        username="disabled", full_name="Compte désactivé", status=User.Status.INACTIVE
    )
    auth.set_user_password(user, "CorrectHorseBatteryStaple1!")
    user.save()
    return user


def _csrf_client():
    """Client Django avec CSRF réellement appliqué (pas le comportement
    permissif par défaut du test client)."""
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


@pytest.mark.django_db
class TestPasswordHashing:
    def test_password_is_hashed_with_argon2id(self, active_user):
        assert active_user.password_hash.startswith("argon2$argon2id$")

    def test_raw_password_is_never_stored(self, active_user):
        assert "CorrectHorseBatteryStaple1!" not in active_user.password_hash


@pytest.mark.django_db
class TestLogin:
    def test_successful_login_returns_200_and_creates_session(self, active_user):
        client = _csrf_client()
        response = _login_with_csrf(client, "jdupont", "CorrectHorseBatteryStaple1!")
        assert response.status_code == 200
        assert response.json()["username"] == "jdupont"
        assert auth.SESSION_USER_ID_KEY in client.session

    def test_wrong_password_returns_generic_401(self, active_user):
        client = _csrf_client()
        response = _login_with_csrf(client, "jdupont", "wrong-password")
        assert response.status_code == 401
        assert response.json()["detail"] == auth.GENERIC_ERROR_MESSAGE

    def test_unknown_username_returns_same_generic_401(self, active_user):
        """Même statut et même message que mot de passe incorrect : pas
        d'énumération possible des comptes existants."""
        client = _csrf_client()
        response_unknown = _login_with_csrf(client, "does-not-exist", "whatever")
        response_wrong_pw = _login_with_csrf(
            _csrf_client(), "jdupont", "wrong-password"
        )
        assert response_unknown.status_code == response_wrong_pw.status_code == 401
        assert (
            response_unknown.json()["detail"]
            == response_wrong_pw.json()["detail"]
            == auth.GENERIC_ERROR_MESSAGE
        )

    def test_inactive_account_cannot_login(self, inactive_user):
        client = _csrf_client()
        response = _login_with_csrf(
            client, "disabled", "CorrectHorseBatteryStaple1!"
        )
        assert response.status_code == 401
        assert response.json()["detail"] == auth.GENERIC_ERROR_MESSAGE
        assert auth.SESSION_USER_ID_KEY not in client.session

    def test_login_requires_csrf_token(self, active_user):
        client = _csrf_client()
        # Pas d'appel préalable à /csrf/ : aucun token fourni.
        response = client.post(
            LOGIN_URL,
            {"username": "jdupont", "password": "CorrectHorseBatteryStaple1!"},
        )
        assert response.status_code == 403

    def test_login_sets_httponly_and_secure_cookie_flags(self, active_user):
        with override_settings(SESSION_COOKIE_SECURE=True):
            client = _csrf_client()
            response = _login_with_csrf(
                client, "jdupont", "CorrectHorseBatteryStaple1!"
            )
        session_cookie = response.cookies["sessionid"]
        assert session_cookie["httponly"] is True
        assert session_cookie["secure"] is True


@pytest.mark.django_db
class TestLogout:
    def test_logout_invalidates_session(self, active_user):
        client = _csrf_client()
        _login_with_csrf(client, "jdupont", "CorrectHorseBatteryStaple1!")
        assert auth.SESSION_USER_ID_KEY in client.session

        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        response = client.post(
            LOGOUT_URL, content_type="application/json", HTTP_X_CSRFTOKEN=token
        )
        assert response.status_code == 200
        assert auth.SESSION_USER_ID_KEY not in client.session

    def test_logout_is_idempotent_when_not_logged_in(self):
        client = _csrf_client()
        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        response = client.post(
            LOGOUT_URL, content_type="application/json", HTTP_X_CSRFTOKEN=token
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestBruteForceProtection:
    def test_account_locks_after_max_failed_attempts(self, active_user):
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            with pytest.raises(auth.AuthenticationError):
                auth.authenticate(username="jdupont", raw_password="wrong")

        active_user.refresh_from_db()
        assert active_user.failed_login_attempts == auth.MAX_FAILED_ATTEMPTS
        assert active_user.locked_until is not None
        assert active_user.locked_until > timezone.now()

    def test_correct_password_rejected_while_locked(self, active_user):
        active_user.failed_login_attempts = auth.MAX_FAILED_ATTEMPTS
        active_user.locked_until = timezone.now() + timedelta(minutes=15)
        active_user.save()

        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(
                username="jdupont", raw_password="CorrectHorseBatteryStaple1!"
            )

    def test_login_succeeds_again_after_lockout_expires(self, active_user):
        active_user.failed_login_attempts = auth.MAX_FAILED_ATTEMPTS
        active_user.locked_until = timezone.now() - timedelta(seconds=1)  # expiré
        active_user.save()

        user = auth.authenticate(
            username="jdupont", raw_password="CorrectHorseBatteryStaple1!"
        )
        assert user.pk == active_user.pk

    def test_successful_login_resets_failed_attempts_counter(self, active_user):
        active_user.failed_login_attempts = 2
        active_user.save()

        auth.authenticate(
            username="jdupont", raw_password="CorrectHorseBatteryStaple1!"
        )

        active_user.refresh_from_db()
        assert active_user.failed_login_attempts == 0
        assert active_user.locked_until is None

    def test_endpoint_reflects_lockout_with_same_generic_message(self, active_user):
        client = _csrf_client()
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            _login_with_csrf(_csrf_client(), "jdupont", "wrong")

        response = _login_with_csrf(
            client, "jdupont", "CorrectHorseBatteryStaple1!"
        )
        assert response.status_code == 401
        assert response.json()["detail"] == auth.GENERIC_ERROR_MESSAGE


@pytest.mark.django_db
class TestCorruxAuthenticationMiddleware:
    def test_request_corrux_user_is_none_when_anonymous(self, client):
        response = client.get("/healthz/")
        assert response.status_code == 200  # la route reste accessible

    def test_get_authenticated_user_resolves_session(self, rf, active_user):
        from django.contrib.sessions.middleware import SessionMiddleware

        request = rf.get("/")
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.session[auth.SESSION_USER_ID_KEY] = active_user.id

        resolved = auth.get_authenticated_user(request)
        assert resolved is not None
        assert resolved.pk == active_user.pk

    def test_get_authenticated_user_returns_none_for_inactive_account(
        self, rf, inactive_user
    ):
        from django.contrib.sessions.middleware import SessionMiddleware

        request = rf.get("/")
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.session[auth.SESSION_USER_ID_KEY] = inactive_user.id

        assert auth.get_authenticated_user(request) is None
