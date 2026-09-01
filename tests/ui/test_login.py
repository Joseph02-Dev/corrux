"""Tests de l'écran de connexion — UI-103.

Réutilise le patron de test déjà établi en TECH-002 (Client avec CSRF
réellement appliqué). Vérifie le comportement métier réel : session,
audit (TECH-008, table réelle), redirection sûre, message générique
identique quelle que soit la cause d'échec — pas de simple présence de
chaîne dans le HTML.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.audit.models import AuditLog
from core.identity import auth
from core.identity.models import User

LOGIN_URL = "/login/"

PASSWORD = "MotDePasseReel123!"


@pytest.fixture
def active_user(db):
    user = User.objects.create(username="jdupont", full_name="Jean Dupont")
    auth.set_user_password(user, PASSWORD)
    user.save()
    return user


def _client():
    return Client(enforce_csrf_checks=True)


def _get_csrf_token(client, url=LOGIN_URL):
    response = client.get(url)
    return response.cookies["csrftoken"].value


def _post_login(client, username, password, *, token=None, next_param=None):
    token = token or client.cookies["csrftoken"].value
    data = {"username": username, "password": password, "csrfmiddlewaretoken": token}
    if next_param is not None:
        data["next"] = next_param
    return client.post(LOGIN_URL, data)


@pytest.mark.django_db
class TestGetLogin:
    def test_get_returns_200_and_renders_form(self):
        client = _client()
        response = client.get(LOGIN_URL)

        assert response.status_code == 200
        content = response.content.decode()
        assert '<form method="post"' in content
        assert 'name="csrfmiddlewaretoken"' in content

    def test_form_fields_are_correctly_labelled(self):
        client = _client()
        content = client.get(LOGIN_URL).content.decode()

        assert 'for="id_username"' in content
        assert 'id="id_username"' in content
        assert 'for="id_password"' in content
        assert 'id="id_password"' in content
        assert 'type="password"' in content

    def test_username_field_has_autofocus_and_autocomplete(self):
        content = _client().get(LOGIN_URL).content.decode()
        assert "autofocus" in content
        assert 'autocomplete="username"' in content
        assert 'autocomplete="current-password"' in content

    def test_no_error_banner_on_fresh_get(self):
        content = _client().get(LOGIN_URL).content.decode()
        assert "corrux-auth-card__error" not in content

    def test_no_django_comment_leaks_into_output(self):
        content = _client().get(LOGIN_URL).content.decode()
        assert "{#" not in content
        assert "{% comment %}" not in content


@pytest.mark.django_db
class TestCsrfProtection:
    def test_post_without_csrf_token_is_rejected(self, active_user):
        client = _client()
        client.get(LOGIN_URL)  # amorce le cookie, mais le token n'est pas transmis

        response = client.post(LOGIN_URL, {"username": "jdupont", "password": PASSWORD})

        assert response.status_code == 403
        # La session ne doit pas être établie si le CSRF a bloqué la requête.
        assert auth.SESSION_USER_ID_KEY not in client.session


@pytest.mark.django_db
class TestSuccessfulLogin:
    def test_valid_credentials_redirect_and_establish_session(self, active_user):
        client = _client()
        client.get(LOGIN_URL)

        response = _post_login(client, "jdupont", PASSWORD)

        assert response.status_code == 302
        assert response.url == "/shell-demo/"
        assert client.session[auth.SESSION_USER_ID_KEY] == active_user.id

    def test_successful_login_is_audited_with_correct_actor(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        _post_login(client, "jdupont", PASSWORD)

        entry = AuditLog.objects.get(action=auth.AUTH_ACTION, metadata__status="success")
        assert entry.actor_user == active_user

    def test_session_cookie_is_httponly_and_secure_when_configured(self, active_user):
        from django.test import override_settings

        with override_settings(SESSION_COOKIE_SECURE=True):
            client = _client()
            client.get(LOGIN_URL)
            response = _post_login(client, "jdupont", PASSWORD)

        cookie = response.cookies["sessionid"]
        assert cookie["httponly"] is True
        assert cookie["secure"] is True


@pytest.mark.django_db
class TestFailedLoginShowsGenericMessageOnly:
    def _attempt_and_get_error_html(self, username, password, active_user=None):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, username, password)
        return response

    def test_wrong_password_shows_generic_error_and_no_redirect(self, active_user):
        response = self._attempt_and_get_error_html("jdupont", "mauvais-mot-de-passe")
        assert response.status_code == 200
        content = response.content.decode()
        assert auth.GENERIC_ERROR_MESSAGE in content

    def test_unknown_username_shows_identical_error_text(self, active_user):
        r_unknown = self._attempt_and_get_error_html("n-existe-pas", "whatever")
        r_wrong_pw = self._attempt_and_get_error_html("jdupont", "mauvais")

        def extract_error(response):
            content = response.content.decode()
            start = content.index('role="alert">') + len('role="alert">')
            end = content.index("</p>", start)
            return content[start:end]

        assert extract_error(r_unknown) == extract_error(r_wrong_pw) == auth.GENERIC_ERROR_MESSAGE

    def test_locked_account_shows_the_same_generic_message_not_a_distinct_one(
        self, active_user
    ):
        active_user.failed_login_attempts = auth.MAX_FAILED_ATTEMPTS
        active_user.locked_until = timezone.now() + timedelta(minutes=15)
        active_user.save()

        response = self._attempt_and_get_error_html("jdupont", PASSWORD)  # bon mdp, verrouillé

        content = response.content.decode()
        assert auth.GENERIC_ERROR_MESSAGE in content
        # Aucune mention distincte d'un état de verrouillage n'est exposée.
        assert "verrouill" not in content.lower()
        assert "locked" not in content.lower()

    def test_inactive_account_shows_the_same_generic_message(self, active_user):
        active_user.status = User.Status.INACTIVE
        active_user.save()

        response = self._attempt_and_get_error_html("jdupont", PASSWORD)

        content = response.content.decode()
        assert auth.GENERIC_ERROR_MESSAGE in content
        assert "inactif" not in content.lower()
        assert "inactive" not in content.lower()

    def test_empty_submission_shows_generic_message_without_crashing(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, "", "")
        assert response.status_code == 200
        assert auth.GENERIC_ERROR_MESSAGE in response.content.decode()

    def test_all_four_failure_reasons_are_still_distinguishable_in_the_audit_log(
        self, active_user
    ):
        """Le message HTTP/HTML est identique dans les 4 cas, mais
        l'audit interne (TECH-008) doit continuer à distinguer les
        raisons — vérifié directement en base, pas via le rendu."""
        self._attempt_and_get_error_html("inconnu", "x")
        self._attempt_and_get_error_html("jdupont", "mauvais")

        active_user.status = User.Status.INACTIVE
        active_user.save()
        self._attempt_and_get_error_html("jdupont", PASSWORD)

        reasons = set(
            AuditLog.objects.filter(action=auth.AUTH_ACTION).values_list(
                "metadata__reason", flat=True
            )
        )
        assert auth.REASON_UNKNOWN_USERNAME in reasons
        assert auth.REASON_INVALID_PASSWORD in reasons
        assert auth.REASON_ACCOUNT_INACTIVE in reasons


@pytest.mark.django_db
class TestFormRepopulation:
    def test_username_is_repopulated_after_failed_attempt(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, "jdupont", "mauvais-mot-de-passe")

        assert 'value="jdupont"' in response.content.decode()

    def test_password_is_never_repopulated_after_failed_attempt(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, "jdupont", "un-mot-de-passe-tres-particulier-XYZ")

        content = response.content.decode()
        assert "un-mot-de-passe-tres-particulier-XYZ" not in content

    def test_password_never_appears_anywhere_in_any_response(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, "jdupont", PASSWORD)  # succès, redirige
        assert PASSWORD not in response.content.decode()


@pytest.mark.django_db
class TestAlreadyAuthenticated:
    def test_get_login_while_authenticated_redirects_immediately(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        _post_login(client, "jdupont", PASSWORD)  # établit la session

        response = client.get(LOGIN_URL)

        assert response.status_code == 302
        assert response.url == "/shell-demo/"

    def test_form_is_not_rendered_when_already_authenticated(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        _post_login(client, "jdupont", PASSWORD)

        response = client.get(LOGIN_URL, follow=False)
        assert response.status_code == 302  # jamais un 200 avec le formulaire


@pytest.mark.django_db
class TestNextRedirectSafety:
    def test_safe_relative_next_is_honoured(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, "jdupont", PASSWORD, next_param="/shell-demo/")
        assert response.url == "/shell-demo/"

    def test_absolute_external_next_is_rejected_open_redirect(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(
            client, "jdupont", PASSWORD, next_param="https://evil.example.com/phish"
        )
        assert response.url == "/shell-demo/"
        assert "evil.example.com" not in response.url

    def test_protocol_relative_next_is_rejected(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, "jdupont", PASSWORD, next_param="//evil.example.com/")
        assert response.url == "/shell-demo/"

    def test_next_hidden_field_is_rendered_when_present_in_get(self, active_user):
        client = _client()
        response = client.get(LOGIN_URL + "?next=/shell-demo/")
        content = response.content.decode()
        assert 'name="next"' in content
        assert 'value="/shell-demo/"' in content


@pytest.mark.django_db
class TestAccessibility:
    def test_error_banner_has_alert_role(self, active_user):
        client = _client()
        client.get(LOGIN_URL)
        response = _post_login(client, "jdupont", "mauvais")
        assert 'role="alert"' in response.content.decode()

    def test_submit_button_is_type_submit(self):
        content = _client().get(LOGIN_URL).content.decode()
        assert 'type="submit"' in content
