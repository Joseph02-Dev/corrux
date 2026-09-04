"""Tests de get_navigation() — UI-102.

Utilise de vrais objets User/Role/UserRole/Permission/RolePermission/
Module (TECH-001/003/005/006) — aucune simulation du RBAC. Vérifie le
comportement métier réel : union multi-rôles, garde module+permission,
décisions produit #8 (groupe absent) / #9 (item absent).
"""

import pytest

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity.models import User
from core.modules.models import Module
from ui.navigation import get_navigation


@pytest.fixture
def make_user(db):
    def _make(username):
        return User.objects.create(username=username, full_name=username.title())

    return _make


def _grant(user, *permission_codes):
    """Crée un rôle dédié portant exactement ces permissions et l'assigne
    à `user` — utilise le vrai chemin User->UserRole->Role->RolePermission
    ->Permission (TECH-001/003), pas un raccourci artificiel."""
    role = Role.objects.create(name=f"role-{user.username}-{len(permission_codes)}")
    for code in permission_codes:
        module_id, resource, action = code.split(".")
        permission, _ = Permission.objects.get_or_create(
            module_id=module_id, resource=resource, action=action
        )
        RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


def _activate_module(module_id):
    Module.objects.update_or_create(
        id=module_id,
        defaults={
            "name": module_id,
            "version": "1.0.0",
            "state": Module.State.ACTIVATED,
            "manifest_snapshot": {},
        },
    )


@pytest.mark.django_db
class TestAnonymousUser:
    def test_none_user_gets_no_navigation_at_all(self):
        assert get_navigation(None) == ()


@pytest.mark.django_db
class TestAuthenticatedWithoutPermission:
    def test_user_with_no_roles_sees_no_groups(self, make_user):
        user = make_user("sanspermission")
        assert get_navigation(user) == ()


@pytest.mark.django_db
class TestAdministrationGroup:
    def test_single_permission_shows_only_matching_item(self, make_user):
        user = make_user("admin_partiel")
        _grant(user, "core.user.read")

        groups = get_navigation(user)

        assert len(groups) == 1
        assert groups[0].label == "Administration"
        labels = [item.label for item in groups[0].items]
        assert labels == ["Utilisateurs & rôles"]

    def test_full_admin_permissions_show_all_four_items(self, make_user):
        user = make_user("admin_complet")
        _grant(
            user,
            "core.user.read",
            "core.module.read",
            "core.backup.read",
            "core.audit.read",
        )

        groups = get_navigation(user)

        assert len(groups) == 1
        labels = {item.label for item in groups[0].items}
        assert labels == {
            "Utilisateurs & rôles",
            "Modules",
            "Sauvegardes",
            "Journal d'audit",
        }

    def test_group_entirely_absent_without_any_admin_permission(self, make_user):
        """Décision produit #8 : un groupe sans aucune permission n'apparaît
        jamais — pas de groupe vide, pas de groupe grisé."""
        user = make_user("sans_admin")
        _grant(user, "documentation.document.read")  # une permission, mais pas admin
        _activate_module("documentation")

        groups = get_navigation(user)

        assert all(g.label != "Administration" for g in groups)


@pytest.mark.django_db
class TestDocumentationRequiresModuleAndPermission:
    def test_permission_alone_without_active_module_shows_nothing(self, make_user):
        """Permission accordée mais module NON activé -> item absent."""
        user = make_user("doc_sans_module")
        _grant(user, "documentation.document.read")
        # Documentation n'est pas activé (aucun Module créé du tout).

        groups = get_navigation(user)

        assert all(g.label != "Documentation" for g in groups)

    def test_active_module_alone_without_permission_shows_nothing(self, make_user):
        """Module activé mais AUCUNE permission -> item absent."""
        user = make_user("doc_sans_permission")
        _activate_module("documentation")

        groups = get_navigation(user)

        assert all(g.label != "Documentation" for g in groups)

    def test_module_installed_but_not_activated_shows_nothing(self, make_user):
        """Module installé (pas seulement absent) mais PAS activé -> absent.
        Vérifie la distinction installé != activé, pas seulement présent/absent."""
        user = make_user("doc_module_installe")
        _grant(user, "documentation.document.read")
        Module.objects.create(
            id="documentation", name="Documentation", version="1.0.0",
            state=Module.State.INSTALLED, manifest_snapshot={},
        )

        groups = get_navigation(user)

        assert all(g.label != "Documentation" for g in groups)

    def test_active_module_and_permission_together_show_the_group(self, make_user):
        user = make_user("doc_complet")
        _grant(user, "documentation.document.read")
        _activate_module("documentation")

        groups = get_navigation(user)

        doc_group = next(g for g in groups if g.label == "Documentation")
        labels = {item.label for item in doc_group.items}
        assert labels == {"Documents", "Recherche"}

    def test_deactivating_module_again_removes_the_group(self, make_user):
        """Un module réellement désactivé (pas seulement absent) retire
        aussi l'entrée — même garde que pour "jamais activé"."""
        user = make_user("doc_desactive")
        _grant(user, "documentation.document.read")
        module, _ = Module.objects.update_or_create(
            id="documentation",
            defaults={
                "name": "Documentation", "version": "1.0.0",
                "state": Module.State.ACTIVATED, "manifest_snapshot": {},
            },
        )
        assert get_navigation(user)  # activé : visible

        module.state = Module.State.DEACTIVATED
        module.save(update_fields=["state"])

        groups = get_navigation(user)
        assert all(g.label != "Documentation" for g in groups)


@pytest.mark.django_db
class TestRhRequiresModuleAndPermission:
    def test_rh_item_isolated_absence_within_active_group(self, make_user):
        """Décision produit #9 : un item isolé disparaît sans casser le
        reste du groupe (Employés visible, Congés absent)."""
        user = make_user("rh_partiel")
        _grant(user, "rh.employee.read")
        _activate_module("rh")

        groups = get_navigation(user)

        rh_group = next(g for g in groups if g.label == "Ressources Humaines")
        labels = [item.label for item in rh_group.items]
        assert labels == ["Employés"]

    def test_rh_group_absent_without_module_active(self, make_user):
        user = make_user("rh_sans_module")
        _grant(user, "rh.employee.read", "rh.leave_request.read")
        # rh n'est pas activé.

        groups = get_navigation(user)

        assert all(g.label != "Ressources Humaines" for g in groups)


@pytest.mark.django_db
class TestMultipleRoles:
    def test_permissions_from_several_roles_are_unioned(self, make_user):
        """Plusieurs rôles distincts : l'union de leurs permissions
        détermine la navigation visible (réutilise directement l'union
        déjà testée au niveau du moteur RBAC, TECH-003)."""
        user = make_user("multi_role")
        role_a = Role.objects.create(name="Lecteur documentation")
        perm_a, _ = Permission.objects.get_or_create(
            module_id="documentation", resource="document", action="read"
        )
        RolePermission.objects.create(role=role_a, permission=perm_a)
        UserRole.objects.create(user=user, role=role_a)

        role_b = Role.objects.create(name="Lecteur RH")
        perm_b, _ = Permission.objects.get_or_create(
            module_id="rh", resource="employee", action="read"
        )
        RolePermission.objects.create(role=role_b, permission=perm_b)
        UserRole.objects.create(user=user, role=role_b)

        _activate_module("documentation")
        _activate_module("rh")

        groups = get_navigation(user)
        group_labels = {g.label for g in groups}
        assert group_labels == {"Documentation", "Ressources Humaines"}


@pytest.mark.django_db
class TestFullMultiGroupScenario:
    def test_admin_rh_with_documentation_inactive(self, make_user):
        """Scénario combiné réaliste : Administrateur RH avec Documentation
        installé mais pas encore activé — seule RH doit apparaître."""
        user = make_user("admin_rh")
        _grant(user, "rh.employee.read", "rh.leave_request.read", "core.user.read")
        _activate_module("rh")
        Module.objects.create(
            id="documentation", name="Documentation", version="1.0.0",
            state=Module.State.INSTALLED, manifest_snapshot={},
        )

        groups = get_navigation(user)
        group_labels = {g.label for g in groups}
        assert group_labels == {"Administration", "Ressources Humaines"}
