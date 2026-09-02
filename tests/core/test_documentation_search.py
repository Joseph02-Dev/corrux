"""Tests de la recherche documentaire — TECH-022.

Base réelle, aucun mock de DocumentPermission (contrat §15). Le
filtrage par permission est vérifié comme comportement observable réel,
pas par inspection du code.
"""

from datetime import UTC, datetime

import pytest
from django.test import override_settings

from core.authz.models import Role, UserRole
from core.identity.models import User
from modules.documentation.models import DocumentMetadata, DocumentPermission
from modules.documentation.services import create_folder, search_documents, upload_document


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def owner(db):
    return User.objects.create(
        username="proprietaire_search", password_hash="x", full_name="Propriétaire"
    )


@pytest.fixture
def other_user(db):
    return User.objects.create(username="autre_utilisateur", password_hash="x", full_name="Autre")


def _dt(day: int) -> datetime:
    return datetime(2026, 3, day, tzinfo=UTC)


def _upload(owner_user, filename="x.pdf", **kwargs):
    return upload_document(content=b"contenu", filename=filename, owner_user=owner_user, **kwargs)


def _touch_created_at(document, when):
    from modules.documentation.models import Document

    Document.objects.filter(pk=document.pk).update(created_at=when)
    document.refresh_from_db()


# --- 1-4. Mot-clé ------------------------------------------------------------


@pytest.mark.django_db
class TestKeyword:
    def test_search_by_filename(self, storage_root, owner):
        target = _upload(owner, filename="rapport-annuel.pdf")
        _upload(owner, filename="autre.pdf")

        results = search_documents(user=owner, keyword="rapport")
        assert results == [target]

    def test_search_by_metadata_value(self, storage_root, owner):
        target = _upload(owner, filename="x.pdf")
        DocumentMetadata.objects.create(document=target, key="categorie", value="Finance")
        _upload(owner, filename="y.pdf")

        results = search_documents(user=owner, keyword="finance")
        assert results == [target]

    def test_search_is_case_insensitive(self, storage_root, owner):
        target = _upload(owner, filename="RAPPORT.pdf")
        results = search_documents(user=owner, keyword="rapport")
        assert results == [target]

    def test_search_is_partial_match(self, storage_root, owner):
        target = _upload(owner, filename="rapport-annuel-2026.pdf")
        results = search_documents(user=owner, keyword="annuel")
        assert results == [target]


# --- 5-7. Filtre type / dossier -----------------------------------------------


@pytest.mark.django_db
class TestTypeAndFolderFilters:
    def test_filter_by_mime_type(self, storage_root, owner):
        pdf = _upload(owner, filename="x.pdf")
        _upload(owner, filename="y.jpg")

        results = search_documents(user=owner, mime_type="application/pdf")
        assert results == [pdf]

    def test_filter_by_direct_folder(self, storage_root, owner):
        folder = create_folder(name="Contrats")
        inside = _upload(owner, filename="x.pdf", folder=folder)
        _upload(owner, filename="y.pdf")  # racine

        results = search_documents(user=owner, folder=folder)
        assert results == [inside]

    def test_subfolders_are_not_implicitly_included(self, storage_root, owner):
        parent = create_folder(name="Parent")
        child = create_folder(name="Enfant", parent=parent)
        in_child = _upload(owner, filename="dans-enfant.pdf", folder=child)

        results = search_documents(user=owner, folder=parent)

        assert in_child not in results
        assert results == []

    def test_filter_by_root_folder_none(self, storage_root, owner):
        root_doc = _upload(owner, filename="racine.pdf")
        folder = create_folder(name="Ailleurs")
        _upload(owner, filename="ailleurs.pdf", folder=folder)

        results = search_documents(user=owner, folder=None)
        assert results == [root_doc]


# --- 8. Filtre date ------------------------------------------------------------


@pytest.mark.django_db
class TestDateFilter:
    def test_filter_by_created_after(self, storage_root, owner):
        old = _upload(owner, filename="ancien.pdf")
        _touch_created_at(old, _dt(1))
        recent = _upload(owner, filename="recent.pdf")
        _touch_created_at(recent, _dt(20))

        results = search_documents(user=owner, created_after=_dt(10))
        assert results == [recent]

    def test_filter_by_created_before(self, storage_root, owner):
        old = _upload(owner, filename="ancien.pdf")
        _touch_created_at(old, _dt(1))
        recent = _upload(owner, filename="recent.pdf")
        _touch_created_at(recent, _dt(20))

        results = search_documents(user=owner, created_before=_dt(10))
        assert results == [old]


# --- 9. Combinaison de filtres ---------------------------------------------------


@pytest.mark.django_db
class TestCombinedFilters:
    def test_all_filters_combined_with_and(self, storage_root, owner):
        folder = create_folder(name="Cible")
        match = _upload(owner, filename="rapport.pdf", folder=folder)
        _touch_created_at(match, _dt(15))

        wrong_keyword = _upload(owner, filename="autre.pdf", folder=folder)
        _touch_created_at(wrong_keyword, _dt(15))

        wrong_folder = _upload(owner, filename="rapport.pdf")
        _touch_created_at(wrong_folder, _dt(15))

        wrong_date = _upload(owner, filename="rapport.pdf", folder=folder)
        _touch_created_at(wrong_date, _dt(1))

        results = search_documents(
            user=owner, keyword="rapport", folder=folder, created_after=_dt(10)
        )
        assert results == [match]


# --- 10. Absence de doublons ---------------------------------------------------


@pytest.mark.django_db
class TestNoDuplicates:
    def test_no_duplicates_when_multiple_metadata_entries_match(self, storage_root, owner):
        document = _upload(owner, filename="x.pdf")
        DocumentMetadata.objects.create(document=document, key="categorie", value="Finance RH")
        DocumentMetadata.objects.create(document=document, key="notes", value="Finance urgent")

        results = search_documents(user=owner, keyword="finance")

        assert results.count(document) == 1
        assert len(results) == 1

    def test_no_duplicates_from_multiple_permission_rows(self, storage_root, owner):
        role_a = Role.objects.create(name="role-search-a")
        role_b = Role.objects.create(name="role-search-b")
        UserRole.objects.create(user=owner, role=role_a)
        UserRole.objects.create(user=owner, role=role_b)

        other_owner = User.objects.create(username="tiers", password_hash="x", full_name="Tiers")
        document = _upload(other_owner, filename="partage.pdf")
        DocumentPermission.objects.create(document=document, role=role_a, action="read")
        DocumentPermission.objects.create(document=document, role=role_b, action="read")

        results = search_documents(user=owner, keyword="partage")
        assert len(results) == 1


# --- 11. Recherche sans mot-clé ---------------------------------------------------


@pytest.mark.django_db
class TestNoKeywordStillFilters:
    def test_other_filters_work_without_keyword(self, storage_root, owner):
        folder = create_folder(name="Sans mot-cle")
        target = _upload(owner, filename="x.pdf", folder=folder)
        _upload(owner, filename="y.pdf")

        results = search_documents(user=owner, folder=folder)
        assert results == [target]

    def test_no_filters_at_all_returns_all_visible_documents(self, storage_root, owner):
        a = _upload(owner, filename="a.pdf")
        b = _upload(owner, filename="b.pdf")

        results = search_documents(user=owner)
        assert set(results) == {a, b}


# --- 12-15. Permissions --------------------------------------------------------


@pytest.mark.django_db
class TestPermissions:
    def test_owner_sees_their_own_document(self, storage_root, owner):
        document = _upload(owner, filename="prive.pdf")
        results = search_documents(user=owner, keyword="prive")
        assert results == [document]

    def test_user_without_any_access_does_not_see_the_document(
        self, storage_root, owner, other_user
    ):
        _upload(owner, filename="prive.pdf")
        results = search_documents(user=other_user, keyword="prive")
        assert results == []

    def test_exact_keyword_match_on_forbidden_document_returns_nothing(
        self, storage_root, owner, other_user
    ):
        """Cas critique explicite du contrat : mot-clé EXACT du nom d'un
        document interdit -> aucun résultat pour l'utilisateur sans droit."""
        _upload(owner, filename="rapport-confidentiel.pdf")

        results = search_documents(user=other_user, keyword="rapport-confidentiel.pdf")

        assert results == []

    def test_user_with_individual_permission_sees_the_document(
        self, storage_root, owner, other_user
    ):
        document = _upload(owner, filename="partage-individuel.pdf")
        DocumentPermission.objects.create(document=document, user=other_user, action="read")

        results = search_documents(user=other_user, keyword="partage-individuel")
        assert results == [document]

    def test_user_with_role_permission_sees_the_document(self, storage_root, owner, other_user):
        role = Role.objects.create(name="role-search-lecteur")
        UserRole.objects.create(user=other_user, role=role)
        document = _upload(owner, filename="partage-role.pdf")
        DocumentPermission.objects.create(document=document, role=role, action="read")

        results = search_documents(user=other_user, keyword="partage-role")
        assert results == [document]

    def test_different_users_get_different_result_sets(self, storage_root, owner, other_user):
        """Le même mot-clé produit des ensembles de résultats différents
        selon les permissions de chaque utilisateur."""
        shared = _upload(owner, filename="doc-permission.pdf")
        DocumentPermission.objects.create(document=shared, user=other_user, action="read")
        private = _upload(owner, filename="doc-permission-prive.pdf")

        owner_results = search_documents(user=owner, keyword="doc-permission")
        other_results = search_documents(user=other_user, keyword="doc-permission")

        assert set(owner_results) == {shared, private}
        assert set(other_results) == {shared}

    def test_permission_action_write_does_not_grant_search_visibility(
        self, storage_root, owner, other_user
    ):
        """Seule action="read" doit accorder la visibilité en recherche."""
        document = _upload(owner, filename="ecriture-seule.pdf")
        DocumentPermission.objects.create(document=document, user=other_user, action="write")

        results = search_documents(user=other_user, keyword="ecriture-seule")
        assert results == []


# --- 16. Document sans métadonnée correspondante --------------------------------


@pytest.mark.django_db
class TestNoMatchingMetadata:
    def test_document_without_matching_metadata_is_not_returned_by_metadata_search(
        self, storage_root, owner
    ):
        document = _upload(owner, filename="autre-nom.pdf")
        DocumentMetadata.objects.create(document=document, key="categorie", value="Juridique")

        results = search_documents(user=owner, keyword="inexistant")
        assert document not in results


# --- 17. Aucun résultat ------------------------------------------------------------


@pytest.mark.django_db
class TestNoResult:
    def test_search_with_no_match_returns_empty_list(self, storage_root, owner):
        _upload(owner, filename="x.pdf")
        results = search_documents(user=owner, keyword="zzzintrouvable")
        assert results == []


# --- 18. Aucun accès au contenu binaire ---------------------------------------------


@pytest.mark.django_db
class TestNoFileAccess:
    def test_search_does_not_read_file_content(self, storage_root, owner, monkeypatch):
        """Vérifie explicitement qu'aucun appel à storage.read() n'est
        effectué pendant une recherche."""
        from core.storage import files as storage

        _upload(owner, filename="x.pdf")

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("search_documents ne doit jamais lire le contenu du fichier")

        monkeypatch.setattr(storage, "read", _fail_if_called)

        search_documents(user=owner, keyword="x")  # ne doit pas lever
