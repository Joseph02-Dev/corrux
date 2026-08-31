"""Tests unitaires des modèles `core` (identity/authz) — TECH-001.

Couvre : création valide, contraintes d'unicité, clés étrangères, valeurs
par défaut, statut. Aucun test d'authentification/login (TECH-002) ni de
moteur RBAC (TECH-003) : ce ticket ne porte que la structure de données.
"""

import pytest
from django.db import IntegrityError, transaction

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity.models import User


@pytest.mark.django_db
class TestUser:
    def test_create_valid_user(self):
        user = User.objects.create(
            username="jdupont",
            password_hash="argon2id$dummy-hash",
            full_name="Jean Dupont",
        )
        assert user.pk is not None
        assert user.created_at is not None

    def test_username_is_unique(self):
        User.objects.create(
            username="jdupont", password_hash="x", full_name="Jean Dupont"
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create(
                username="jdupont", password_hash="y", full_name="Autre Jean"
            )

    def test_status_defaults_to_active(self):
        user = User.objects.create(
            username="jdupont", password_hash="x", full_name="Jean Dupont"
        )
        assert user.status == User.Status.ACTIVE


@pytest.mark.django_db
class TestRole:
    def test_create_valid_role(self):
        role = Role.objects.create(name="Administrateur", description="Accès complet")
        assert role.pk is not None

    def test_name_is_unique(self):
        Role.objects.create(name="Administrateur")
        with pytest.raises(IntegrityError), transaction.atomic():
            Role.objects.create(name="Administrateur")


@pytest.mark.django_db
class TestUserRole:
    def test_create_valid_user_role(self):
        user = User.objects.create(username="jdupont", password_hash="x", full_name="Jean Dupont")
        role = Role.objects.create(name="Employé")
        user_role = UserRole.objects.create(user=user, role=role)
        assert user_role.pk is not None
        assert user.user_roles.count() == 1
        assert role.user_roles.count() == 1

    def test_user_role_pair_is_unique(self):
        user = User.objects.create(username="jdupont", password_hash="x", full_name="Jean Dupont")
        role = Role.objects.create(name="Employé")
        UserRole.objects.create(user=user, role=role)
        with pytest.raises(IntegrityError), transaction.atomic():
            UserRole.objects.create(user=user, role=role)

    def test_deleting_user_cascades_to_user_role(self):
        user = User.objects.create(username="jdupont", password_hash="x", full_name="Jean Dupont")
        role = Role.objects.create(name="Employé")
        UserRole.objects.create(user=user, role=role)
        user.delete()
        assert UserRole.objects.count() == 0


@pytest.mark.django_db
class TestPermission:
    def test_create_valid_permission(self):
        permission = Permission.objects.create(
            module_id="documentation", resource="document", action="read"
        )
        assert permission.pk is not None
        assert str(permission) == "documentation.document.read"

    def test_module_resource_action_triplet_is_unique(self):
        Permission.objects.create(module_id="documentation", resource="document", action="read")
        with pytest.raises(IntegrityError), transaction.atomic():
            Permission.objects.create(
                module_id="documentation", resource="document", action="read"
            )

    def test_same_resource_action_different_module_is_allowed(self):
        Permission.objects.create(module_id="documentation", resource="document", action="read")
        # même resource/action mais module différent : autorisé (triplet distinct).
        Permission.objects.create(module_id="rh", resource="document", action="read")
        assert Permission.objects.count() == 2


@pytest.mark.django_db
class TestRolePermission:
    def test_create_valid_role_permission(self):
        role = Role.objects.create(name="Administrateur RH")
        permission = Permission.objects.create(
            module_id="rh", resource="employee", action="write"
        )
        role_permission = RolePermission.objects.create(role=role, permission=permission)
        assert role_permission.pk is not None
        assert role.role_permissions.count() == 1

    def test_role_permission_pair_is_unique(self):
        role = Role.objects.create(name="Administrateur RH")
        permission = Permission.objects.create(
            module_id="rh", resource="employee", action="write"
        )
        RolePermission.objects.create(role=role, permission=permission)
        with pytest.raises(IntegrityError), transaction.atomic():
            RolePermission.objects.create(role=role, permission=permission)

    def test_deleting_permission_cascades_to_role_permission(self):
        role = Role.objects.create(name="Administrateur RH")
        permission = Permission.objects.create(
            module_id="rh", resource="employee", action="write"
        )
        RolePermission.objects.create(role=role, permission=permission)
        permission.delete()
        assert RolePermission.objects.count() == 0
