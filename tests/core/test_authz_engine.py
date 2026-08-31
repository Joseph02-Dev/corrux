"""Tests du moteur d'autorisation RBAC — TECH-003.

Couvre : résolution des permissions effectives via rôle(s), refus par
défaut, union de permissions sur plusieurs rôles, intégration HTTP
(décorateur require_permission sur une route factice isolée), et le fait
que l'autorisation est strictement calculée côté serveur.
"""

import pytest
from django.test import Client

from core.authz.engine import get_effective_permission_codes, has_permission
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User

PROTECTED_URL = "/dummy-protected/"


@pytest.fixture
def permission_read(db):
    return Permission.objects.create(
        module_id="test", resource="module", action="lire"
    )


@pytest.fixture
def permission_write(db):
    return Permission.objects.create(
        module_id="test", resource="module", action="ecrire"
    )


@pytest.fixture
def role_reader(db, permission_read):
    role = Role.objects.create(name="Lecteur test")
    RolePermission.objects.create(role=role, permission=permission_read)
    return role


@pytest.fixture
def role_writer(db, permission_write):
    role = Role.objects.create(name="Rédacteur test")
    RolePermission.objects.create(role=role, permission=permission_write)
    return role


@pytest.fixture
def user_with_permission(db, role_reader):
    user = User.objects.create(username="autorise", full_name="Utilisateur A")
    auth.set_user_password(user, "Password123!")
    user.save()
    UserRole.objects.create(user=user, role=role_reader)
    return user


@pytest.fixture
def user_without_permission(db):
    user = User.objects.create(username="refuse", full_name="Utilisateur B")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.mark.django_db
class TestPermissionResolution:
    def test_user_with_permission_via_role_is_authorized(self, user_with_permission):
        assert has_permission(user_with_permission, "test.module.lire") is True

    def test_user_without_permission_is_denied(self, user_without_permission):
        assert has_permission(user_without_permission, "test.module.lire") is False

    def test_user_with_no_roles_has_no_permissions(self, user_without_permission):
        assert get_effective_permission_codes(user_without_permission) == set()

    def test_nonexistent_permission_is_denied(self, user_with_permission):
        assert has_permission(user_with_permission, "does.not.exist") is False

    def test_none_user_is_always_denied(self, permission_read):
        assert has_permission(None, "test.module.lire") is False

    def test_inactive_user_is_denied_even_with_role(
        self, user_with_permission
    ):
        user_with_permission.status = User.Status.INACTIVE
        user_with_permission.save()
        assert has_permission(user_with_permission, "test.module.lire") is False

    def test_multiple_roles_give_union_of_permissions(
        self, user_without_permission, role_reader, role_writer
    ):
        user = user_without_permission
        UserRole.objects.create(user=user, role=role_reader)
        UserRole.objects.create(user=user, role=role_writer)

        codes = get_effective_permission_codes(user)

        assert codes == {"test.module.lire", "test.module.ecrire"}

    def test_role_without_matching_permission_does_not_grant_it(
        self, user_without_permission, role_writer
    ):
        UserRole.objects.create(user=user_without_permission, role=role_writer)
        assert has_permission(user_without_permission, "test.module.lire") is False


@pytest.mark.django_db
@pytest.mark.urls("tests.core.authz_test_urls")
class TestProtectedRouteIntegration:
    def _authenticated_client(self, user):
        client = Client()
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = user.id
        session.save()
        client.cookies["sessionid"] = session.session_key
        return client

    def test_route_accessible_with_permission(self, user_with_permission):
        client = self._authenticated_client(user_with_permission)
        response = client.get(PROTECTED_URL)
        assert response.status_code == 200
        assert response.json() == {"detail": "accès autorisé"}

    def test_route_returns_403_without_permission(self, user_without_permission):
        client = self._authenticated_client(user_without_permission)
        response = client.get(PROTECTED_URL)
        assert response.status_code == 403

    def test_route_returns_401_when_not_authenticated(self):
        client = Client()
        response = client.get(PROTECTED_URL)
        assert response.status_code == 401

    def test_client_supplied_permission_claim_is_ignored(
        self, user_without_permission
    ):
        """Le serveur ignore toute prétention de permission/role envoyée
        par le client : seule la base fait autorité (aucune permission en
        base pour cet utilisateur => refusé, quoi que le client prétende)."""
        client = self._authenticated_client(user_without_permission)
        response = client.get(
            PROTECTED_URL,
            HTTP_X_CORRUX_ROLE="Administrateur",
            HTTP_X_CORRUX_PERMISSION="test.module.lire",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestServerSideAuthority:
    def test_permission_check_ignores_arbitrary_user_supplied_attribute(
        self, user_without_permission
    ):
        """Même si un attribut arbitraire est injecté sur l'objet (simulant
        une donnée non fiable), seule la relation UserRole->RolePermission
        persistée en base est utilisée par le moteur."""
        user_without_permission.claimed_permissions = ["test.module.lire"]
        assert has_permission(user_without_permission, "test.module.lire") is False


# --- Non-régression TECH-002 (exécutée avec le reste de la suite) --------
# Les tests de tests/core/test_authentication.py ne sont pas dupliqués ici :
# la suite pytest complète (`pytest`) les exécute et sert de vérification
# de non-régression pour TECH-002 (login/logout/session/Argon2id/CSRF/
# anti-bruteforce).
