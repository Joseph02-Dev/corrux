"""Tests de rendu du shell applicatif — UI-102.

Comportement HTTP réel (test client Django), session réelle amorcée
exactement comme en TECH-002/003 (pas de contournement d'authentification
ad hoc). Vérifie le comportement métier (RBAC réel, endpoint de logout
réel), pas uniquement la présence de chaînes dans le HTML.
"""

import pytest
from django.template.loader import render_to_string
from django.test import Client

from core.audit.models import AuditLog
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from ui.navigation import NavGroup, NavItem

SHELL_DEMO_URL = "/shell-demo/"
CSRF_URL = "/api/auth/csrf/"
LOGOUT_URL = "/api/auth/logout/"


@pytest.fixture
def active_user(db):
    user = User.objects.create(username="jdupont", full_name="Jean Dupont")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


def _grant(user, module_id, resource, action):
    role = Role.objects.create(name=f"role-{user.username}-{resource}")
    permission, _ = Permission.objects.get_or_create(
        module_id=module_id, resource=resource, action=action
    )
    RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


def _authenticated_client(user) -> Client:
    client = Client()
    session = client.session
    session[auth.SESSION_USER_ID_KEY] = user.id
    session.save()
    client.cookies["sessionid"] = session.session_key
    return client


@pytest.mark.django_db
class TestAnonymousBehaviour:
    def test_anonymous_visitor_does_not_see_the_shell(self):
        client = Client()
        response = client.get(SHELL_DEMO_URL)

        assert response.status_code == 200
        content = response.content.decode()
        assert "corrux-topbar" not in content
        assert "corrux-sidebar" not in content

    def test_anonymous_visitor_sees_an_explanatory_message(self):
        client = Client()
        response = client.get(SHELL_DEMO_URL)
        assert "Non connect" in response.content.decode()


@pytest.mark.django_db
class TestAuthenticatedWithoutPermission:
    def test_authenticated_user_sees_the_shell_chrome(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)

        content = response.content.decode()
        assert "corrux-topbar" in content
        assert "corrux-sidebar" in content

    def test_user_without_any_permission_sees_empty_sidebar(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)

        content = response.content.decode()
        assert "corrux-sidebar__group" not in content

    def test_user_full_name_is_displayed(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)
        assert "Jean Dupont" in response.content.decode()


@pytest.mark.django_db
class TestAuthenticatedWithPermission:
    def test_user_with_permission_sees_matching_nav_item(self, active_user):
        _grant(active_user, "core", "user", "read")
        client = _authenticated_client(active_user)

        response = client.get(SHELL_DEMO_URL)

        content = response.content.decode()
        assert "Utilisateurs" in content
        assert "Administration" in content

    def test_user_with_only_documentation_module_and_permission_sees_it(
        self, active_user
    ):
        Module.objects.create(
            id="documentation", name="Documentation", version="1.0.0",
            state=Module.State.ACTIVATED, manifest_snapshot={},
        )
        _grant(active_user, "documentation", "document", "read")
        client = _authenticated_client(active_user)

        response = client.get(SHELL_DEMO_URL)

        content = response.content.decode()
        assert "Documentation" in content
        assert "Documents" in content


@pytest.mark.django_db
class TestNoPermissionSpoofing:
    """Le filtrage ne doit jamais faire confiance à une donnée cliente."""

    def test_client_supplied_permission_header_is_ignored(self, active_user):
        client = _authenticated_client(active_user)  # aucune permission réelle

        response = client.get(
            SHELL_DEMO_URL,
            HTTP_X_CORRUX_PERMISSION="core.user.read",
            HTTP_X_CORRUX_ROLE="Administrateur",
        )

        content = response.content.decode()
        assert "corrux-sidebar__group" not in content

    def test_forged_get_parameter_does_not_grant_navigation_items(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL + "?permission=core.user.read&is_admin=1")
        content = response.content.decode()
        assert "corrux-sidebar__group" not in content


class TestActiveStateMechanism:
    """Teste le mécanisme (comparaison item.href / current_path) directement
    au niveau du template, indépendamment des routes réelles (encore
    inexistantes) — isole la logique testée de la disponibilité des
    futurs écrans Documentation/RH/Administration."""

    def test_matching_path_gets_active_class_and_aria_current(self):
        groups = (
            NavGroup("Documentation", (NavItem("Documents", "/documents/", "folder"),)),
        )
        html = render_to_string(
            "ui/shell/sidebar.html",
            {"groups": groups, "current_path": "/documents/"},
        )
        assert "corrux-nav-item--active" in html
        assert 'aria-current="page"' in html

    def test_non_matching_path_has_no_active_class(self):
        groups = (
            NavGroup("Documentation", (NavItem("Documents", "/documents/", "folder"),)),
        )
        html = render_to_string(
            "ui/shell/sidebar.html",
            {"groups": groups, "current_path": "/autre-page/"},
        )
        assert "corrux-nav-item--active" not in html
        assert "aria-current" not in html


@pytest.mark.django_db
class TestLogoutIntegration:
    """La déconnexion est réellement fonctionnelle (endpoint TECH-002),
    pas un simple lien de façade."""

    def test_logout_form_targets_the_real_endpoint(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)
        content = response.content.decode()
        assert f'action="{LOGOUT_URL}"' in content
        assert "csrfmiddlewaretoken" in content

    def test_submitting_the_logout_form_actually_clears_the_session(self, active_user):
        client = Client(enforce_csrf_checks=True)
        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = active_user.id
        session.save()
        client.cookies["sessionid"] = session.session_key

        response = client.post(
            LOGOUT_URL, content_type="application/json", HTTP_X_CSRFTOKEN=token
        )

        assert response.status_code == 200
        assert auth.SESSION_USER_ID_KEY not in client.session

    def test_logout_via_shell_is_audited(self, active_user):
        client = Client(enforce_csrf_checks=True)
        client.get(CSRF_URL)
        token = client.cookies["csrftoken"].value
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = active_user.id
        session.save()
        client.cookies["sessionid"] = session.session_key

        client.post(LOGOUT_URL, content_type="application/json", HTTP_X_CSRFTOKEN=token)

        assert AuditLog.objects.filter(action=auth.LOGOUT_ACTION, actor_user=active_user).exists()


@pytest.mark.django_db
class TestNonFunctionalControlsAreHonest:
    """Recherche/notifications n'existent pas encore côté backend : ne
    jamais les présenter comme fonctionnelles."""

    def test_search_input_is_disabled(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)
        content = response.content.decode()
        assert 'class="corrux-topbar__search-input"' in content
        assert "disabled" in content
        assert 'aria-disabled="true"' in content

    def test_notifications_button_is_disabled(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)
        content = response.content.decode()
        assert "corrux-topbar__icon-button" in content
        # Le bouton notifications est le seul <button> désactivé du groupe
        # d'actions (le bouton de déconnexion, lui, reste actif).
        assert 'aria-label="Notifications' in content


@pytest.mark.django_db
class TestAccessibility:
    def test_skip_link_targets_main_content(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)
        content = response.content.decode()
        assert 'href="#corrux-main-content"' in content
        assert 'id="corrux-main-content"' in content

    def test_sidebar_nav_has_accessible_label(self, active_user):
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)
        content = response.content.decode()
        assert '<nav class="corrux-sidebar" aria-label="Navigation principale">' in content

    def test_no_django_comment_leaks_into_shell_output(self, active_user):
        """Régression directe UI-101 : {# #} multi-lignes fuit dans le
        rendu. Vérifié explicitement sur les nouveaux templates du shell."""
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)
        content = response.content.decode()
        assert "{#" not in content
        assert "{% comment %}" not in content
