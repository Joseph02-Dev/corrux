"""Tests des permissions document/dossier, intégration à authz — TECH-023.

Objets réels (User, Role, Document, Folder, DocumentPermission), aucun
mock du moteur d'autorisation. Couvre les 28 points du contrat Phase 2 :
primitive de lecture, attribution, retrait, intégration TECH-022, audit.
"""

import pytest
from django.test import override_settings

from core.audit.models import AuditLog
from core.authz.models import Role, UserRole
from core.authz.object_permissions import has_object_permission
from core.identity.models import User
from modules.documentation.models import DocumentPermission
from modules.documentation.services import (
    create_folder,
    grant_permission,
    has_document_permission,
    has_folder_permission,
    revoke_permission,
    search_documents,
    upload_document,
)


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def owner(db):
    return User.objects.create(username="owner_perm", password_hash="x", full_name="Propriétaire")


@pytest.fixture
def other_user(db):
    return User.objects.create(username="autre_perm", password_hash="x", full_name="Autre")


@pytest.fixture
def document(storage_root, owner):
    return upload_document(content=b"x", filename="doc.pdf", owner_user=owner)


@pytest.fixture
def folder():
    return create_folder(name="Dossier Test")


@pytest.fixture
def role(db):
    return Role.objects.create(name="role-perm-test")


# --- Primitive de lecture (1-12) ------------------------------------------------


@pytest.mark.django_db
class TestReadPrimitive:
    def test_owner_read_authorized(self, document, owner):
        assert has_document_permission(owner, document, "read") is True

    def test_user_without_permission_denied(self, document, other_user):
        assert has_document_permission(other_user, document, "read") is False

    def test_direct_user_permission_authorized(self, document, other_user, owner):
        grant_permission(actor=owner, action="read", document=document, user=other_user)
        assert has_document_permission(other_user, document, "read") is True

    def test_role_permission_authorized(self, document, other_user, owner, role):
        UserRole.objects.create(user=other_user, role=role)
        grant_permission(actor=owner, action="read", document=document, role=role)
        assert has_document_permission(other_user, document, "read") is True

    def test_multiple_roles_one_authorized(self, document, other_user, owner, role):
        unrelated_role = Role.objects.create(name="role-perm-unrelated")
        UserRole.objects.create(user=other_user, role=unrelated_role)
        UserRole.objects.create(user=other_user, role=role)
        grant_permission(actor=owner, action="read", document=document, role=role)
        assert has_document_permission(other_user, document, "read") is True

    def test_no_applicable_permission_denied(self, document, other_user, owner, role):
        UserRole.objects.create(user=other_user, role=role)
        assert has_document_permission(other_user, document, "read") is False

    def test_write_with_explicit_permission(self, document, other_user, owner):
        grant_permission(actor=owner, action="write", document=document, user=other_user)
        assert has_document_permission(other_user, document, "write") is True

    def test_owner_write_not_implicitly_authorized(self, document, owner):
        """Décision Phase 2 explicite : aucune extrapolation d'un accès
        write implicite par simple propriété."""
        assert has_document_permission(owner, document, "write") is False

    def test_permission_on_one_document_does_not_grant_another(
        self, storage_root, owner, other_user
    ):
        doc_a = upload_document(content=b"a", filename="a.pdf", owner_user=owner)
        doc_b = upload_document(content=b"b", filename="b.pdf", owner_user=owner)
        grant_permission(actor=owner, action="read", document=doc_a, user=other_user)

        assert has_document_permission(other_user, doc_a, "read") is True
        assert has_document_permission(other_user, doc_b, "read") is False

    def test_folder_permission_does_not_grant_access_to_contained_document(
        self, storage_root, owner, other_user, folder
    ):
        """Décision structurante Phase 2 : aucun héritage dossier -> document."""
        doc_in_folder = upload_document(
            content=b"x", filename="x.pdf", owner_user=owner, folder=folder
        )
        grant_permission(actor=owner, action="read", folder=folder, user=other_user)

        assert has_folder_permission(other_user, folder, "read") is True
        assert has_document_permission(other_user, doc_in_folder, "read") is False

    def test_subfolder_permission_does_not_grant_access_to_parent(
        self, owner, other_user, folder
    ):
        child = create_folder(name="Enfant", parent=folder)
        grant_permission(actor=owner, action="read", folder=child, user=other_user)

        assert has_folder_permission(other_user, child, "read") is True
        assert has_folder_permission(other_user, folder, "read") is False

    def test_document_and_folder_use_the_same_grant_mechanism(
        self, storage_root, owner, other_user, folder
    ):
        permission = grant_permission(
            actor=owner, action="read", document=None, folder=folder, user=other_user
        )
        assert isinstance(permission, DocumentPermission)
        assert has_folder_permission(other_user, folder, "read") is True


# --- Attribution (13-16) --------------------------------------------------------


@pytest.mark.django_db
class TestGrant:
    def test_grant_to_user(self, document, other_user, owner):
        permission = grant_permission(
            actor=owner, action="read", document=document, user=other_user
        )
        assert permission.user == other_user
        assert permission.role is None

    def test_grant_to_role(self, document, owner, role):
        permission = grant_permission(actor=owner, action="read", document=document, role=role)
        assert permission.role == role
        assert permission.user is None

    def test_grant_on_document(self, document, owner, other_user):
        permission = grant_permission(
            actor=owner, action="read", document=document, user=other_user
        )
        assert permission.document == document
        assert permission.folder is None

    def test_grant_on_folder(self, folder, owner, other_user):
        permission = grant_permission(actor=owner, action="read", folder=folder, user=other_user)
        assert permission.folder == folder
        assert permission.document is None


# --- Retrait (17-19) -------------------------------------------------------------


@pytest.mark.django_db
class TestRevoke:
    def test_revoke_user_permission_closes_access_immediately(self, document, other_user, owner):
        permission = grant_permission(
            actor=owner, action="read", document=document, user=other_user
        )
        assert has_document_permission(other_user, document, "read") is True

        revoke_permission(actor=owner, permission=permission)

        assert has_document_permission(other_user, document, "read") is False

    def test_revoke_role_permission_closes_access_immediately(
        self, document, other_user, owner, role
    ):
        UserRole.objects.create(user=other_user, role=role)
        permission = grant_permission(actor=owner, action="read", document=document, role=role)
        assert has_document_permission(other_user, document, "read") is True

        revoke_permission(actor=owner, permission=permission)

        assert has_document_permission(other_user, document, "read") is False

    def test_revoke_one_permission_keeps_access_if_another_applies(
        self, document, other_user, owner, role
    ):
        UserRole.objects.create(user=other_user, role=role)
        role_permission = grant_permission(
            actor=owner, action="read", document=document, role=role
        )
        grant_permission(actor=owner, action="read", document=document, user=other_user)

        revoke_permission(actor=owner, permission=role_permission)

        assert has_document_permission(other_user, document, "read") is True


# --- Intégration TECH-022 (20-25) ------------------------------------------------


@pytest.mark.django_db
class TestSearchIntegration:
    def test_search_uses_the_official_primitive(self):
        import inspect

        from modules.documentation import services

        source = inspect.getsource(services.search_documents)
        assert "_document_read_permission_filter" in source

    def test_authorized_user_sees_document_in_search(self, storage_root, owner, other_user):
        document = upload_document(content=b"x", filename="partage.pdf", owner_user=owner)
        grant_permission(actor=owner, action="read", document=document, user=other_user)

        results = search_documents(user=other_user, keyword="partage")
        assert results == [document]

    def test_unauthorized_user_does_not_see_document_in_search(
        self, storage_root, owner, other_user
    ):
        upload_document(content=b"x", filename="prive.pdf", owner_user=owner)
        results = search_documents(user=other_user, keyword="prive")
        assert results == []

    def test_exact_keyword_of_forbidden_document_returns_nothing(
        self, storage_root, owner, other_user
    ):
        upload_document(content=b"x", filename="confidentiel.pdf", owner_user=owner)
        results = search_documents(user=other_user, keyword="confidentiel.pdf")
        assert results == []

    def test_role_permission_vs_user_permission_both_grant_search_visibility(
        self, storage_root, owner, other_user, role
    ):
        UserRole.objects.create(user=other_user, role=role)
        via_role = upload_document(content=b"a", filename="via-role.pdf", owner_user=owner)
        via_user = upload_document(content=b"b", filename="via-user.pdf", owner_user=owner)
        grant_permission(actor=owner, action="read", document=via_role, role=role)
        grant_permission(actor=owner, action="read", document=via_user, user=other_user)

        results = search_documents(user=other_user)
        assert set(results) == {via_role, via_user}

    def test_no_forbidden_document_ever_exposed_after_revocation(
        self, storage_root, owner, other_user
    ):
        document = upload_document(content=b"x", filename="temporaire.pdf", owner_user=owner)
        permission = grant_permission(
            actor=owner, action="read", document=document, user=other_user
        )
        assert search_documents(user=other_user, keyword="temporaire") == [document]

        revoke_permission(actor=owner, permission=permission)

        assert search_documents(user=other_user, keyword="temporaire") == []


# --- Audit (26-28) ---------------------------------------------------------------


@pytest.mark.django_db
class TestAudit:
    def test_grant_produces_the_expected_audit_event(self, document, other_user, owner):
        grant_permission(actor=owner, action="read", document=document, user=other_user)

        entry = AuditLog.objects.get(action="documentation.permission_grant")
        assert entry.actor_user == owner
        assert entry.target == f"document:{document.id}"
        assert entry.metadata["action"] == "read"
        assert entry.metadata["grantee"] == f"user:{other_user.username}"

    def test_revoke_produces_the_expected_audit_event(self, document, other_user, owner):
        permission = grant_permission(
            actor=owner, action="read", document=document, user=other_user
        )
        AuditLog.objects.filter(action="documentation.permission_grant").delete()

        revoke_permission(actor=owner, permission=permission)

        entry = AuditLog.objects.get(action="documentation.permission_revoke")
        assert entry.actor_user == owner
        assert entry.target == f"document:{document.id}"

    def test_read_check_produces_no_audit_event(self, document, owner, other_user):
        AuditLog.objects.all().delete()
        has_document_permission(other_user, document, "read")
        has_document_permission(owner, document, "read")
        assert AuditLog.objects.count() == 0

    def test_search_produces_no_audit_event(self, storage_root, owner):
        upload_document(content=b"x", filename="x.pdf", owner_user=owner)
        AuditLog.objects.all().delete()
        search_documents(user=owner)
        assert AuditLog.objects.count() == 0

    def test_idempotent_grant_does_not_duplicate_audit_event(self, document, other_user, owner):
        """get_or_create : un second appel identique ne crée ni doublon
        ni second événement d'audit (pas de no-op audité, cohérent avec
        activate_module()/deactivate_module())."""
        grant_permission(actor=owner, action="read", document=document, user=other_user)
        grant_permission(actor=owner, action="read", document=document, user=other_user)

        assert DocumentPermission.objects.count() == 1
        assert AuditLog.objects.filter(action="documentation.permission_grant").count() == 1


# --- Primitive générique core.authz (isolée, sans DocumentPermission) -----------


@pytest.mark.django_db
class TestGenericPrimitiveIsolated:
    def test_generic_primitive_does_not_import_documentation_models(self):
        import ast
        import inspect

        from core.authz import object_permissions

        # Analyse les imports réellement exécutés (AST), pas la
        # docstring en prose (qui mentionne "modules.documentation" pour
        # justifier précisément son absence de dépendance).
        source = inspect.getsource(object_permissions)
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)

        assert not any(name.startswith("modules") for name in imported_modules)

    def test_none_user_is_denied(self):
        assert has_object_permission(None, DocumentPermission.objects.none(), "read") is False

    def test_inactive_user_is_denied(self, other_user, document, owner):
        grant_permission(actor=owner, action="read", document=document, user=other_user)
        other_user.status = User.Status.INACTIVE
        other_user.save()

        assert has_document_permission(other_user, document, "read") is False
