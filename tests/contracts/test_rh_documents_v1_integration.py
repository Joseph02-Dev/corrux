"""Tests du rattachement de documents RH via documents.v1 — TECH-034.

Test de contrat côté consommateur RH + test d'intégration bout en bout
dépôt -> visibilité fiche employé, tous deux explicitement requis par
le contrat du ticket.
"""

from datetime import date

import pytest
from django.test import override_settings

from core.identity.models import User
from modules.documentation.documents_v1 import DocumentNotAccessibleError, attach, get
from modules.documentation.services import grant_permission
from modules.rh.models import EmployeeDocument
from modules.rh.services import (
    attach_document_to_employee,
    create_employee,
    link_document_to_employee,
    list_employee_documents,
)


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def employee(db):
    return create_employee(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


@pytest.fixture
def owner(db):
    return User.objects.create(username="proprietaire_rh_doc", full_name="Propriétaire")


# --- A. Dépôt + rattachement en un seul appel -----------------------------------


@pytest.mark.django_db
class TestAttachDocumentToEmployee:
    def test_creates_a_real_document_ref(self, employee, owner, storage_root):
        document_ref = attach_document_to_employee(
            employee=employee, content=b"contenu", filename="cv.pdf", owner_user=owner
        )
        assert isinstance(document_ref, int)

    def test_creates_the_employee_document_link(self, employee, owner, storage_root):
        document_ref = attach_document_to_employee(
            employee=employee, content=b"contenu", filename="cv.pdf", owner_user=owner
        )
        assert EmployeeDocument.objects.filter(
            employee=employee, document_ref=document_ref
        ).exists()

    def test_document_remains_stored_in_documentation(self, employee, owner, storage_root):
        """Critère d'acceptation explicite : « reste stocké dans
        Documentation »."""
        document_ref = attach_document_to_employee(
            employee=employee, content=b"contenu reel", filename="cv.pdf", owner_user=owner
        )

        meta = get(document_ref, owner)
        assert meta.filename == "cv.pdf"

    def test_no_file_written_outside_documentation_tree(self, employee, owner, storage_root):
        attach_document_to_employee(
            employee=employee, content=b"contenu", filename="cv.pdf", owner_user=owner
        )
        top_level_entries = {p.name for p in storage_root.iterdir()}
        assert top_level_entries == {"documentation"}


# --- B. Liaison d'un document déjà existant -------------------------------------


@pytest.mark.django_db
class TestLinkDocumentToEmployee:
    def test_links_an_existing_document(self, employee, owner, storage_root):
        document_ref = attach(content=b"x", filename="existant.pdf", owner_user=owner)

        link_document_to_employee(
            employee=employee, document_ref=document_ref, requesting_user=owner
        )

        assert EmployeeDocument.objects.filter(
            employee=employee, document_ref=document_ref
        ).exists()

    def test_refuses_a_document_the_requester_cannot_access(
        self, employee, owner, storage_root, db
    ):
        """Test de contrat côté consommateur RH : ne revérifie jamais
        aveuglément — DocumentNotAccessibleError doit se propager
        depuis documents_v1, pas être avalée."""
        document_ref = attach(content=b"x", filename="prive.pdf", owner_user=owner)
        other_user = User.objects.create(username="sans_acces_rh", full_name="Sans Accès")

        with pytest.raises(DocumentNotAccessibleError):
            link_document_to_employee(
                employee=employee, document_ref=document_ref, requesting_user=other_user
            )

    def test_refused_link_creates_no_employee_document_row(
        self, employee, owner, storage_root, db
    ):
        document_ref = attach(content=b"x", filename="prive.pdf", owner_user=owner)
        other_user = User.objects.create(username="sans_acces_rh2", full_name="Sans Accès 2")

        with pytest.raises(DocumentNotAccessibleError):
            link_document_to_employee(
                employee=employee, document_ref=document_ref, requesting_user=other_user
            )

        assert not EmployeeDocument.objects.filter(document_ref=document_ref).exists()

    def test_nonexistent_document_ref_is_refused_identically(
        self, employee, owner, storage_root
    ):
        """Non-divulgation (TECH-024) : un document_ref inexistant lève
        la même erreur qu'un document interdit."""
        with pytest.raises(DocumentNotAccessibleError):
            link_document_to_employee(
                employee=employee, document_ref=999999, requesting_user=owner
            )

    def test_linking_twice_does_not_duplicate(self, employee, owner, storage_root):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)

        link_document_to_employee(
            employee=employee, document_ref=document_ref, requesting_user=owner
        )
        link_document_to_employee(
            employee=employee, document_ref=document_ref, requesting_user=owner
        )

        count = EmployeeDocument.objects.filter(
            employee=employee, document_ref=document_ref
        ).count()
        assert count == 1


# --- C. Bout en bout — critère d'acceptation explicite --------------------------


@pytest.mark.django_db
class TestEndToEndVisibility:
    def test_deposited_document_is_visible_from_employee_record(
        self, employee, owner, storage_root
    ):
        """Critère d'acceptation explicite du ticket : « un document
        rattaché à un employé est visible depuis sa fiche RH »."""
        attach_document_to_employee(
            employee=employee, content=b"contenu", filename="contrat-signe.pdf",
            owner_user=owner,
        )

        results = list_employee_documents(employee=employee, requesting_user=owner)

        assert len(results) == 1
        assert results[0].filename == "contrat-signe.pdf"

    def test_multiple_documents_all_visible(self, employee, owner, storage_root):
        attach_document_to_employee(
            employee=employee, content=b"1", filename="a.pdf", owner_user=owner
        )
        attach_document_to_employee(
            employee=employee, content=b"2", filename="b.pdf", owner_user=owner
        )

        results = list_employee_documents(employee=employee, requesting_user=owner)

        filenames = {meta.filename for meta in results}
        assert filenames == {"a.pdf", "b.pdf"}

    def test_documents_of_other_employees_are_not_mixed_in(
        self, employee, owner, storage_root, db
    ):
        other_employee = create_employee(
            first_name="Autre", last_name="Employé", position="X",
            hire_date=date(2026, 1, 1),
        )
        attach_document_to_employee(
            employee=employee, content=b"1", filename="a.pdf", owner_user=owner
        )
        attach_document_to_employee(
            employee=other_employee, content=b"2", filename="b.pdf", owner_user=owner
        )

        results = list_employee_documents(employee=employee, requesting_user=owner)

        assert len(results) == 1
        assert results[0].filename == "a.pdf"

    def test_visibility_still_respects_documentation_permissions(
        self, employee, owner, storage_root, db
    ):
        """La visibilité « depuis la fiche RH » ne contourne jamais les
        permissions Documentation — même utilisateur sans droit ->
        rien de visible, cohérent avec TECH-023/024."""
        attach_document_to_employee(
            employee=employee, content=b"x", filename="confidentiel.pdf", owner_user=owner
        )
        other_user = User.objects.create(username="sans_droit_visib", full_name="Sans Droit")

        results = list_employee_documents(employee=employee, requesting_user=other_user)

        assert results == []

    def test_visibility_respects_explicit_grant(self, employee, owner, storage_root, db):
        document_ref = attach_document_to_employee(
            employee=employee, content=b"x", filename="partage.pdf", owner_user=owner
        )
        colleague = User.objects.create(username="collegue_rh", full_name="Collègue")
        from modules.documentation.models import Document

        grant_permission(
            actor=owner, action="read",
            document=Document.objects.get(pk=document_ref), user=colleague,
        )

        results = list_employee_documents(employee=employee, requesting_user=colleague)

        assert len(results) == 1


# --- D. Contrainte architecturale ---------------------------------------------------


class TestArchitecturalConstraint:
    def test_services_never_imports_documentation_internal_models(self):
        """Contrainte transverse impérative du ticket : aucun import
        direct des modèles internes Documentation."""
        import ast
        import inspect

        from modules.rh import services as rh_services

        source = inspect.getsource(rh_services)
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        assert "modules.documentation.models" not in imported_modules

    def test_services_never_imports_core_storage(self):
        import ast
        import inspect

        from modules.rh import services as rh_services

        source = inspect.getsource(rh_services)
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)

        assert not any("core.storage" in name for name in imported_modules)
