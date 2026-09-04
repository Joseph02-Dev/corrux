"""Tests de l'onglet Documents de la fiche employé — UI-403.

Réutilise list_employee_documents()/attach_document_to_employee()
(TECH-034) — décision confirmée (Option B, audit Phase 1) : aucune
navigation par dossier, une liste filtrée par la table de liaison RH.
"""

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.documentation.documents_v1 import get as documents_v1_get
from modules.rh.models import EmployeeDocument
from modules.rh.services import create_employee


def _documents_tab_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/documents/"


def _upload_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/documents/deposer/"


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def rh_activated(db):
    return Module.objects.create(
        id="rh", name="Ressources Humaines", version="1.0.0",
        state=Module.State.ACTIVATED, manifest_snapshot={},
    )


@pytest.fixture
def documentation_activated(db):
    return Module.objects.create(
        id="documentation", name="Documentation / Archivage", version="1.0.0",
        state=Module.State.ACTIVATED, manifest_snapshot={},
    )


def _authenticated_client(user) -> Client:
    client = Client()
    session = client.session
    session[auth.SESSION_USER_ID_KEY] = user.id
    session.save()
    client.cookies["sessionid"] = session.session_key
    return client


def _grant(user, module_id, resource, action):
    role = Role.objects.create(name=f"role-{user.username}-{resource}-{action}")
    permission, _ = Permission.objects.get_or_create(
        module_id=module_id, resource=resource, action=action
    )
    RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


@pytest.fixture
def full_access_user(db):
    user = User.objects.create(username="acces_complet_doc_emp", full_name="Accès Complet")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    _grant(user, "documentation", "document", "read")
    return user


@pytest.fixture
def rh_only_user(db):
    user = User.objects.create(username="rh_seulement", full_name="RH Seulement")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    return user


@pytest.fixture
def employee(rh_activated, documentation_activated):
    return create_employee(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


def _pdf_file(name="cv.pdf", content=b"contenu"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


# --- A. Accès — modèle à deux niveaux -------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_full_access_user_sees_the_tab(self, employee, full_access_user, storage_root):
        client = _authenticated_client(full_access_user)
        assert client.get(_documents_tab_url(employee.id)).status_code == 200

    def test_rh_permission_alone_is_not_enough_for_tab_content(
        self, employee, rh_only_user, storage_root
    ):
        """« Les permissions appliquées sont celles du module
        Documentation » (maquette) : rh.employee.read seul ne suffit
        pas à voir le CONTENU de cet onglet."""
        client = _authenticated_client(rh_only_user)
        response = client.get(_documents_tab_url(employee.id))
        assert response.status_code == 403

    def test_tab_permission_denied_still_shows_header_and_tabs(
        self, employee, rh_only_user, storage_root
    ):
        """État "Permission denied" local à l'onglet (maquette) — pas
        la page entière masquée."""
        client = _authenticated_client(rh_only_user)
        content = client.get(_documents_tab_url(employee.id)).content.decode()
        assert "Jean Dupont" in content
        assert "corrux-permission-denied" in content

    def test_no_rh_permission_at_all_shows_full_page_denial(self, employee, db, storage_root):
        user = User.objects.create(username="aucun_droit_doc_emp", full_name="Aucun Droit")
        auth.set_user_password(user, "Password123!")
        user.save()
        client = _authenticated_client(user)
        response = client.get(_documents_tab_url(employee.id))
        assert response.status_code == 403
        assert "Jean Dupont" not in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self, employee):
        client = Client()
        response = client.get(_documents_tab_url(employee.id))
        assert response.status_code == 302

    def test_post_is_rejected_on_the_tab(self, employee, full_access_user, storage_root):
        client = _authenticated_client(full_access_user)
        response = client.post(_documents_tab_url(employee.id))
        assert response.status_code == 405


# --- B. Liste ------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentsList:
    def test_empty_state(self, employee, full_access_user, storage_root):
        client = _authenticated_client(full_access_user)
        content = client.get(_documents_tab_url(employee.id)).content.decode()
        assert "corrux-empty-state" in content

    def test_linked_documents_are_listed(self, employee, full_access_user, storage_root):
        client = _authenticated_client(full_access_user)
        client.post(_upload_url(employee.id), {"file": _pdf_file(name="rapport.pdf")})

        content = client.get(_documents_tab_url(employee.id)).content.decode()
        assert "rapport.pdf" in content


# --- C. Dépôt bout en bout — cas explicitement requis par le contrat -----------


@pytest.mark.django_db
class TestUploadEndToEnd:
    def test_upload_creates_a_real_employee_document_link(
        self, employee, full_access_user, storage_root
    ):
        client = _authenticated_client(full_access_user)
        response = client.post(_upload_url(employee.id), {"file": _pdf_file()})

        assert response.status_code == 302
        assert EmployeeDocument.objects.filter(employee=employee).exists()

    def test_uploaded_document_remains_stored_in_documentation(
        self, employee, full_access_user, storage_root
    ):
        """Critère d'acceptation explicite du ticket : visible dans
        Documentation."""
        client = _authenticated_client(full_access_user)
        client.post(_upload_url(employee.id), {"file": _pdf_file(name="contrat.pdf")})

        link = EmployeeDocument.objects.get(employee=employee)
        meta = documents_v1_get(link.document_ref, full_access_user)
        assert meta.filename == "contrat.pdf"

    def test_uploaded_document_is_visible_in_the_tab(
        self, employee, full_access_user, storage_root
    ):
        """Critère d'acceptation explicite du ticket : visible dans
        l'onglet — bout en bout."""
        client = _authenticated_client(full_access_user)
        client.post(_upload_url(employee.id), {"file": _pdf_file(name="visible-onglet.pdf")})

        content = client.get(_documents_tab_url(employee.id)).content.decode()
        assert "visible-onglet.pdf" in content

    def test_no_file_written_outside_documentation_tree(
        self, employee, full_access_user, storage_root
    ):
        client = _authenticated_client(full_access_user)
        client.post(_upload_url(employee.id), {"file": _pdf_file()})

        top_level_entries = {p.name for p in storage_root.iterdir()}
        assert top_level_entries == {"documentation"}

    def test_category_is_saved(self, employee, full_access_user, storage_root):
        client = _authenticated_client(full_access_user)
        client.post(
            _upload_url(employee.id), {"file": _pdf_file(), "category": "Contrats"}
        )

        link = EmployeeDocument.objects.get(employee=employee)
        from modules.documentation.models import Document
        from modules.documentation.services import document_metadata_value

        document = Document.objects.get(pk=link.document_ref)
        assert document_metadata_value(document, "categorie") == "Contrats"


# --- D. Échecs ------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadFailures:
    def test_disallowed_extension_shows_explicit_error(
        self, employee, full_access_user, storage_root
    ):
        client = _authenticated_client(full_access_user)
        response = client.post(
            _upload_url(employee.id),
            {
                "file": SimpleUploadedFile(
                    "bad.exe", b"x", content_type="application/octet-stream"
                )
            },
        )
        assert response.status_code == 200
        assert "corrux-error-state" in response.content.decode()
        assert not EmployeeDocument.objects.filter(employee=employee).exists()

    def test_no_file_selected_shows_explicit_error(
        self, employee, full_access_user, storage_root
    ):
        client = _authenticated_client(full_access_user)
        response = client.post(_upload_url(employee.id), {})
        assert "Veuillez sélectionner un fichier" in response.content.decode()

    def test_upload_requires_documentation_permission(
        self, employee, rh_only_user, storage_root
    ):
        client = _authenticated_client(rh_only_user)
        response = client.post(_upload_url(employee.id), {"file": _pdf_file()})
        assert response.status_code == 403
        assert not EmployeeDocument.objects.filter(employee=employee).exists()


# --- E. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_filename_is_html_escaped(self, employee, full_access_user, storage_root):
        client = _authenticated_client(full_access_user)
        client.post(
            _upload_url(employee.id),
            {"file": _pdf_file(name="a<script>b.pdf")},
        )
        content = client.get(_documents_tab_url(employee.id)).content.decode()
        assert "<script>b" not in content

    def test_upload_wrong_method_is_rejected(self, employee, full_access_user, storage_root):
        client = _authenticated_client(full_access_user)
        response = client.put(_upload_url(employee.id))
        assert response.status_code == 405
