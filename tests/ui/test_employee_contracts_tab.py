"""Tests de l'onglet Contrats + Drawer contrat + sélecteur de document
en mode sélection — UI-404 (Option A confirmée, audit Phase 1).
"""

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.documentation.documents_v1 import attach
from modules.documentation.services import create_folder, grant_permission
from modules.rh.models import Contract
from modules.rh.services import create_contract, create_employee


def _contracts_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/contrats/"


def _create_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/contrats/nouveau/"


def _edit_url(employee_id: int, contract_id: int) -> str:
    return f"/employes/{employee_id}/contrats/{contract_id}/modifier/"


def _picker_url(contract_id: int) -> str:
    return f"/documents/pour-contrat/{contract_id}/"


def _picker_select_url(contract_id: int, document_id: int) -> str:
    return f"/documents/pour-contrat/{contract_id}/choisir/{document_id}/"


def _picker_upload_url(contract_id: int) -> str:
    return f"/documents/pour-contrat/{contract_id}/deposer/"


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
def reader(db):
    user = User.objects.create(username="lecteur_contrats", full_name="Lecteur")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    return user


@pytest.fixture
def writer(db):
    user = User.objects.create(username="rh_admin_contrats", full_name="Admin RH")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    _grant(user, "rh", "employee", "write")
    _grant(user, "documentation", "document", "read")
    return user


@pytest.fixture
def employee(rh_activated, documentation_activated):
    return create_employee(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


def _pdf_file(name="doc.pdf", content=b"contenu"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


# --- A. Accès à l'onglet ---------------------------------------------------------


@pytest.mark.django_db
class TestTabAccess:
    def test_reader_sees_the_tab(self, employee, reader):
        client = _authenticated_client(reader)
        assert client.get(_contracts_url(employee.id)).status_code == 200

    def test_anonymous_redirected(self, employee):
        response = Client().get(_contracts_url(employee.id))
        assert response.status_code == 302

    def test_post_is_rejected_on_tab(self, employee, reader):
        client = _authenticated_client(reader)
        assert client.post(_contracts_url(employee.id)).status_code == 405

    def test_create_button_hidden_for_reader(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_contracts_url(employee.id)).content.decode()
        assert "create-contract" not in content


# --- B. Création de contrat -------------------------------------------------------


@pytest.mark.django_db
class TestCreateContract:
    def test_create_adds_a_real_contract(self, employee, writer):
        client = _authenticated_client(writer)
        response = client.post(
            _create_url(employee.id),
            {"type": "CDI", "start_date": "2026-01-01", "end_date": "", "status": "active"},
        )
        assert response.status_code == 302
        assert Contract.objects.filter(employee=employee, type="CDI").exists()

    def test_created_contract_appears_in_the_list(self, employee, writer):
        client = _authenticated_client(writer)
        client.post(
            _create_url(employee.id),
            {
                "type": "CDD", "start_date": "2026-01-01", "end_date": "2026-06-30",
                "status": "active",
            },
        )
        content = client.get(_contracts_url(employee.id)).content.decode()
        assert "CDD" in content

    def test_missing_type_reshows_form_with_error(self, employee, writer):
        client = _authenticated_client(writer)
        response = client.post(
            _create_url(employee.id),
            {"type": "", "start_date": "2026-01-01", "end_date": "", "status": "active"},
        )
        assert response.status_code == 400
        assert not Contract.objects.filter(employee=employee).exists()

    def test_create_without_write_is_refused(self, employee, reader):
        client = _authenticated_client(reader)
        response = client.post(
            _create_url(employee.id),
            {"type": "CDI", "start_date": "2026-01-01", "end_date": "", "status": "active"},
        )
        assert response.status_code == 403


# --- C. Édition de contrat ---------------------------------------------------------


@pytest.mark.django_db
class TestEditContract:
    def test_edit_updates_the_contract(self, employee, writer):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(writer)
        response = client.post(
            _edit_url(employee.id, contract.id),
            {
                "type": "CDD", "start_date": "2026-02-01", "end_date": "2026-12-31",
                "status": "expired",
            },
        )
        assert response.status_code == 302
        contract.refresh_from_db()
        assert contract.type == "CDD"
        assert contract.status == Contract.Status.EXPIRED

    def test_put_is_rejected(self, employee, writer):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(writer)
        response = client.put(_edit_url(employee.id, contract.id))
        assert response.status_code == 405


# --- D. Sélecteur — accès à deux niveaux ------------------------------------------


@pytest.mark.django_db
class TestPickerAccess:
    def test_writer_sees_the_picker(self, employee, writer, storage_root):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(writer)
        assert client.get(_picker_url(contract.id)).status_code == 200

    def test_reader_without_write_is_refused(self, employee, reader, storage_root):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(reader)
        response = client.get(_picker_url(contract.id))
        assert response.status_code == 403

    def test_write_without_documentation_permission_is_refused(self, employee, db, storage_root):
        user = User.objects.create(username="rh_sans_doc", full_name="RH Sans Doc")
        auth.set_user_password(user, "Password123!")
        user.save()
        _grant(user, "rh", "employee", "write")
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        client = _authenticated_client(user)
        response = client.get(_picker_url(contract.id))
        assert response.status_code == 403


# --- E. Sélection — cas explicitement requis par le contrat ---------------------


@pytest.mark.django_db
class TestDocumentSelection:
    def test_create_contract_and_link_existing_document(self, employee, writer, storage_root):
        """Test explicitement requis par le contrat du ticket :
        création de contrat avec liaison d'un document existant."""
        document_ref = attach(
            content=b"contrat pdf", filename="contrat-signe.pdf", owner_user=writer
        )
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        client = _authenticated_client(writer)
        response = client.post(_picker_select_url(contract.id, document_ref))

        assert response.status_code == 302
        contract.refresh_from_db()
        assert contract.document_ref == document_ref

    def test_linked_document_points_to_the_real_document(self, employee, writer, storage_root):
        """Critère d'acceptation explicite : « document lié pointe vers
        le document réel »."""
        from modules.documentation.documents_v1 import get as documents_v1_get

        document_ref = attach(content=b"x", filename="reel.pdf", owner_user=writer)
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        client = _authenticated_client(writer)
        client.post(_picker_select_url(contract.id, document_ref))

        contract.refresh_from_db()
        meta = documents_v1_get(contract.document_ref, writer)
        assert meta.filename == "reel.pdf"

    def test_no_copy_of_file_is_stored_in_rh(self, employee, writer, storage_root):
        """Critère d'acceptation explicite : « aucune copie de fichier
        dans RH »."""
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=writer)
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        client = _authenticated_client(writer)
        client.post(_picker_select_url(contract.id, document_ref))

        top_level_entries = {p.name for p in storage_root.iterdir()}
        assert top_level_entries == {"documentation"}

    def test_selecting_an_inaccessible_document_is_refused(self, employee, writer, storage_root):
        owner = User.objects.create(username="proprietaire_prive_404", full_name="Autre")
        document_ref = attach(content=b"x", filename="prive.pdf", owner_user=owner)
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        client = _authenticated_client(writer)
        response = client.post(_picker_select_url(contract.id, document_ref))

        assert response.status_code == 403
        contract.refresh_from_db()
        assert contract.document_ref is None

    def test_picker_navigation_into_a_folder(self, employee, writer, storage_root):
        folder = create_folder(name="Contrats")
        grant_permission(actor=writer, action="read", folder=folder, user=writer)
        attach(content=b"x", filename="dans-dossier.pdf", owner_user=writer, folder=folder)
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        client = _authenticated_client(writer)
        content = client.get(
            f"/documents/pour-contrat/{contract.id}/dossier/{folder.id}/"
        ).content.decode()

        assert "dans-dossier.pdf" in content

    def test_edit_drawer_shows_linked_document_reference(self, employee, writer, storage_root):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=writer)
        create_contract(
            employee=employee, type="CDI", start_date=date(2026, 1, 1), document_ref=document_ref
        )

        client = _authenticated_client(writer)
        content = client.get(_contracts_url(employee.id)).content.decode()

        assert f"Document #{document_ref}" in content

    def test_create_drawer_has_no_document_link_action(self, employee, writer):
        """« Document lié » n'est proposé qu'en édition — le contrat
        doit déjà exister pour cibler le sélecteur."""
        client = _authenticated_client(writer)
        content = client.get(_contracts_url(employee.id)).content.decode()
        create_block = content.split('id="create-contract"')[1].split("</dialog>")[0]
        assert "pour-contrat" not in create_block


# --- F. Dépôt depuis le sélecteur -------------------------------------------------


@pytest.mark.django_db
class TestPickerUpload:
    def test_upload_from_picker_links_automatically(self, employee, writer, storage_root):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(writer)

        response = client.post(
            _picker_upload_url(contract.id), {"file": _pdf_file(name="nouveau.pdf")}
        )

        assert response.status_code == 302
        contract.refresh_from_db()
        assert contract.document_ref is not None

    def test_uploaded_via_picker_document_is_findable(self, employee, writer, storage_root):
        from modules.documentation.documents_v1 import get as documents_v1_get

        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(writer)
        client.post(
            _picker_upload_url(contract.id), {"file": _pdf_file(name="depuis-picker.pdf")}
        )

        contract.refresh_from_db()
        meta = documents_v1_get(contract.document_ref, writer)
        assert meta.filename == "depuis-picker.pdf"

    def test_disallowed_extension_shows_error_without_linking(
        self, employee, writer, storage_root
    ):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(writer)

        response = client.post(
            _picker_upload_url(contract.id),
            {
                "file": SimpleUploadedFile(
                    "bad.exe", b"x", content_type="application/octet-stream"
                )
            },
        )

        assert "corrux-error-state" in response.content.decode()
        contract.refresh_from_db()
        assert contract.document_ref is None


# --- G. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_contract_type_is_html_escaped(self, employee, writer):
        client = _authenticated_client(writer)
        client.post(
            _create_url(employee.id),
            {
                "type": "<script>alert(1)</script>", "start_date": "2026-01-01",
                "end_date": "", "status": "active",
            },
        )
        content = client.get(_contracts_url(employee.id)).content.decode()
        assert "<script>alert(1)</script>" not in content

    def test_picker_select_get_is_rejected(self, employee, writer, storage_root):
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=writer)
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        client = _authenticated_client(writer)
        response = client.get(_picker_select_url(contract.id, document_ref))
        assert response.status_code == 405
