"""Tests unitaires des modèles `documentation` — TECH-020.

Structure de données uniquement (pas de stockage, pas de permission
applicative, pas de vue) : couvre création valide, relations, contraintes
et qualification de schéma. Patron déjà établi par
tests/core/test_identity_authz_models.py (TECH-001), réutilisé tel quel.
"""

import pytest
from django.db import IntegrityError, connection, transaction

from core.authz.models import Role
from core.identity.models import User
from modules.documentation.models import Document, DocumentMetadata, DocumentPermission, Folder


@pytest.fixture
def owner(db):
    return User.objects.create(username="proprietaire", password_hash="x", full_name="Propriétaire")


@pytest.fixture
def document(db, owner):
    return Document.objects.create(
        owner_user=owner,
        filename="contrat.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        storage_path="abcd1234-uuid",
    )


@pytest.fixture
def role(db):
    return Role.objects.create(name="role-test-documentation")


# --- A. Création valide ---------------------------------------------------------


@pytest.mark.django_db
class TestValidCreation:
    def test_create_valid_document(self, owner):
        document = Document.objects.create(
            owner_user=owner,
            filename="rapport.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
            storage_path="uuid-xyz",
        )
        assert document.pk is not None
        assert document.created_at is not None

    def test_create_valid_folder(self):
        folder = Folder.objects.create(name="Contrats")
        assert folder.pk is not None

    def test_create_valid_document_permission_for_document_and_role(self, document, role):
        permission = DocumentPermission.objects.create(
            document=document, role=role, action="read"
        )
        assert permission.pk is not None

    def test_create_valid_document_metadata(self, document):
        metadata = DocumentMetadata.objects.create(
            document=document, key="categorie", value="RH"
        )
        assert metadata.pk is not None


# --- B. Relations ----------------------------------------------------------------


@pytest.mark.django_db
class TestRelations:
    def test_document_to_user(self, owner):
        document = Document.objects.create(
            owner_user=owner, filename="x.pdf", mime_type="application/pdf",
            size_bytes=1, storage_path="x",
        )
        assert document.owner_user == owner
        assert owner.owned_documents.get() == document

    def test_document_to_folder(self, owner):
        folder = Folder.objects.create(name="Dossier")
        document = Document.objects.create(
            owner_user=owner, folder=folder, filename="x.pdf",
            mime_type="application/pdf", size_bytes=1, storage_path="x",
        )
        assert document.folder == folder
        assert folder.documents.get() == document

    def test_document_without_folder_is_valid(self, owner):
        """Résolution documentée : un document peut exister à la racine."""
        document = Document.objects.create(
            owner_user=owner, filename="racine.pdf", mime_type="application/pdf",
            size_bytes=1, storage_path="x",
        )
        assert document.folder is None

    def test_folder_to_parent_folder(self):
        parent = Folder.objects.create(name="Parent")
        child = Folder.objects.create(name="Enfant", parent_folder=parent)
        assert child.parent_folder == parent
        assert parent.subfolders.get() == child

    def test_folder_without_parent_is_valid(self):
        root = Folder.objects.create(name="Racine")
        assert root.parent_folder is None

    def test_document_permission_to_document(self, document, role):
        permission = DocumentPermission.objects.create(document=document, role=role, action="read")
        assert permission.document == document
        assert document.permissions.get() == permission

    def test_document_permission_to_folder(self, role):
        folder = Folder.objects.create(name="Dossier permission")
        permission = DocumentPermission.objects.create(folder=folder, role=role, action="write")
        assert permission.folder == folder
        assert folder.permissions.get() == permission

    def test_document_permission_to_user_grantee(self, document, owner):
        permission = DocumentPermission.objects.create(document=document, user=owner, action="read")
        assert permission.user == owner
        assert owner.document_permissions.get() == permission

    def test_document_metadata_to_document(self, document):
        metadata = DocumentMetadata.objects.create(document=document, key="k", value="v")
        assert metadata.document == document
        assert document.metadata_entries.get() == metadata


# --- C. Contraintes ---------------------------------------------------------------


@pytest.mark.django_db
class TestConstraints:
    def test_permission_requires_exactly_one_target(self, role):
        """Ni document ni folder -> refusé (CheckConstraint)."""
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentPermission.objects.create(role=role, action="read")

    def test_permission_rejects_both_document_and_folder(self, document, role):
        folder = Folder.objects.create(name="X")
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentPermission.objects.create(
                document=document, folder=folder, role=role, action="read"
            )

    def test_permission_requires_exactly_one_grantee(self, document):
        """Ni role ni user -> refusé (CheckConstraint)."""
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentPermission.objects.create(document=document, action="read")

    def test_permission_rejects_both_role_and_user(self, document, role, owner):
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentPermission.objects.create(
                document=document, role=role, user=owner, action="read"
            )

    def test_document_metadata_requires_a_document(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentMetadata.objects.create(key="k", value="v")

    def test_document_owner_is_required(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            Document.objects.create(
                filename="x.pdf", mime_type="application/pdf", size_bytes=1, storage_path="x"
            )

    def test_deleting_owner_is_protected(self, document, owner):
        """Résolution documentée : PROTECT, cohérent avec core.audit_log."""
        with pytest.raises(IntegrityError), transaction.atomic():
            owner.delete()
        assert Document.objects.filter(pk=document.pk).exists()

    def test_deleting_folder_is_protected_when_it_contains_a_document(self, owner):
        folder = Folder.objects.create(name="Protégé")
        Document.objects.create(
            owner_user=owner, folder=folder, filename="x.pdf",
            mime_type="application/pdf", size_bytes=1, storage_path="x",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            folder.delete()

    def test_deleting_document_cascades_to_its_permissions_and_metadata(self, document, role):
        DocumentPermission.objects.create(document=document, role=role, action="read")
        DocumentMetadata.objects.create(document=document, key="k", value="v")

        document.delete()

        assert DocumentPermission.objects.count() == 0
        assert DocumentMetadata.objects.count() == 0

    def test_deleting_folder_cascades_to_its_permissions(self, role):
        folder = Folder.objects.create(name="Avec permission")
        DocumentPermission.objects.create(folder=folder, role=role, action="read")
        folder.delete()
        assert DocumentPermission.objects.count() == 0


# --- D. Schéma ---------------------------------------------------------------------


@pytest.mark.django_db
class TestSchema:
    def test_tables_are_qualified_in_the_documentation_schema(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'documentation' ORDER BY table_name"
            )
            tables = {row[0] for row in cursor.fetchall()}
        assert tables == {
            "documents",
            "folders",
            "document_permissions",
            "document_metadata",
        }

    def test_no_field_beyond_those_specified_by_architecture(self, document):
        """Aucun timestamp/UUID/statut/soft-delete non spécifié n'a été
        ajouté — vérifié sur les champs réellement présents."""
        field_names = {f.name for f in Document._meta.get_fields()}
        expected = {
            "id", "owner_user", "folder", "filename", "mime_type",
            "size_bytes", "storage_path", "created_at",
            "permissions", "metadata_entries",  # related_name inverses
        }
        assert field_names == expected
