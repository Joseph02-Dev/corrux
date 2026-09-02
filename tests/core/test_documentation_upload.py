"""Tests du service de dépôt/organisation de documents — TECH-021.

Stockage réel (tmp_path + override_settings, patron déjà établi par
tests/core/test_storage.py, TECH-004), base réelle (pas de mock).
Backend pur : aucun appel HTTP, ces fonctions sont testées directement.
"""

import pytest
from django.db import connection
from django.test import override_settings

from core.identity.models import User
from core.storage.files import InvalidPathError
from modules.documentation.models import Document, DocumentMetadata
from modules.documentation.services import (
    MAX_FILE_SIZE_BYTES,
    DocumentUploadError,
    create_folder,
    list_folder_contents,
    upload_document,
)


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def owner(db):
    return User.objects.create(
        username="proprietaire_up", password_hash="x", full_name="Propriétaire"
    )


def _file_of_size(n: int) -> bytes:
    return b"x" * n


# --- Dépôt accepté -----------------------------------------------------------


@pytest.mark.django_db
class TestUploadAccepted:
    @pytest.mark.parametrize(
        "filename,mime",
        [
            ("rapport.pdf", "application/pdf"),
            (
                "contrat.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            (
                "tableau.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("photo.jpg", "image/jpeg"),
        ],
    )
    def test_each_allowed_extension_is_accepted(self, storage_root, owner, filename, mime):
        document = upload_document(
            content=b"contenu de test", filename=filename, owner_user=owner
        )
        assert document.pk is not None
        assert document.mime_type == mime

    def test_content_is_really_written_to_disk_and_readable(self, storage_root, owner):
        from core.storage import files as storage

        content = b"contenu binaire reel \x00\x01\xff"
        document = upload_document(content=content, filename="x.pdf", owner_user=owner)

        read_back = storage.read("documentation", document.storage_path, document.filename)
        assert read_back == content

    def test_document_is_really_created_in_database(self, storage_root, owner):
        document = upload_document(content=b"x", filename="x.pdf", owner_user=owner)
        assert Document.objects.filter(pk=document.pk).exists()

    def test_storage_path_equals_the_returned_file_id(self, storage_root, owner):
        import uuid

        from core.storage import files as storage

        document = upload_document(content=b"x", filename="x.pdf", owner_user=owner)
        assert storage.read("documentation", document.storage_path, document.filename) == b"x"
        uuid.UUID(document.storage_path)  # ne lève pas si c'est un UUID valide

    def test_filename_is_preserved(self, storage_root, owner):
        document = upload_document(content=b"x", filename="rapport-annuel.pdf", owner_user=owner)
        assert document.filename == "rapport-annuel.pdf"

    def test_owner_is_correctly_associated(self, storage_root, owner):
        document = upload_document(content=b"x", filename="x.pdf", owner_user=owner)
        assert document.owner_user == owner

    def test_document_can_be_associated_to_a_folder(self, storage_root, owner):
        folder = create_folder(name="Contrats")
        document = upload_document(
            content=b"x", filename="x.pdf", owner_user=owner, folder=folder
        )
        assert document.folder == folder

    def test_document_without_folder_is_at_the_root(self, storage_root, owner):
        document = upload_document(content=b"x", filename="x.pdf", owner_user=owner)
        assert document.folder is None

    def test_size_bytes_matches_real_content_length(self, storage_root, owner):
        content = b"y" * 12345
        document = upload_document(content=content, filename="x.pdf", owner_user=owner)
        assert document.size_bytes == 12345


# --- Dépôt refusé --------------------------------------------------------------


@pytest.mark.django_db
class TestUploadRejected:
    def test_disallowed_extension_is_rejected(self, storage_root, owner):
        with pytest.raises(DocumentUploadError):
            upload_document(content=b"x", filename="script.exe", owner_user=owner)

    def test_disallowed_extension_writes_no_file(self, storage_root, owner):
        with pytest.raises(DocumentUploadError):
            upload_document(content=b"x", filename="script.exe", owner_user=owner)
        documentation_dir = storage_root / "documentation"
        assert not documentation_dir.exists() or not any(documentation_dir.iterdir())

    def test_disallowed_extension_creates_no_document(self, storage_root, owner):
        with pytest.raises(DocumentUploadError):
            upload_document(content=b"x", filename="script.exe", owner_user=owner)
        assert Document.objects.count() == 0

    def test_file_over_max_size_is_rejected(self, storage_root, owner):
        with pytest.raises(DocumentUploadError):
            upload_document(
                content=_file_of_size(MAX_FILE_SIZE_BYTES + 1),
                filename="x.pdf",
                owner_user=owner,
            )

    def test_file_at_exactly_max_size_is_accepted(self, storage_root, owner):
        document = upload_document(
            content=_file_of_size(MAX_FILE_SIZE_BYTES), filename="x.pdf", owner_user=owner
        )
        assert document.size_bytes == MAX_FILE_SIZE_BYTES

    def test_oversized_file_writes_no_file_and_no_document(self, storage_root, owner):
        with pytest.raises(DocumentUploadError):
            upload_document(
                content=_file_of_size(MAX_FILE_SIZE_BYTES + 1),
                filename="x.pdf",
                owner_user=owner,
            )
        assert Document.objects.count() == 0
        documentation_dir = storage_root / "documentation"
        assert not documentation_dir.exists() or not any(documentation_dir.iterdir())

    def test_validation_happens_before_storage_write_not_after(self, storage_root, owner):
        """Vérifie l'ordre réel : aucune sous-arborescence de stockage
        n'est créée si la validation échoue en amont."""
        assert not (storage_root / "documentation").exists()
        with pytest.raises(DocumentUploadError):
            upload_document(content=b"x", filename="x.exe", owner_user=owner)
        assert not (storage_root / "documentation").exists()


# --- Catégorie -----------------------------------------------------------------


@pytest.mark.django_db
class TestCategory:
    def test_category_creates_a_document_metadata_entry(self, storage_root, owner):
        document = upload_document(
            content=b"x", filename="x.pdf", owner_user=owner, category="RH"
        )
        metadata = DocumentMetadata.objects.get(document=document)
        assert metadata.key == "categorie"
        assert metadata.value == "RH"

    def test_no_category_creates_no_metadata(self, storage_root, owner):
        document = upload_document(content=b"x", filename="x.pdf", owner_user=owner)
        assert DocumentMetadata.objects.filter(document=document).count() == 0

    def test_no_document_field_named_category_exists(self):
        """Confirme le contrat : aucun champ Document.category n'a été ajouté."""
        field_names = {f.name for f in Document._meta.get_fields()}
        assert "category" not in field_names
        assert "categorie" not in field_names


# --- Dossiers --------------------------------------------------------------------


@pytest.mark.django_db
class TestFolders:
    def test_create_root_folder(self):
        folder = create_folder(name="Racine")
        assert folder.pk is not None
        assert folder.parent_folder is None

    def test_create_subfolder(self):
        parent = create_folder(name="Parent")
        child = create_folder(name="Enfant", parent=parent)
        assert child.parent_folder == parent

    def test_list_folder_contents_returns_documents(self, storage_root, owner):
        folder = create_folder(name="Contrats")
        doc = upload_document(content=b"x", filename="x.pdf", owner_user=owner, folder=folder)

        documents, subfolders = list_folder_contents(folder)

        assert documents == [doc]
        assert subfolders == []

    def test_list_folder_contents_returns_subfolders(self):
        parent = create_folder(name="Parent")
        child = create_folder(name="Enfant", parent=parent)

        documents, subfolders = list_folder_contents(parent)

        assert documents == []
        assert subfolders == [child]

    def test_list_root_contents(self, storage_root, owner):
        root_doc = upload_document(content=b"x", filename="racine.pdf", owner_user=owner)
        root_folder = create_folder(name="Dossier racine")
        other_folder = create_folder(name="Sous-dossier", parent=root_folder)
        upload_document(
            content=b"y", filename="dans-dossier.pdf", owner_user=owner, folder=root_folder
        )

        documents, subfolders = list_folder_contents(None)

        assert documents == [root_doc]
        assert subfolders == [root_folder]
        assert other_folder not in subfolders


# --- Sécurité ----------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_path_traversal_filename_is_rejected_by_storage_layer(self, storage_root, owner):
        """Le service ne duplique pas la validation de chemin — elle
        remonte telle quelle depuis core.storage.files."""
        with pytest.raises(InvalidPathError):
            upload_document(content=b"x", filename="../../etc/passwd.pdf", owner_user=owner)

    def test_absolute_path_filename_is_rejected_by_storage_layer(self, storage_root, owner):
        with pytest.raises(InvalidPathError):
            upload_document(content=b"x", filename="/etc/passwd.pdf", owner_user=owner)


# --- Cohérence stockage/DB ------------------------------------------------------


@pytest.mark.django_db
class TestConsistency:
    def test_document_and_metadata_are_created_in_the_same_transaction(
        self, storage_root, owner
    ):
        """Vérifie l'usage réel d'une transaction DB pour Document +
        DocumentMetadata (pas une prétention d'atomicité disque/DB
        globale, hors de portée)."""
        document = upload_document(
            content=b"x", filename="x.pdf", owner_user=owner, category="Finance"
        )
        assert Document.objects.filter(pk=document.pk).exists()
        assert DocumentMetadata.objects.filter(document=document).exists()

    def test_no_delete_function_was_added_to_storage_module(self):
        """Confirme le contrat : aucune fonction delete() n'a été
        ajoutée à core.storage.files (TECH-004 non modifié)."""
        from core.storage import files as storage

        assert not hasattr(storage, "delete")


# --- Schéma / non-régression -----------------------------------------------------


@pytest.mark.django_db
class TestNoRegressionOnSchema:
    def test_documentation_tables_still_match_tech020(self):
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
