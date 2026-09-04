"""Tests de la liste Employés + Drawer création/édition — UI-401.

Rendu HTTP réel, permissions réelles.
"""

from datetime import date

import pytest
from django.test import Client

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.rh.models import Contract, Employee, EmployeeDocument, LeaveRequest
from modules.rh.services import create_employee, deactivate_employee

LIST_URL = "/employes/"
CREATE_URL = "/employes/nouveau/"


def _edit_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/modifier/"


def _deactivate_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/desactiver/"


@pytest.fixture
def rh_activated(db):
    return Module.objects.create(
        id="rh", name="Ressources Humaines", version="1.0.0",
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
    user = User.objects.create(username="lecteur_employes", full_name="Lecteur")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    return user


@pytest.fixture
def writer(db):
    user = User.objects.create(username="rh_admin", full_name="Admin RH")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    _grant(user, "rh", "employee", "write")
    return user


# --- A. Accès ------------------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_reader_sees_the_list(self, rh_activated, reader):
        client = _authenticated_client(reader)
        assert client.get(LIST_URL).status_code == 200

    def test_unauthorized_user_sees_permission_denied(self, rh_activated, db):
        user = User.objects.create(username="sans_permission_emp", full_name="Sans Droit")
        auth.set_user_password(user, "Password123!")
        user.save()
        client = _authenticated_client(user)
        response = client.get(LIST_URL)
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self, rh_activated):
        client = Client()
        response = client.get(LIST_URL)
        assert response.status_code == 302

    def test_deactivated_rh_module_is_refused(self, db, reader):
        client = _authenticated_client(reader)
        response = client.get(LIST_URL)
        assert response.status_code == 403

    def test_reader_without_write_does_not_see_create_button(self, rh_activated, reader):
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "create-employee" not in content

    def test_post_is_rejected_on_list(self, rh_activated, reader):
        client = _authenticated_client(reader)
        response = client.post(LIST_URL)
        assert response.status_code == 405


# --- B. Création + mise à jour de la liste — cas explicitement requis ----------


@pytest.mark.django_db
class TestCreateAndListUpdate:
    def test_create_adds_a_real_employee(self, rh_activated, writer):
        client = _authenticated_client(writer)
        response = client.post(
            CREATE_URL,
            {
                "first_name": "Awa", "last_name": "Sow", "email": "awa.sow@example.com",
                "position": "Développeuse", "hire_date": "2026-03-01",
            },
        )
        assert response.status_code == 302
        assert Employee.objects.filter(first_name="Awa", last_name="Sow").exists()

    def test_created_employee_appears_in_the_list(self, rh_activated, writer):
        client = _authenticated_client(writer)
        client.post(
            CREATE_URL,
            {
                "first_name": "Awa", "last_name": "Sow", "email": "",
                "position": "Développeuse", "hire_date": "2026-03-01",
            },
        )
        content = client.get(LIST_URL).content.decode()
        assert "Awa Sow" in content
        assert "Développeuse" in content

    def test_creation_never_creates_a_user_account(self, rh_activated, writer):
        """Critère d'acceptation explicite (décision Lot 4 finale #1)."""
        count_before = User.objects.count()
        client = _authenticated_client(writer)
        client.post(
            CREATE_URL,
            {
                "first_name": "X", "last_name": "Y", "email": "",
                "position": "Z", "hire_date": "2026-01-01",
            },
        )
        assert User.objects.count() == count_before
        employee = Employee.objects.get(first_name="X", last_name="Y")
        assert employee.user is None

    def test_missing_required_field_reshows_the_form_with_error(self, rh_activated, writer):
        client = _authenticated_client(writer)
        response = client.post(
            CREATE_URL,
            {"first_name": "", "last_name": "Y", "email": "", "position": "Z",
             "hire_date": "2026-01-01"},
        )
        assert response.status_code == 400
        assert "Ce champ est requis" in response.content.decode()
        assert not Employee.objects.filter(last_name="Y").exists()

    def test_invalid_hire_date_reshows_the_form_with_error(self, rh_activated, writer):
        client = _authenticated_client(writer)
        response = client.post(
            CREATE_URL,
            {"first_name": "X", "last_name": "Y", "email": "", "position": "Z",
             "hire_date": "not-a-date"},
        )
        assert response.status_code == 400
        assert not Employee.objects.filter(last_name="Y").exists()

    def test_create_without_write_permission_is_refused(self, rh_activated, reader):
        client = _authenticated_client(reader)
        response = client.post(
            CREATE_URL,
            {"first_name": "X", "last_name": "Y", "email": "", "position": "Z",
             "hire_date": "2026-01-01"},
        )
        assert response.status_code == 403
        assert not Employee.objects.filter(last_name="Y").exists()


# --- C. Note d'absence de compte — critère d'acceptation explicite -------------


@pytest.mark.django_db
class TestNoAccountNote:
    def test_create_drawer_shows_the_no_account_note(self, rh_activated, writer):
        """Critère d'acceptation explicite : « note affichée précisant
        qu'aucun compte utilisateur n'est créé automatiquement »."""
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert "ne crée jamais de compte utilisateur" in content


# --- D. Édition -------------------------------------------------------------------


@pytest.mark.django_db
class TestEdit:
    def test_edit_updates_the_employee(self, rh_activated, writer):
        employee = create_employee(
            first_name="Jean", last_name="Dupont", position="Dev",
            hire_date=date(2026, 1, 1),
        )
        client = _authenticated_client(writer)
        response = client.post(
            _edit_url(employee.id),
            {"first_name": "Jeanne", "last_name": "Martin", "email": "",
             "position": "Chef de projet", "hire_date": "2026-02-01"},
        )
        assert response.status_code == 302
        employee.refresh_from_db()
        assert employee.first_name == "Jeanne"
        assert employee.position == "Chef de projet"

    def test_edit_never_changes_status(self, rh_activated, writer):
        employee = create_employee(
            first_name="Jean", last_name="Dupont", position="Dev",
            hire_date=date(2026, 1, 1),
        )
        deactivate_employee(employee=employee)

        client = _authenticated_client(writer)
        client.post(
            _edit_url(employee.id),
            {"first_name": "Jean", "last_name": "Dupont", "email": "",
             "position": "Nouveau poste", "hire_date": "2026-01-01"},
        )

        employee.refresh_from_db()
        assert employee.status == Employee.Status.INACTIVE

    def test_put_is_rejected(self, rh_activated, writer):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        client = _authenticated_client(writer)
        response = client.put(_edit_url(employee.id))
        assert response.status_code == 405


# --- E. Marquer inactif — critère d'acceptation explicite ----------------------


@pytest.mark.django_db
class TestDeactivate:
    def test_deactivate_sets_status_to_inactive(self, rh_activated, writer):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        client = _authenticated_client(writer)
        response = client.post(_deactivate_url(employee.id))
        assert response.status_code == 302
        employee.refresh_from_db()
        assert employee.status == Employee.Status.INACTIVE

    def test_deactivate_preserves_history(self, rh_activated, writer):
        """Critère d'acceptation explicite du ticket."""
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        contract = Contract.objects.create(
            employee=employee, type="CDI", start_date=date(2026, 1, 1)
        )
        leave = LeaveRequest.objects.create(
            employee=employee, type="RTT", start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )
        link = EmployeeDocument.objects.create(employee=employee, document_ref=1)

        client = _authenticated_client(writer)
        client.post(_deactivate_url(employee.id))

        assert Contract.objects.filter(pk=contract.pk).exists()
        assert LeaveRequest.objects.filter(pk=leave.pk).exists()
        assert EmployeeDocument.objects.filter(pk=link.pk).exists()

    def test_deactivate_without_write_permission_is_refused(self, rh_activated, reader):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        client = _authenticated_client(reader)
        response = client.post(_deactivate_url(employee.id))
        assert response.status_code == 403
        employee.refresh_from_db()
        assert employee.status == Employee.Status.ACTIVE

    def test_get_is_rejected(self, rh_activated, writer):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        client = _authenticated_client(writer)
        response = client.get(_deactivate_url(employee.id))
        assert response.status_code == 405


# --- F. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_employee_name_is_html_escaped(self, rh_activated, writer):
        create_employee(
            first_name="<script>alert(1)</script>", last_name="Y", position="Z",
            hire_date=date(2026, 1, 1),
        )
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert "<script>alert(1)</script>" not in content
