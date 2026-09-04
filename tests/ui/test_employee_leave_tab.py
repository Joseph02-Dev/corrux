"""Tests de l'onglet Congés + Drawer demande de congé — UI-405.

Rendu HTTP réel, permissions réelles. UI-406 (file d'attente du
valideur) n'existe pas encore : le critère d'acceptation « visible ici
et dans UI-406 » est vérifié via list_pending_leave_requests()
(TECH-033, déjà construite et testée) — le mécanisme réel que UI-406
utilisera, sans anticiper la construction de cet écran.
"""

from datetime import date

import pytest
from django.test import Client

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.rh.models import LeaveRequest
from modules.rh.services import (
    create_employee,
    create_leave_request,
    list_pending_leave_requests,
    reject_leave_request,
)


def _leave_tab_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/conges/"


def _create_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/conges/nouveau/"


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
    user = User.objects.create(username="lecteur_conges", full_name="Lecteur")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    _grant(user, "rh", "leave_request", "read")
    return user


@pytest.fixture
def writer(db):
    user = User.objects.create(username="employe_demandeur", full_name="Employé Demandeur")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    _grant(user, "rh", "leave_request", "read")
    _grant(user, "rh", "leave_request", "write")
    return user


@pytest.fixture
def employee(rh_activated):
    return create_employee(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


# --- A. Accès à deux niveaux ------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_reader_sees_the_tab(self, employee, reader):
        client = _authenticated_client(reader)
        assert client.get(_leave_tab_url(employee.id)).status_code == 200

    def test_employee_read_alone_is_not_enough(self, employee, db):
        user = User.objects.create(username="employee_read_seul_conges", full_name="X")
        auth.set_user_password(user, "Password123!")
        user.save()
        _grant(user, "rh", "employee", "read")

        client = _authenticated_client(user)
        response = client.get(_leave_tab_url(employee.id))
        assert response.status_code == 403

    def test_tab_permission_denied_shows_header_and_tabs(self, employee, db):
        user = User.objects.create(username="employee_read_seul_conges2", full_name="X2")
        auth.set_user_password(user, "Password123!")
        user.save()
        _grant(user, "rh", "employee", "read")

        client = _authenticated_client(user)
        content = client.get(_leave_tab_url(employee.id)).content.decode()
        assert "Jean Dupont" in content
        assert "corrux-permission-denied" in content

    def test_anonymous_redirected(self, employee):
        response = Client().get(_leave_tab_url(employee.id))
        assert response.status_code == 302

    def test_post_is_rejected_on_tab(self, employee, reader):
        client = _authenticated_client(reader)
        assert client.post(_leave_tab_url(employee.id)).status_code == 405

    def test_create_button_hidden_for_reader(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_leave_tab_url(employee.id)).content.decode()
        assert "create-leave" not in content


# --- B. Création + visibilité — critère d'acceptation explicite -----------------


@pytest.mark.django_db
class TestCreateAndVisibility:
    def test_create_adds_a_real_leave_request(self, employee, writer):
        client = _authenticated_client(writer)
        response = client.post(
            _create_url(employee.id),
            {"type": "RTT", "start_date": "2026-06-01", "end_date": "2026-06-02", "comment": ""},
        )
        assert response.status_code == 302
        assert LeaveRequest.objects.filter(employee=employee, type="RTT").exists()

    def test_created_request_is_pending_in_the_tab(self, employee, writer):
        """Critère d'acceptation explicite : « une demande créée
        apparaît en En attente ici »."""
        client = _authenticated_client(writer)
        client.post(
            _create_url(employee.id),
            {
                "type": "Congés payés", "start_date": "2026-06-01", "end_date": "2026-06-10",
                "comment": "",
            },
        )
        content = client.get(_leave_tab_url(employee.id)).content.decode()
        assert "Congés payés" in content
        assert "En attente" in content

    def test_created_request_is_pending_in_the_validator_queue_mechanism(
        self, employee, writer
    ):
        """Critère d'acceptation explicite : « ... et dans UI-406 » —
        UI-406 n'existe pas encore ; vérifié via
        list_pending_leave_requests() (TECH-033), le mécanisme réel que
        cet écran futur utilisera pour son propre rendu."""
        client = _authenticated_client(writer)
        client.post(
            _create_url(employee.id),
            {"type": "RTT", "start_date": "2026-01-01", "end_date": "2026-01-02", "comment": ""},
        )

        pending = list_pending_leave_requests()
        assert any(
            leave.employee_id == employee.id and leave.type == "RTT" for leave in pending
        )

    def test_comment_is_optional_and_saved(self, employee, writer):
        client = _authenticated_client(writer)
        client.post(
            _create_url(employee.id),
            {
                "type": "RTT", "start_date": "2026-01-01", "end_date": "2026-01-02",
                "comment": "Rendez-vous médical",
            },
        )
        leave = LeaveRequest.objects.get(employee=employee)
        assert leave.comment == "Rendez-vous médical"

    def test_missing_type_reshows_form_with_error(self, employee, writer):
        client = _authenticated_client(writer)
        response = client.post(
            _create_url(employee.id),
            {"type": "", "start_date": "2026-01-01", "end_date": "2026-01-02", "comment": ""},
        )
        assert response.status_code == 400
        assert not LeaveRequest.objects.filter(employee=employee).exists()

    def test_create_without_write_is_refused(self, employee, reader):
        client = _authenticated_client(reader)
        response = client.post(
            _create_url(employee.id),
            {"type": "RTT", "start_date": "2026-01-01", "end_date": "2026-01-02", "comment": ""},
        )
        assert response.status_code == 403


# --- C. Aucun champ de solde (décision Lot 4 finale #3) -------------------------


@pytest.mark.django_db
class TestNoBalanceField:
    def test_create_drawer_has_no_balance_field(self, employee, writer):
        client = _authenticated_client(writer)
        content = client.get(_leave_tab_url(employee.id)).content.decode()
        drawer_block = content.split('id="create-leave"')[1].split("</dialog>")[0]
        assert "solde" not in drawer_block.lower()
        assert "balance" not in drawer_block.lower()


# --- D. Commentaire du valideur visible après refus -------------------------------


@pytest.mark.django_db
class TestApproverCommentVisibility:
    def test_rejection_comment_is_visible_to_the_employee(self, employee, writer, db):
        approver = User.objects.create(username="valideur_ui405", full_name="Valideur")
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )
        reject_leave_request(
            leave_request=leave, approver=approver, comment="Effectif insuffisant"
        )

        client = _authenticated_client(writer)
        content = client.get(_leave_tab_url(employee.id)).content.decode()

        assert "Effectif insuffisant" in content
        assert "Refusé" in content


# --- E. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_type_is_html_escaped(self, employee, writer):
        client = _authenticated_client(writer)
        client.post(
            _create_url(employee.id),
            {
                "type": "<script>alert(1)</script>", "start_date": "2026-01-01",
                "end_date": "2026-01-02", "comment": "",
            },
        )
        content = client.get(_leave_tab_url(employee.id)).content.decode()
        assert "<script>alert(1)</script>" not in content

    def test_put_is_rejected_on_create(self, employee, writer):
        client = _authenticated_client(writer)
        response = client.put(_create_url(employee.id))
        assert response.status_code == 405
