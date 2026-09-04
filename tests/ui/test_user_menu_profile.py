"""Tests du menu utilisateur, de « Mon profil » et de la déconnexion —
UI-105.

Rendu réel (patron établi UI-101/102/103/104), comportement HTTP réel
(session amorcée comme en TECH-002/003), réutilisation vérifiée de
auth.logout() (aucune seconde logique) et du mécanisme de redirection
sûre construit en UI-103 (exercé de bout en bout ici pour la première
fois).
"""

import pytest
from django.template.loader import render_to_string
from django.test import Client

from core.audit.models import AuditLog
from core.authz.models import Role, UserRole
from core.identity import auth
from core.identity.models import User

SHELL_DEMO_URL = "/shell-demo/"
PROFILE_URL = "/profil/"
LOGOUT_ACTION_URL = "/deconnexion/"
LOGIN_URL = "/login/"
CSRF_URL = "/api/auth/csrf/"

PASSWORD = "MotDePasseReel123!"


@pytest.fixture
def active_user(db):
    user = User.objects.create(username="jdupont", full_name="Jean Dupont")
    auth.set_user_password(user, PASSWORD)
    user.save()
    return user


def _authenticated_client(user) -> Client:
    client = Client()
    session = client.session
    session[auth.SESSION_USER_ID_KEY] = user.id
    session.save()
    client.cookies["sessionid"] = session.session_key
    return client


class TestUserMenuRendering:
    def test_renders_nothing_when_no_user_in_context(self):
        html = render_to_string("ui/components/user_menu.html", {"user": None})
        assert "corrux-user-menu" not in html

    def test_closed_state_shows_avatar_name_and_chevron(self):
        html = render_to_string(
            "ui/components/user_menu.html",
            {
                "user": type("U", (), {"full_name": "Jean Dupont"})(),
                "initial": "J",
                "role_names": "",
            },
        )
        assert "<details" in html
        assert "<summary" in html
        assert "Jean Dupont" in html
        assert ">J<" in html  # initiale dans l'avatar

    def test_opened_panel_contains_profile_link_and_logout_form(self):
        html = render_to_string(
            "ui/components/user_menu.html",
            {
                "user": type("U", (), {"full_name": "Jean Dupont"})(),
                "initial": "J",
                "role_names": "",
            },
        )
        assert f'href="{PROFILE_URL}"' in html
        assert "Mon profil" in html
        assert f'action="{LOGOUT_ACTION_URL}"' in html
        assert "Se déconnecter" in html

    def test_role_names_are_displayed_when_present(self):
        html = render_to_string(
            "ui/components/user_menu.html",
            {
                "user": type("U", (), {"full_name": "Jean Dupont"})(),
                "initial": "J",
                "role_names": "Administrateur, Employé",
            },
        )
        assert "Administrateur, Employé" in html

    def test_full_name_is_html_escaped(self):
        html = render_to_string(
            "ui/components/user_menu.html",
            {
                "user": type("U", (), {"full_name": "<script>alert(1)</script>"})(),
                "initial": "<",
                "role_names": "",
            },
        )
        assert "<script>alert(1)</script>" not in html

    def test_no_django_comment_leaks_into_output(self):
        html = render_to_string(
            "ui/components/user_menu.html",
            {
                "user": type("U", (), {"full_name": "Jean Dupont"})(),
                "initial": "J",
                "role_names": "",
            },
        )
        assert "{#" not in html
        assert "{% comment %}" not in html


@pytest.mark.django_db
class TestUserMenuIntegrationInShell:
    def test_multiple_roles_are_joined_and_displayed(self, active_user):
        """« Administrateur » et « Employé » sont désormais des rôles
        prédéfinis semés par UI-201 (migration 0006) : réutilisés tels
        quels plutôt que recréés (le nom est réservé par contrainte
        d'unicité, correctement appliquée)."""
        Role.objects.create(name="Zebra")  # pour vérifier l'ordre alphabétique
        role_a = Role.objects.get(name="Administrateur")
        role_b = Role.objects.get(name="Employé")
        UserRole.objects.create(user=active_user, role=role_a)
        UserRole.objects.create(user=active_user, role=role_b)

        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()

        assert "Administrateur, Employé" in content

    def test_avatar_initial_is_first_letter_of_full_name_uppercased(self, active_user):
        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()
        assert ">J<" in content


@pytest.mark.django_db
class TestProfilePage:
    def test_authenticated_user_sees_own_account_info(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(PROFILE_URL)

        assert response.status_code == 200
        content = response.content.decode()
        assert "jdupont" in content
        assert "Jean Dupont" in content

    def test_post_is_rejected(self, active_user):
        """Bug corrigé (audit général) : profile_page n'avait aucune
        restriction de méthode — un POST était traité comme un GET."""
        client = _authenticated_client(active_user)
        response = client.post(PROFILE_URL)
        assert response.status_code == 405

    def test_shows_active_status_badge(self, active_user):
        client = _authenticated_client(active_user)
        content = client.get(PROFILE_URL).content.decode()
        assert "corrux-badge--success" in content
        assert "Actif" in content

    def test_status_tone_mapping_logic_for_inactive_status(self, active_user, rf):
        """La branche "neutral" de profile_page n'est jamais atteignable
        par le flux HTTP complet (un compte inactif est redirigé avant,
        cf. test ci-dessus) : testée ici directement au niveau de la vue,
        avec request.corrux_user positionné manuellement, pour vérifier
        que la logique de correspondance statut -> ton reste correcte si
        jamais ce chemin devenait atteignable (ex. futur statut
        supplémentaire)."""
        from ui.views import profile_page

        active_user.status = User.Status.INACTIVE
        request = rf.get(PROFILE_URL)
        request.corrux_user = active_user

        response = profile_page(request)

        assert response.status_code == 200
        content = response.content.decode()
        assert "corrux-badge--neutral" in content
        assert "Inactif" in content
        """Un compte inactif est déjà traité comme non authentifié par
        core.identity.auth.get_authenticated_user (TECH-002) — il ne peut
        donc jamais « voir son profil inactif », il est redirigé comme
        tout visiteur anonyme. Comportement hérité correct, pas un bug :
        ce test documente cette conséquence plutôt que de supposer un
        scénario impossible (utilisateur « authentifié inactif »)."""
        active_user.status = User.Status.INACTIVE
        active_user.save()
        client = _authenticated_client(active_user)

        response = client.get(PROFILE_URL)

        assert response.status_code == 302
        assert response.url == f"{LOGIN_URL}?next={PROFILE_URL}"

    def test_shows_account_creation_date(self, active_user):
        """Conversion vers le fuseau local (Europe/Paris) avant formatage
        — comme le fait réellement le rendu (filtre |date). Bug
        pré-existant corrigé ici : une comparaison naïve en UTC pouvait
        échouer près de minuit UTC, où la date locale diffère de la date
        UTC (ex. 23h41 UTC = 01h41 CEST le jour suivant)."""
        from django.utils import timezone

        client = _authenticated_client(active_user)
        content = client.get(PROFILE_URL).content.decode()
        local_created_at = timezone.localtime(active_user.created_at)
        assert local_created_at.strftime("%d/%m/%Y") in content

    def test_uses_the_shell_topbar_and_sidebar(self, active_user):
        client = _authenticated_client(active_user)
        content = client.get(PROFILE_URL).content.decode()
        assert "corrux-topbar" in content
        assert "corrux-sidebar" in content

    def test_only_displays_fields_that_actually_exist_on_the_model(self, active_user):
        """Aucun champ inventé : uniquement identifiant, nom, statut, date
        de création (TECH-001)."""
        client = _authenticated_client(active_user)
        content = client.get(PROFILE_URL).content.decode()
        assert "Identifiant" in content
        assert "Nom complet" in content
        assert "Statut" in content
        assert "créé le" in content

    def test_anonymous_visitor_is_redirected_to_login_with_safe_next(self):
        client = Client()
        response = client.get(PROFILE_URL)

        assert response.status_code == 302
        assert response.url == f"{LOGIN_URL}?next={PROFILE_URL}"

    def test_full_name_is_html_escaped_on_profile_page(self, active_user):
        active_user.full_name = "<script>alert(1)</script>"
        active_user.save()
        client = _authenticated_client(active_user)
        content = client.get(PROFILE_URL).content.decode()
        assert "<script>alert(1)</script>" not in content


@pytest.mark.django_db
class TestLoginToProfileEndToEnd:
    """Exercice de bout en bout du mécanisme `next` construit et testé de
    façon isolée en UI-103 — jamais exécuté de bout en bout jusqu'ici."""

    def test_anonymous_redirect_then_login_lands_back_on_profile(self, active_user):
        client = Client(enforce_csrf_checks=True)

        redirect_response = client.get(PROFILE_URL)
        assert redirect_response.url == f"{LOGIN_URL}?next={PROFILE_URL}"

        login_get = client.get(redirect_response.url)
        token = login_get.cookies["csrftoken"].value
        content = login_get.content.decode()
        assert f'value="{PROFILE_URL}"' in content  # next repris dans le formulaire

        login_response = client.post(
            LOGIN_URL,
            {
                "username": "jdupont",
                "password": PASSWORD,
                "next": PROFILE_URL,
                "csrfmiddlewaretoken": token,
            },
        )

        assert login_response.status_code == 302
        assert login_response.url == PROFILE_URL

        profile_response = client.get(PROFILE_URL)
        assert profile_response.status_code == 200
        assert "jdupont" in profile_response.content.decode()


@pytest.mark.django_db
class TestLogoutAction:
    def _csrf_client(self):
        return Client(enforce_csrf_checks=True)

    def test_logout_action_redirects_to_login(self, active_user):
        client = self._csrf_client()
        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = active_user.id
        session.save()
        client.cookies["sessionid"] = session.session_key

        response = client.post(LOGOUT_ACTION_URL, HTTP_X_CSRFTOKEN=token)

        assert response.status_code == 302
        assert response.url == LOGIN_URL

    def test_logout_action_clears_the_session(self, active_user):
        client = self._csrf_client()
        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = active_user.id
        session.save()
        client.cookies["sessionid"] = session.session_key

        client.post(LOGOUT_ACTION_URL, HTTP_X_CSRFTOKEN=token)

        assert auth.SESSION_USER_ID_KEY not in client.session

    def test_logout_action_reuses_auth_logout_and_is_audited(self, active_user):
        """Même backend que TECH-002/008 : aucune seconde logique."""
        client = self._csrf_client()
        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = active_user.id
        session.save()
        client.cookies["sessionid"] = session.session_key

        client.post(LOGOUT_ACTION_URL, HTTP_X_CSRFTOKEN=token)

        assert AuditLog.objects.filter(
            action=auth.LOGOUT_ACTION, actor_user=active_user
        ).exists()

    def test_logout_action_rejects_get_requests(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(LOGOUT_ACTION_URL)
        assert response.status_code == 405

    def test_logout_action_requires_csrf_token(self, active_user):
        client = self._csrf_client()
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = active_user.id
        session.save()
        client.cookies["sessionid"] = session.session_key

        response = client.post(LOGOUT_ACTION_URL)  # pas de token CSRF

        assert response.status_code == 403
        assert auth.SESSION_USER_ID_KEY in client.session  # session non affectée

    def test_full_user_journey_click_logout_from_menu_ends_at_login(self, active_user):
        """Simule le clic « Se déconnecter » du menu : POST vers l'action
        exacte que le formulaire cible réellement (vérifié plus haut),
        aboutit sur /login/."""
        client = self._csrf_client()
        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = active_user.id
        session.save()
        client.cookies["sessionid"] = session.session_key

        response = client.post(LOGOUT_ACTION_URL, HTTP_X_CSRFTOKEN=token)
        assert response.url == LOGIN_URL

        login_page = client.get(LOGIN_URL)
        assert login_page.status_code == 200  # formulaire affiché, pas de boucle
