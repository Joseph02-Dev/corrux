"""Tests des écrans Rôles / Matrice de permissions — UI-202.

Rendu HTTP réel, RBAC réel (TECH-003), mutation réelle (RolePermission),
vérifiée par effet observable sur has_permission() et sur une route
protégée réelle — pas une inspection de code.
"""

import pytest
from django.test import Client

from core.audit.models import AuditLog
from core.authz.engine import has_permission
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User

ROLE_LIST_URL = "/utilisateurs/roles/"
ROLE_MATRIX_URL = "/utilisateurs/roles/matrice/"
ROLE_MATRIX_TOGGLE_URL = "/utilisateurs/roles/matrice/toggler/"


def _grant(user, module_id, resource, action):
    role = Role.objects.create(name=f"role-{user.username}-{resource}-{action}")
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


@pytest.fixture
def basic_user(db):
    """Utilisateur avec core.user.read uniquement (accès aux deux
    onglets Utilisateurs/Rôles), mais pas core.role.write (pas la
    matrice)."""
    user = User.objects.create(username="lecteur_roles", full_name="Lecteur Rôles")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "user", "read")
    return user


@pytest.fixture
def admin_user(db):
    """Utilisateur avec core.user.read (accès à l'écran) + core.role.write
    (accès à la matrice) — scénario réaliste d'un Administrateur, qui
    dispose des deux, pas de core.role.write en isolation."""
    user = User.objects.create(username="admin_roles", full_name="Admin Rôles")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "user", "read")
    _grant(user, "core", "role", "write")
    return user


@pytest.fixture
def no_permission_user(db):
    user = User.objects.create(username="sanspermission_roles", full_name="Sans Permission")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.fixture
def predefined_roles(db):
    """Les 4 rôles prédéfinis (seedés par la migration 0006, TECH-001) —
    utilisés tels quels, jamais recréés artificiellement."""
    return list(
        Role.objects.filter(
            name__in=("Administrateur", "Administrateur RH", "Valideur", "Employé")
        )
    )


# --- A. Liste des rôles ----------------------------------------------------------


@pytest.mark.django_db
class TestRoleList:
    def test_authorized_user_sees_the_screen(self, basic_user):
        client = _authenticated_client(basic_user)
        assert client.get(ROLE_LIST_URL).status_code == 200

    def test_unauthorized_user_sees_permission_denied(self, no_permission_user):
        client = _authenticated_client(no_permission_user)
        response = client.get(ROLE_LIST_URL)
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self):
        client = Client()
        response = client.get(ROLE_LIST_URL)
        assert response.status_code == 302
        assert response.url == f"/login/?next={ROLE_LIST_URL}"

    def test_post_is_rejected(self, basic_user):
        """Bug corrigé (audit général) : role_list n'avait aucune
        restriction de méthode — un POST était traité comme un GET."""
        client = _authenticated_client(basic_user)
        response = client.post(ROLE_LIST_URL)
        assert response.status_code == 405

    def test_four_predefined_roles_are_displayed(self, basic_user, predefined_roles):
        client = _authenticated_client(basic_user)
        content = client.get(ROLE_LIST_URL).content.decode()
        for role in predefined_roles:
            assert role.name in content

    def test_user_count_reflects_real_assignments(self, basic_user, predefined_roles):
        admin_role = Role.objects.get(name="Administrateur")
        extra = User.objects.create(username="extra_role_user", full_name="Extra")
        UserRole.objects.create(user=extra, role=admin_role)

        client = _authenticated_client(basic_user)
        content = client.get(ROLE_LIST_URL).content.decode()
        assert "<td>1</td>" in content or ">1<" in content

    def test_matrix_link_visible_only_with_role_write_permission(self, basic_user, admin_user):
        content_basic = _authenticated_client(basic_user).get(ROLE_LIST_URL).content.decode()
        content_admin = _authenticated_client(admin_user).get(ROLE_LIST_URL).content.decode()

        assert "Voir les permissions" not in content_basic
        assert "Voir les permissions" in content_admin

    def test_tabs_link_to_users_screen(self, basic_user):
        client = _authenticated_client(basic_user)
        content = client.get(ROLE_LIST_URL).content.decode()
        assert 'href="/utilisateurs/"' in content


# --- B. Matrice — accès ----------------------------------------------------------


@pytest.mark.django_db
class TestMatrixAccess:
    def test_admin_sees_the_matrix(self, admin_user):
        client = _authenticated_client(admin_user)
        assert client.get(ROLE_MATRIX_URL).status_code == 200

    def test_basic_user_without_role_write_sees_permission_denied(self, basic_user):
        """core.user.read seul ne suffit pas — décision produit #2."""
        client = _authenticated_client(basic_user)
        response = client.get(ROLE_MATRIX_URL)
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self):
        client = Client()
        response = client.get(ROLE_MATRIX_URL)
        assert response.status_code == 302

    def test_post_is_rejected(self, admin_user):
        """Bug corrigé (audit général) : role_matrix n'avait aucune
        restriction de méthode — un POST était traité comme un GET."""
        client = _authenticated_client(admin_user)
        response = client.post(ROLE_MATRIX_URL)
        assert response.status_code == 405


# --- C. Matrice — contenu ---------------------------------------------------------


@pytest.mark.django_db
class TestMatrixContent:
    def test_matrix_shows_all_predefined_roles_as_columns(self, admin_user, predefined_roles):
        client = _authenticated_client(admin_user)
        content = client.get(ROLE_MATRIX_URL).content.decode()
        for role in predefined_roles:
            assert role.name in content

    def test_matrix_shows_the_three_module_groups(self, admin_user):
        client = _authenticated_client(admin_user)
        content = client.get(ROLE_MATRIX_URL).content.decode()
        assert "Administration" in content
        assert "Documentation" in content
        assert "Ressources humaines" in content

    def test_granted_permission_shows_toggle_on(self, admin_user, predefined_roles):
        admin_role = Role.objects.get(name="Administrateur")
        permission, _ = Permission.objects.get_or_create(
            module_id="core", resource="backup", action="read"
        )
        RolePermission.objects.create(role=admin_role, permission=permission)

        client = _authenticated_client(admin_user)
        content = client.get(ROLE_MATRIX_URL).content.decode()
        assert "corrux-permission-cell__toggle--on" in content

    def test_ungranted_permission_shows_toggle_off(self, admin_user):
        client = _authenticated_client(admin_user)
        content = client.get(ROLE_MATRIX_URL).content.decode()
        assert 'corrux-permission-cell__toggle"' in content


# --- D. Bascule — effet réel --------------------------------------------------------


@pytest.mark.django_db
class TestToggleEffect:
    def test_toggle_on_grants_the_permission(self, admin_user):
        employe_role = Role.objects.get(name="Employé")
        target_user = User.objects.create(username="cible_toggle_on", full_name="Cible")
        UserRole.objects.create(user=target_user, role=employe_role)
        assert has_permission(target_user, "core.audit.read") is False

        client = _authenticated_client(admin_user)
        response = client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )
        assert response.status_code == 302

        assert has_permission(target_user, "core.audit.read") is True

    def test_toggle_off_revokes_the_permission(self, admin_user):
        employe_role = Role.objects.get(name="Employé")
        permission, _ = Permission.objects.get_or_create(
            module_id="core", resource="audit", action="read"
        )
        RolePermission.objects.create(role=employe_role, permission=permission)

        target_user = User.objects.create(username="cible_toggle_off", full_name="Cible")
        UserRole.objects.create(user=target_user, role=employe_role)
        assert has_permission(target_user, "core.audit.read") is True

        client = _authenticated_client(admin_user)
        client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )

        assert has_permission(target_user, "core.audit.read") is False

    def test_toggle_affects_a_real_protected_route_immediately(self, admin_user):
        """Critère d'acceptation explicite : effet sur une route protégée
        réelle (/journal-audit/, gardée par core.audit.read)."""
        employe_role = Role.objects.get(name="Employé")
        target_user = User.objects.create(username="cible_route", full_name="Cible")
        UserRole.objects.create(user=target_user, role=employe_role)
        target_client = _authenticated_client(target_user)

        assert target_client.get("/journal-audit/").status_code == 403

        admin_client = _authenticated_client(admin_user)
        admin_client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )

        assert target_client.get("/journal-audit/").status_code == 200

    def test_toggle_twice_grants_then_revokes(self, admin_user):
        employe_role = Role.objects.get(name="Employé")
        target_user = User.objects.create(username="cible_double", full_name="Cible")
        UserRole.objects.create(user=target_user, role=employe_role)

        client = _authenticated_client(admin_user)
        payload = {
            "role_id": employe_role.id,
            "module_id": "core",
            "resource": "audit",
            "action": "read",
        }
        client.post(ROLE_MATRIX_TOGGLE_URL, payload)
        assert has_permission(target_user, "core.audit.read") is True

        client.post(ROLE_MATRIX_TOGGLE_URL, payload)
        assert has_permission(target_user, "core.audit.read") is False

    def test_toggle_does_not_affect_other_roles(self, admin_user):
        employe_role = Role.objects.get(name="Employé")
        valideur_role = Role.objects.get(name="Valideur")
        employe_user = User.objects.create(username="cible_employe", full_name="Employé Cible")
        valideur_user = User.objects.create(
            username="cible_valideur", full_name="Valideur Cible"
        )
        UserRole.objects.create(user=employe_user, role=employe_role)
        UserRole.objects.create(user=valideur_user, role=valideur_role)

        client = _authenticated_client(admin_user)
        client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )

        assert has_permission(employe_user, "core.audit.read") is True
        assert has_permission(valideur_user, "core.audit.read") is False


# --- E. Sécurité de la bascule -----------------------------------------------------


@pytest.mark.django_db
class TestToggleSecurity:
    def test_toggle_requires_role_write_permission(self, basic_user):
        employe_role = Role.objects.get(name="Employé")
        client = _authenticated_client(basic_user)
        response = client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )
        assert response.status_code == 403

    def test_toggle_get_is_rejected(self, admin_user):
        client = _authenticated_client(admin_user)
        response = client.get(ROLE_MATRIX_TOGGLE_URL)
        assert response.status_code == 405

    def test_toggle_anonymous_is_rejected(self, db):
        employe_role, _ = Role.objects.get_or_create(name="Employé")
        client = Client()
        response = client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )
        assert response.status_code in (401, 403)


# --- F. Audit ----------------------------------------------------------------------


@pytest.mark.django_db
class TestAudit:
    def test_grant_produces_audit_event(self, admin_user):
        employe_role = Role.objects.get(name="Employé")
        client = _authenticated_client(admin_user)
        client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )
        entry = AuditLog.objects.get(action="role.permission_grant")
        assert entry.actor_user == admin_user
        assert entry.target == "Employé"
        assert entry.metadata["permission"] == "core.audit.read"

    def test_revoke_produces_audit_event(self, admin_user):
        employe_role = Role.objects.get(name="Employé")
        permission, _ = Permission.objects.get_or_create(
            module_id="core", resource="audit", action="read"
        )
        RolePermission.objects.create(role=employe_role, permission=permission)

        client = _authenticated_client(admin_user)
        client.post(
            ROLE_MATRIX_TOGGLE_URL,
            {
                "role_id": employe_role.id,
                "module_id": "core",
                "resource": "audit",
                "action": "read",
            },
        )
        entry = AuditLog.objects.get(action="role.permission_revoke")
        assert entry.actor_user == admin_user
        assert entry.target == "Employé"

    def test_viewing_the_matrix_produces_no_audit_event(self, admin_user):
        AuditLog.objects.all().delete()
        client = _authenticated_client(admin_user)
        client.get(ROLE_MATRIX_URL)
        assert AuditLog.objects.count() == 0
