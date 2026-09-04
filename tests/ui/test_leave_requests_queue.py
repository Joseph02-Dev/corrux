"""Tests de Congés à traiter + Modal de décision — UI-406.

Les 2 scénarios explicitement requis par le contrat du ticket sont
couverts intégralement : validation, refus avec commentaire obligatoire.
Critère d'acceptation explicite : une décision met à jour le statut
visible dans UI-405 pour l'employé concerné — vérifié bout en bout.
"""

from datetime import date

import pytest
from django.test import Client

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.rh.models import LeaveRequest
from modules.rh.services import create_employee, create_leave_request

QUEUE_URL = "/conges-a-traiter/"


def _approve_url(leave_id: int) -> str:
    return f"/conges-a-traiter/{leave_id}/valider/"


def _reject_url(leave_id: int) -> str:
    return f"/conges-a-traiter/{leave_id}/refuser/"


def _employee_leave_tab_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/conges/"


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
def validator(db):
    user = User.objects.create(username="valideur_ui406", full_name="Valideur")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "leave_request", "approve")
    return user


@pytest.fixture
def read_only_user(db):
    user = User.objects.create(username="lecture_seule_ui406", full_name="Lecture Seule")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "leave_request", "read")
    return user


@pytest.fixture
def employee_viewer(db):
    """Utilisateur pouvant relire l'onglet Congés d'un employé (UI-405) —
    pour vérifier la mise à jour visible dans les deux écrans."""
    user = User.objects.create(username="lecteur_employe_ui406", full_name="Lecteur Employé")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    _grant(user, "rh", "leave_request", "read")
    return user


@pytest.fixture
def employee(rh_activated):
    return create_employee(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


# --- A. Accès ------------------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_validator_sees_the_queue(self, validator, rh_activated):
        client = _authenticated_client(validator)
        assert client.get(QUEUE_URL).status_code == 200

    def test_read_only_permission_is_not_enough(self, read_only_user):
        """Décision documentée : rh.leave_request.approve, pas
        .read — même écart déjà résolu (Option A)."""
        client = _authenticated_client(read_only_user)
        response = client.get(QUEUE_URL)
        assert response.status_code == 403

    def test_anonymous_redirected(self):
        response = Client().get(QUEUE_URL)
        assert response.status_code == 302

    def test_deactivated_module_is_refused(self, db, validator):
        client = _authenticated_client(validator)
        response = client.get(QUEUE_URL)
        assert response.status_code == 403

    def test_post_is_rejected_on_queue(self, validator):
        client = _authenticated_client(validator)
        assert client.post(QUEUE_URL).status_code == 405

    def test_sidebar_link_only_shown_with_approve(self, validator, read_only_user, rh_activated):
        from ui.navigation import get_navigation

        approve_groups = get_navigation(validator)
        read_groups = get_navigation(read_only_user)

        rh_group_approve = next(
            (g for g in approve_groups if g.label == "Ressources Humaines"), None
        )
        rh_group_read = next((g for g in read_groups if g.label == "Ressources Humaines"), None)

        assert rh_group_approve is not None
        assert any(item.label == "Congés" for item in rh_group_approve.items)
        # read seul ne doit jamais afficher Congés (lien mort évité).
        if rh_group_read is not None:
            assert not any(item.label == "Congés" for item in rh_group_read.items)


# --- B. Liste --------------------------------------------------------------------


@pytest.mark.django_db
class TestQueueListing:
    def test_empty_queue(self, validator):
        client = _authenticated_client(validator)
        content = client.get(QUEUE_URL).content.decode()
        assert "corrux-empty-state" in content

    def test_pending_requests_are_listed_across_employees(self, employee, validator, db):
        other_employee = create_employee(
            first_name="Awa", last_name="Sow", position="RH", hire_date=date(2026, 1, 1)
        )
        create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        create_leave_request(
            employee=other_employee, type="CP", start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 5),
        )

        client = _authenticated_client(validator)
        content = client.get(QUEUE_URL).content.decode()

        assert "Jean Dupont" in content
        assert "Awa Sow" in content

    def test_approved_requests_are_not_in_the_queue(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        client.post(_approve_url(leave.id))

        content = client.get(QUEUE_URL).content.decode()
        assert "corrux-empty-state" in content


# --- C. Validation — cas explicitement requis, bout en bout ---------------------


@pytest.mark.django_db
class TestApproval:
    def test_approve_sets_status_to_approved(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        response = client.post(_approve_url(leave.id))

        assert response.status_code == 302
        leave.refresh_from_db()
        assert leave.status == LeaveRequest.Status.APPROVED

    def test_approve_records_the_validator(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        client.post(_approve_url(leave.id))

        leave.refresh_from_db()
        assert leave.approver_user == validator

    def test_approval_visible_in_ui405_for_the_employee(
        self, employee, validator, employee_viewer
    ):
        """Critère d'acceptation explicite du ticket : « une décision
        met à jour le statut visible dans UI-405 pour l'employé
        concerné » — bout en bout, écran réel."""
        leave = create_leave_request(
            employee=employee, type="Congés payés", start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 10),
        )

        validator_client = _authenticated_client(validator)
        validator_client.post(_approve_url(leave.id))

        viewer_client = _authenticated_client(employee_viewer)
        content = viewer_client.get(_employee_leave_tab_url(employee.id)).content.decode()

        assert "Congés payés" in content
        assert "Approuvé" in content

    def test_approve_without_permission_is_refused(self, employee, read_only_user):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(read_only_user)
        response = client.post(_approve_url(leave.id))
        assert response.status_code == 403
        leave.refresh_from_db()
        assert leave.status == LeaveRequest.Status.PENDING

    def test_get_is_rejected_on_approve(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        response = client.get(_approve_url(leave.id))
        assert response.status_code == 405


# --- D. Refus avec commentaire — cas explicitement requis, bout en bout --------


@pytest.mark.django_db
class TestRejectionWithComment:
    def test_reject_with_comment_sets_status_to_rejected(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        response = client.post(_reject_url(leave.id), {"comment": "Effectif insuffisant"})

        assert response.status_code == 302
        leave.refresh_from_db()
        assert leave.status == LeaveRequest.Status.REJECTED
        assert leave.approver_comment == "Effectif insuffisant"

    def test_rejection_visible_in_ui405_for_the_employee(
        self, employee, validator, employee_viewer
    ):
        """Critère d'acceptation explicite du ticket — bout en bout,
        écran réel, cas du refus."""
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )

        validator_client = _authenticated_client(validator)
        validator_client.post(_reject_url(leave.id), {"comment": "Motif du refus"})

        viewer_client = _authenticated_client(employee_viewer)
        content = viewer_client.get(_employee_leave_tab_url(employee.id)).content.decode()

        assert "Refusé" in content
        assert "Motif du refus" in content


# --- E. Refus sans commentaire rejeté — critère d'acceptation explicite --------


@pytest.mark.django_db
class TestRejectionWithoutCommentIsRejected:
    def test_empty_comment_does_not_reject(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        response = client.post(_reject_url(leave.id), {"comment": ""})

        assert response.status_code == 400
        leave.refresh_from_db()
        assert leave.status == LeaveRequest.Status.PENDING

    def test_empty_comment_shows_explicit_error(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        response = client.post(_reject_url(leave.id), {"comment": "   "})

        assert "corrux-error-state" in response.content.decode()

    def test_reject_without_permission_is_refused(self, employee, read_only_user):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(read_only_user)
        response = client.post(_reject_url(leave.id), {"comment": "Motif"})
        assert response.status_code == 403
        leave.refresh_from_db()
        assert leave.status == LeaveRequest.Status.PENDING


# --- F. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_comment_is_html_escaped(self, employee, validator):
        leave = create_leave_request(
            employee=employee, type="RTT", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
        )
        client = _authenticated_client(validator)
        client.post(_reject_url(leave.id), {"comment": "<script>alert(1)</script>"})

        content = client.get(QUEUE_URL).content.decode()
        assert "<script>alert(1)</script>" not in content

    def test_employee_name_is_html_escaped(self, validator, rh_activated):
        employee_with_html = create_employee(
            first_name="<script>alert(2)</script>", last_name="Z", position="Z",
            hire_date=date(2026, 1, 1),
        )
        create_leave_request(
            employee=employee_with_html, type="RTT", start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )

        client = _authenticated_client(validator)
        content = client.get(QUEUE_URL).content.decode()
        assert "<script>alert(2)</script>" not in content
