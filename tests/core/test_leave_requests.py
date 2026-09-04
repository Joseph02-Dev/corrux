"""Tests du cycle de vie des demandes de congé — TECH-033.

Backend pur, aucune vue HTTP. Les 3 scénarios explicitement requis par
le contrat du ticket sont couverts intégralement : cycle validation,
cycle refus avec commentaire, refus sans commentaire rejeté.
"""

from datetime import date

import pytest

from core.identity.models import User
from modules.rh.models import LeaveRequest
from modules.rh.services import (
    LeaveRequestDecisionError,
    approve_leave_request,
    create_employee,
    create_leave_request,
    list_leave_requests_for_employee,
    list_pending_leave_requests,
    reject_leave_request,
)


@pytest.fixture
def employee(db):
    return create_employee(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


@pytest.fixture
def approver(db):
    return User.objects.create(username="valideur_conges", full_name="Valideur")


# --- A. Création -------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateLeaveRequest:
    def test_creates_a_real_leave_request(self, employee):
        leave = create_leave_request(
            employee=employee, type="Congés payés",
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 10),
        )
        assert leave.pk is not None
        assert LeaveRequest.objects.filter(pk=leave.pk).exists()

    def test_defaults_to_pending_status(self, employee):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        assert leave.status == LeaveRequest.Status.PENDING

    def test_comment_defaults_to_empty(self, employee):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        assert leave.comment == ""

    def test_comment_can_be_provided(self, employee):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
            comment="Rendez-vous médical",
        )
        assert leave.comment == "Rendez-vous médical"

    def test_no_approver_at_creation(self, employee):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        assert leave.approver_user is None
        assert leave.approver_comment == ""


# --- B. Cycle validation — cas explicitement requis par le contrat --------------


@pytest.mark.django_db
class TestApprovalCycle:
    def test_approve_sets_status_to_approved(self, employee, approver):
        leave = create_leave_request(
            employee=employee, type="Congés payés",
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 10),
        )

        approved = approve_leave_request(leave_request=leave, approver=approver)

        approved.refresh_from_db()
        assert approved.status == LeaveRequest.Status.APPROVED

    def test_approve_records_the_approver(self, employee, approver):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        approve_leave_request(leave_request=leave, approver=approver)

        leave.refresh_from_db()
        assert leave.approver_user == approver

    def test_approve_requires_no_comment(self, employee, approver):
        """Maquette : « Variante A — Validation (confirmation simple) »
        — aucun commentaire exigé, contrairement au refus."""
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        approved = approve_leave_request(leave_request=leave, approver=approver)

        assert approved.status == LeaveRequest.Status.APPROVED
        assert approved.approver_comment == ""

    def test_approved_status_is_visible_in_employee_view(self, employee, approver):
        """Critère d'acceptation explicite : « statut visible par
        l'employé »."""
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        approve_leave_request(leave_request=leave, approver=approver)

        results = list_leave_requests_for_employee(employee)

        assert results[0].status == LeaveRequest.Status.APPROVED


# --- C. Cycle refus avec commentaire — cas explicitement requis -----------------


@pytest.mark.django_db
class TestRejectionCycleWithComment:
    def test_reject_with_comment_sets_status_to_rejected(self, employee, approver):
        leave = create_leave_request(
            employee=employee, type="Congés payés",
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 10),
        )

        rejected = reject_leave_request(
            leave_request=leave, approver=approver,
            comment="Effectif insuffisant sur cette période.",
        )

        rejected.refresh_from_db()
        assert rejected.status == LeaveRequest.Status.REJECTED

    def test_reject_records_the_approver_comment(self, employee, approver):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        reject_leave_request(
            leave_request=leave, approver=approver, comment="Refusé pour raison X."
        )

        leave.refresh_from_db()
        assert leave.approver_comment == "Refusé pour raison X."
        assert leave.approver_user == approver

    def test_rejection_comment_is_visible_in_employee_view(self, employee, approver):
        """Critère d'acceptation explicite : « visible par l'employé »."""
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        reject_leave_request(leave_request=leave, approver=approver, comment="Motif du refus")

        results = list_leave_requests_for_employee(employee)

        assert results[0].approver_comment == "Motif du refus"
        assert results[0].status == LeaveRequest.Status.REJECTED


# --- D. Refus sans commentaire rejeté — cas explicitement requis ---------------


@pytest.mark.django_db
class TestRejectionWithoutCommentIsRejected:
    def test_empty_comment_raises(self, employee, approver):
        """Critère d'acceptation explicite du ticket."""
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        with pytest.raises(LeaveRequestDecisionError):
            reject_leave_request(leave_request=leave, approver=approver, comment="")

    def test_whitespace_only_comment_raises(self, employee, approver):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        with pytest.raises(LeaveRequestDecisionError):
            reject_leave_request(leave_request=leave, approver=approver, comment="   ")

    def test_failed_rejection_writes_nothing(self, employee, approver):
        """Aucune écriture n'a lieu si la validation échoue — même
        discipline que DocumentUploadError (TECH-021)."""
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        with pytest.raises(LeaveRequestDecisionError):
            reject_leave_request(leave_request=leave, approver=approver, comment="")

        leave.refresh_from_db()
        assert leave.status == LeaveRequest.Status.PENDING
        assert leave.approver_user is None


# --- E. Visibilité — critères d'acceptation explicites --------------------------


@pytest.mark.django_db
class TestVisibility:
    def test_pending_request_visible_to_approver(self, employee):
        """Critère d'acceptation explicite : « demande visible par le
        valideur »."""
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        pending = list_pending_leave_requests()

        assert leave in pending

    def test_approved_request_no_longer_pending(self, employee, approver):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        approve_leave_request(leave_request=leave, approver=approver)

        assert leave not in list_pending_leave_requests()

    def test_rejected_request_no_longer_pending(self, employee, approver):
        leave = create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        reject_leave_request(leave_request=leave, approver=approver, comment="Motif")

        assert leave not in list_pending_leave_requests()

    def test_employee_view_only_shows_their_own_requests(self, employee, db):
        other_employee = create_employee(
            first_name="Autre", last_name="Employé", position="X",
            hire_date=date(2026, 1, 1),
        )
        create_leave_request(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        create_leave_request(
            employee=other_employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        results = list_leave_requests_for_employee(employee)

        assert all(r.employee_id == employee.id for r in results)
        assert len(results) == 1


# --- F. Aucun calcul de solde (décision Lot 4 finale #3) ------------------------


class TestNoBalanceCalculation:
    def test_no_balance_field_or_function_exists(self):
        """Décision Lot 4 finale #3 : solde de jours hors périmètre —
        aucun champ, aucune fonction de calcul nulle part."""
        import modules.rh.services as rh_services

        assert not hasattr(LeaveRequest, "balance")
        function_names = [name.lower() for name in dir(rh_services)]
        assert not any("balance" in name or "solde" in name for name in function_names)
