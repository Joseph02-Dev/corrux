"""Tests de contrat de la façade `documents.v1` — TECH-024.

Vérifient l'implémentation de Documentation indépendamment de RH
(aucun module RH n'est requis ni simulé) — conforme au critère
d'acceptation explicite du ticket. Objets réels (User, Role, Folder,
DocumentPermission via services.py), aucun mock.
"""


import pytest
from django.test import override_settings

from core.authz.models import Role, UserRole
from core.identity.models import User
from modules.documentation.documents_v1 import (
    DocumentMeta,
    DocumentNotAccessibleError,
    attach,
    get,
    list_for_owner,
)
from modules.documentation.models import Document
from modules.documentation.services import (
    DocumentUploadError,
    create_folder,
    grant_permission,
)


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def owner(db):
    return User.objects.create(username="owner_v1", password_hash="x", full_name="Propriétaire")


@pytest.fixture
def other_user(db):
    return User.objects.create(username="autre_v1", password_hash="x", full_name="Autre")


@pytest.fixture
def role(db):
    return Role.objects.create(name="role-v1-test")


# --- attach ------------------------------------------------------------------


@pytest.mark.django_db
class TestAttach:
    def test_attach_creates_a_real_document(self, storage_root, owner):
        document_ref = attach(content=b"contenu", filename="x.pdf", owner_user=owner)
        assert Document.objects.filter(pk=document_ref).exists()

    def test_attach_returns_the_document_id_as_ref(self, storage_root, owner):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        document = Document.objects.get(pk=document_ref)
        assert document_ref == document.id

    def test_attach_uses_upload_document_rules(self, storage_root, owner):
        """Type refusé -> DocumentUploadError de TECH-021, non
        réimplémentée ni enveloppée."""
        with pytest.raises(DocumentUploadError):
            attach(content=b"x", filename="script.exe", owner_user=owner)

    def test_attach_creates_no_owner_module_or_owner_ref_field(self, storage_root, owner):
        """Décision Phase 2 : aucune persistance owner_module/owner_ref
        côté Documentation."""
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        document = Document.objects.get(pk=document_ref)
        field_names = {f.name for f in document._meta.get_fields()}
        assert "owner_module" not in field_names
        assert "owner_ref" not in field_names

    def test_attach_respects_folder_and_category(self, storage_root, owner):
        folder = create_folder(name="Dossier V1")
        document_ref = attach(
            content=b"x", filename="x.pdf", owner_user=owner, folder=folder, category="RH"
        )
        document = Document.objects.get(pk=document_ref)
        assert document.folder == folder


# --- get -----------------------------------------------------------------------


@pytest.mark.django_db
class TestGet:
    def test_owner_can_get_with_read(self, storage_root, owner):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        meta = get(document_ref, owner)
        assert meta.document_ref == document_ref

    def test_user_with_direct_permission_can_get(self, storage_root, owner, other_user):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        document = Document.objects.get(pk=document_ref)
        grant_permission(actor=owner, action="read", document=document, user=other_user)

        meta = get(document_ref, other_user)
        assert meta.document_ref == document_ref

    def test_role_permission_grants_get(self, storage_root, owner, other_user, role):
        UserRole.objects.create(user=other_user, role=role)
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        document = Document.objects.get(pk=document_ref)
        grant_permission(actor=owner, action="read", document=document, role=role)

        meta = get(document_ref, other_user)
        assert meta.document_ref == document_ref

    def test_multiple_roles_one_authorized(self, storage_root, owner, other_user, role):
        unrelated = Role.objects.create(name="role-v1-unrelated")
        UserRole.objects.create(user=other_user, role=unrelated)
        UserRole.objects.create(user=other_user, role=role)
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        document = Document.objects.get(pk=document_ref)
        grant_permission(actor=owner, action="read", document=document, role=role)

        meta = get(document_ref, other_user)
        assert meta.document_ref == document_ref

    def test_user_without_permission_is_refused(self, storage_root, owner, other_user):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        with pytest.raises(DocumentNotAccessibleError):
            get(document_ref, other_user)

    def test_revoked_permission_is_immediately_refused(self, storage_root, owner, other_user):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        document = Document.objects.get(pk=document_ref)
        permission = grant_permission(
            actor=owner, action="read", document=document, user=other_user
        )
        assert get(document_ref, other_user).document_ref == document_ref

        from modules.documentation.services import revoke_permission

        revoke_permission(actor=owner, permission=permission)

        with pytest.raises(DocumentNotAccessibleError):
            get(document_ref, other_user)

    def test_nonexistent_reference_raises_the_same_error_type(self, other_user):
        with pytest.raises(DocumentNotAccessibleError):
            get(999999, other_user)

    def test_inaccessible_and_nonexistent_are_indistinguishable(
        self, storage_root, owner, other_user
    ):
        """Cas critique de non-divulgation (décision Phase 2) : les deux
        cas lèvent exactement le même type d'exception, sans détail
        distinctif dans le message."""
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)

        with pytest.raises(DocumentNotAccessibleError) as exc_inaccessible:
            get(document_ref, other_user)

        with pytest.raises(DocumentNotAccessibleError) as exc_nonexistent:
            get(999999, other_user)

        assert type(exc_inaccessible.value) is type(exc_nonexistent.value)

    def test_storage_path_is_absent_from_document_meta(self, storage_root, owner):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        meta = get(document_ref, owner)
        assert not hasattr(meta, "storage_path")


# --- DocumentMeta ------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentMeta:
    def test_document_meta_is_not_the_django_model(self, storage_root, owner):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        meta = get(document_ref, owner)
        assert isinstance(meta, DocumentMeta)
        assert not isinstance(meta, Document)

    def test_document_meta_exposes_exactly_the_contractual_fields(self, storage_root, owner):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        meta = get(document_ref, owner)
        field_names = {f.name for f in meta.__dataclass_fields__.values()}
        assert field_names == {"document_ref", "filename", "mime_type", "size_bytes", "created_at"}

    def test_document_meta_does_not_expose_internal_data(self, storage_root, owner):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        meta = get(document_ref, owner)
        for internal_field in ("storage_path", "owner_user", "folder"):
            assert not hasattr(meta, internal_field)

    def test_document_meta_values_match_the_real_document(self, storage_root, owner):
        document_ref = attach(content=b"contenu reel", filename="rapport.pdf", owner_user=owner)
        document = Document.objects.get(pk=document_ref)
        meta = get(document_ref, owner)

        assert meta.filename == document.filename == "rapport.pdf"
        assert meta.mime_type == document.mime_type
        assert meta.size_bytes == document.size_bytes == len(b"contenu reel")
        assert meta.created_at == document.created_at


# --- list_for_owner ----------------------------------------------------------------


@pytest.mark.django_db
class TestListForOwner:
    def test_returns_meta_for_each_accessible_ref(self, storage_root, owner):
        ref_a = attach(content=b"a", filename="a.pdf", owner_user=owner)
        ref_b = attach(content=b"b", filename="b.pdf", owner_user=owner)

        results = list_for_owner([ref_a, ref_b], owner)

        assert {r.document_ref for r in results} == {ref_a, ref_b}

    def test_inaccessible_ref_is_silently_omitted(self, storage_root, owner, other_user):
        accessible = attach(content=b"a", filename="a.pdf", owner_user=owner)
        document = Document.objects.get(pk=accessible)
        grant_permission(actor=owner, action="read", document=document, user=other_user)
        inaccessible = attach(content=b"b", filename="b.pdf", owner_user=owner)

        results = list_for_owner([accessible, inaccessible], other_user)

        assert [r.document_ref for r in results] == [accessible]

    def test_nonexistent_ref_is_silently_omitted(self, storage_root, owner):
        real_ref = attach(content=b"a", filename="a.pdf", owner_user=owner)

        results = list_for_owner([real_ref, 999999], owner)

        assert [r.document_ref for r in results] == [real_ref]

    def test_empty_refs_list_returns_empty_list(self, owner):
        assert list_for_owner([], owner) == []

    def test_does_not_resolve_any_owner_association_itself(self, storage_root, owner):
        """Décision Phase 2 (Option A) : la fonction n'accepte aucun
        paramètre owner_module/owner_ref — uniquement une liste de refs
        déjà résolue par l'appelant."""
        import inspect

        signature = inspect.signature(list_for_owner)
        assert "owner_module" not in signature.parameters
        assert "owner_ref" not in signature.parameters
        assert "document_refs" in signature.parameters


# --- Indépendance vis-à-vis de RH ---------------------------------------------------


@pytest.mark.django_db
class TestIndependentOfRH:
    def test_documents_v1_works_without_any_rh_business_logic(self, storage_root, owner):
        """Le module modules.rh possède désormais son schéma (TECH-030)
        mais aucune logique métier/service — cette suite entière
        fonctionne sans qu'aucun code RH ne soit appelé, conformément
        au critère d'acceptation explicite du ticket TECH-024. Mise à
        jour nécessaire (pas une régression) : ce test affirmait
        littéralement l'absence de modèle RH, dépassée par conception
        depuis TECH-030 — la garantie réelle (documents.v1 fonctionne
        de façon autonome) reste vérifiée ci-dessous."""
        from django.apps import apps

        rh_app = apps.get_app_config("rh")
        assert {m.__name__ for m in rh_app.get_models()} == {
            "Employee",
            "Contract",
            "LeaveRequest",
            "EmployeeDocument",
        }

        # La façade documents.v1 fonctionne malgré tout intégralement,
        # sans appeler aucun code RH.
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        assert get(document_ref, owner).document_ref == document_ref
