"""Tests du service CRUD Fiche employé — TECH-031.

Backend pur, aucune vue HTTP. Patron déjà établi par
tests/core/test_documentation_upload.py (TECH-021).
"""

from datetime import date

import pytest

from core.identity.models import User
from modules.rh.models import Contract, Employee, EmployeeDocument, LeaveRequest
from modules.rh.services import (
    create_employee,
    deactivate_employee,
    employee_full_name,
    update_employee,
)


@pytest.fixture
def employee(db):
    return create_employee(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


# --- A. Création — critère d'acceptation explicite -------------------------------


@pytest.mark.django_db
class TestCreateEmployee:
    def test_creates_a_real_employee(self):
        employee = create_employee(
            first_name="Awa", last_name="Sow", position="RH",
            hire_date=date(2026, 3, 1),
        )
        assert employee.pk is not None
        assert Employee.objects.filter(pk=employee.pk).exists()

    def test_never_creates_a_user_account(self):
        """Critère d'acceptation explicite du ticket : la création ne
        crée jamais de compte utilisateur."""
        count_before = User.objects.count()

        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )

        assert employee.user is None
        assert User.objects.count() == count_before

    def test_email_defaults_to_empty_when_omitted(self):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        assert employee.email == ""

    def test_email_can_be_provided(self):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1),
            email="jean.dupont@example.com",
        )
        assert employee.email == "jean.dupont@example.com"

    def test_defaults_to_active_status(self, employee):
        assert employee.status == Employee.Status.ACTIVE


# --- B. Édition -----------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateEmployee:
    def test_updates_all_fields(self, employee):
        updated = update_employee(
            employee=employee, first_name="Jeanne", last_name="Martin",
            position="Chef de projet", hire_date=date(2026, 2, 1),
            status=Employee.Status.ACTIVE, email="jeanne.martin@example.com",
        )

        updated.refresh_from_db()
        assert updated.first_name == "Jeanne"
        assert updated.last_name == "Martin"
        assert updated.position == "Chef de projet"
        assert updated.hire_date == date(2026, 2, 1)
        assert updated.email == "jeanne.martin@example.com"

    def test_status_is_a_required_parameter(self, employee):
        """Décision documentée : pas de valeur par défaut pour status,
        pour éviter qu'un appel l'omettant ne réinitialise
        silencieusement le statut d'un employé désactivé."""
        with pytest.raises(TypeError):
            update_employee(
                employee=employee, first_name="X", last_name="Y",
                position="Z", hire_date=date(2026, 1, 1),
            )

    def test_never_touches_user_field(self, employee, db):
        user = User.objects.create(username="lie_update", full_name="Lié")
        employee.user = user
        employee.save(update_fields=["user"])

        update_employee(
            employee=employee, first_name="X", last_name="Y", position="Z",
            hire_date=date(2026, 1, 1), status=Employee.Status.ACTIVE,
        )

        employee.refresh_from_db()
        assert employee.user == user

    def test_persists_to_database(self, employee):
        update_employee(
            employee=employee, first_name="Persisté", last_name="Y", position="Z",
            hire_date=date(2026, 1, 1), status=Employee.Status.ACTIVE,
        )

        reloaded = Employee.objects.get(pk=employee.pk)
        assert reloaded.first_name == "Persisté"


# --- C. Désactivation — critère d'acceptation explicite --------------------------


@pytest.mark.django_db
class TestDeactivateEmployee:
    def test_sets_status_to_inactive(self, employee):
        deactivate_employee(employee=employee)
        employee.refresh_from_db()
        assert employee.status == Employee.Status.INACTIVE

    def test_preserves_contracts_history(self, employee):
        """Critère d'acceptation explicite : « marquer inactif conserve
        l'historique (contrats, congés, documents) »."""
        contract = Contract.objects.create(
            employee=employee, type="CDI", start_date=date(2026, 1, 1)
        )

        deactivate_employee(employee=employee)

        assert Contract.objects.filter(pk=contract.pk).exists()
        contract.refresh_from_db()
        assert contract.employee_id == employee.id

    def test_preserves_leave_requests_history(self, employee):
        leave = LeaveRequest.objects.create(
            employee=employee, type="RTT",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )

        deactivate_employee(employee=employee)

        assert LeaveRequest.objects.filter(pk=leave.pk).exists()

    def test_preserves_employee_documents_history(self, employee):
        link = EmployeeDocument.objects.create(employee=employee, document_ref=42)

        deactivate_employee(employee=employee)

        assert EmployeeDocument.objects.filter(pk=link.pk).exists()

    def test_only_status_field_is_modified(self, employee):
        original_first_name = employee.first_name
        original_email = employee.email
        original_position = employee.position

        deactivate_employee(employee=employee)
        employee.refresh_from_db()

        assert employee.first_name == original_first_name
        assert employee.email == original_email
        assert employee.position == original_position

    def test_reactivation_via_update_employee_is_possible(self, employee):
        """Rien n'empêche de repasser un employé à Actif via
        update_employee (status explicite) — aucune irréversibilité
        n'est imposée par les sources."""
        deactivate_employee(employee=employee)

        update_employee(
            employee=employee, first_name=employee.first_name,
            last_name=employee.last_name, position=employee.position,
            hire_date=employee.hire_date, status=Employee.Status.ACTIVE,
        )

        employee.refresh_from_db()
        assert employee.status == Employee.Status.ACTIVE


# --- D. Affichage du nom complet --------------------------------------------------


@pytest.mark.django_db
class TestEmployeeFullName:
    def test_combines_first_and_last_name(self, employee):
        assert employee_full_name(employee) == "Jean Dupont"

    def test_reflects_updated_names(self, employee):
        update_employee(
            employee=employee, first_name="Awa", last_name="Sow",
            position=employee.position, hire_date=employee.hire_date,
            status=Employee.Status.ACTIVE,
        )
        assert employee_full_name(employee) == "Awa Sow"
