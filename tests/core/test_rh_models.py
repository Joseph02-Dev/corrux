"""Tests unitaires des modèles `rh` — TECH-030.

Structure de données uniquement. Patron déjà établi par
tests/core/test_documentation_models.py (TECH-020), réutilisé tel quel.
"""

from datetime import date

import pytest
from django.db import IntegrityError, connection, transaction

from core.identity.models import User
from modules.rh.models import Contract, Employee, EmployeeDocument, LeaveRequest


@pytest.fixture
def employee(db):
    return Employee.objects.create(
        first_name="Jean", last_name="Dupont", position="Développeur",
        hire_date=date(2026, 1, 1),
    )


@pytest.fixture
def approver(db):
    return User.objects.create(username="approbateur", password_hash="x", full_name="Approbateur")


# --- A. Création valide ---------------------------------------------------------


@pytest.mark.django_db
class TestValidCreation:
    def test_create_valid_employee_without_user_account(self):
        employee = Employee.objects.create(
            first_name="Awa", last_name="Sow", position="RH",
            hire_date=date(2026, 3, 1),
        )
        assert employee.pk is not None
        assert employee.user is None

    def test_email_defaults_to_empty_string(self):
        employee = Employee.objects.create(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        assert employee.email == ""

    def test_email_can_be_set_explicitly(self):
        employee = Employee.objects.create(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1),
            email="employe@example.com",
        )
        assert employee.email == "employe@example.com"

    def test_create_valid_employee_with_user_account(self, db):
        user = User.objects.create(username="lie_employe", password_hash="x", full_name="Lié")
        employee = Employee.objects.create(
            first_name="X", last_name="Y", position="Z",
            hire_date=date(2026, 1, 1), user=user,
        )
        assert employee.user == user

    def test_employee_defaults_to_active_status(self, employee):
        assert employee.status == Employee.Status.ACTIVE

    def test_create_valid_contract(self, employee):
        contract = Contract.objects.create(
            employee=employee, type="CDI", start_date=date(2026, 1, 1)
        )
        assert contract.pk is not None
        assert contract.end_date is None
        assert contract.document_ref is None

    def test_create_valid_contract_with_document_ref(self, employee):
        contract = Contract.objects.create(
            employee=employee, type="CDD", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), document_ref=42,
        )
        assert contract.document_ref == 42

    def test_create_valid_leave_request(self, employee):
        leave = LeaveRequest.objects.create(
            employee=employee, type="Congés payés",
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 10),
        )
        assert leave.pk is not None
        assert leave.status == LeaveRequest.Status.PENDING
        assert leave.approver_user is None

    def test_create_valid_employee_document(self, employee):
        link = EmployeeDocument.objects.create(employee=employee, document_ref=7)
        assert link.pk is not None
        assert link.document_ref == 7


# --- B. Relations ----------------------------------------------------------------


@pytest.mark.django_db
class TestRelations:
    def test_employee_to_user_reverse_accessor(self, db):
        user = User.objects.create(username="reverse_test", password_hash="x", full_name="R")
        employee = Employee.objects.create(
            first_name="A", last_name="B", position="C",
            hire_date=date(2026, 1, 1), user=user,
        )
        assert user.employee_profile.get() == employee

    def test_contract_to_employee(self, employee):
        contract = Contract.objects.create(
            employee=employee, type="CDI", start_date=date(2026, 1, 1)
        )
        assert contract.employee == employee
        assert employee.contracts.get() == contract

    def test_leave_request_to_employee(self, employee):
        leave = LeaveRequest.objects.create(
            employee=employee, type="RTT",
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 2),
        )
        assert leave.employee == employee
        assert employee.leave_requests.get() == leave

    def test_leave_request_to_approver(self, employee, approver):
        leave = LeaveRequest.objects.create(
            employee=employee, type="RTT",
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 2),
            status=LeaveRequest.Status.APPROVED, approver_user=approver,
        )
        assert leave.approver_user == approver
        assert approver.approved_leave_requests.get() == leave

    def test_employee_document_to_employee(self, employee):
        link = EmployeeDocument.objects.create(employee=employee, document_ref=1)
        assert link.employee == employee
        assert employee.employee_documents.get() == link


# --- C. Contraintes ---------------------------------------------------------------


@pytest.mark.django_db
class TestConstraints:
    def test_employee_requires_first_name(self, db):
        with pytest.raises(IntegrityError), transaction.atomic():
            Employee.objects.create(
                last_name="X", position="Y", hire_date=date(2026, 1, 1), first_name=None
            )

    def test_contract_requires_an_employee(self, db):
        with pytest.raises(IntegrityError), transaction.atomic():
            Contract.objects.create(employee=None, type="CDI", start_date=date(2026, 1, 1))

    def test_leave_request_status_accepts_only_the_three_defined_values(self, employee):
        for status in (
            LeaveRequest.Status.PENDING,
            LeaveRequest.Status.APPROVED,
            LeaveRequest.Status.REJECTED,
        ):
            leave = LeaveRequest.objects.create(
                employee=employee, type="X",
                start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
                status=status,
            )
            assert leave.status == status

    def test_employee_document_requires_a_document_ref(self, employee):
        with pytest.raises(IntegrityError), transaction.atomic():
            EmployeeDocument.objects.create(employee=employee, document_ref=None)

    def test_employee_document_uniqueness_per_employee_and_ref(self, employee):
        EmployeeDocument.objects.create(employee=employee, document_ref=5)
        with pytest.raises(IntegrityError), transaction.atomic():
            EmployeeDocument.objects.create(employee=employee, document_ref=5)

    def test_same_document_ref_allowed_for_different_employees(self, employee):
        other_employee = Employee.objects.create(
            first_name="Autre", last_name="Employé", position="X",
            hire_date=date(2026, 1, 1),
        )
        EmployeeDocument.objects.create(employee=employee, document_ref=9)
        # Ne doit pas lever : la contrainte d'unicité porte sur la paire
        # (employee, document_ref), pas sur document_ref seul.
        EmployeeDocument.objects.create(employee=other_employee, document_ref=9)
        assert EmployeeDocument.objects.filter(document_ref=9).count() == 2


# --- D. on_delete — comportements documentés ------------------------------------


@pytest.mark.django_db
class TestOnDelete:
    def test_deleting_linked_user_sets_employee_user_to_null(self, db):
        """Résolution documentée : SET_NULL, pas PROTECT — la fiche
        employé survit intacte, seulement délliée."""
        user = User.objects.create(username="a_supprimer", password_hash="x", full_name="X")
        employee = Employee.objects.create(
            first_name="A", last_name="B", position="C",
            hire_date=date(2026, 1, 1), user=user,
        )

        user.delete()

        employee.refresh_from_db()
        assert employee.user is None
        assert Employee.objects.filter(pk=employee.pk).exists()

    def test_deleting_employee_with_contracts_is_protected(self, employee):
        Contract.objects.create(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        with pytest.raises(IntegrityError), transaction.atomic():
            employee.delete()

    def test_deleting_employee_with_leave_requests_is_protected(self, employee):
        LeaveRequest.objects.create(
            employee=employee, type="X",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            employee.delete()

    def test_deleting_employee_with_employee_documents_is_protected(self, employee):
        EmployeeDocument.objects.create(employee=employee, document_ref=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            employee.delete()

    def test_deleting_approver_user_is_protected(self, employee, approver):
        LeaveRequest.objects.create(
            employee=employee, type="X",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
            status=LeaveRequest.Status.APPROVED, approver_user=approver,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            approver.delete()


# --- E. Schéma / contrainte architecturale ---------------------------------------


@pytest.mark.django_db
class TestSchema:
    def test_tables_are_qualified_in_the_rh_schema(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'rh' ORDER BY table_name"
            )
            tables = {row[0] for row in cursor.fetchall()}
        assert tables == {"employees", "contracts", "leave_requests", "employee_documents"}

    def test_document_ref_fields_are_plain_integers_not_foreign_keys(self):
        """Contrainte architecturale impérative : document_ref n'est
        jamais une ForeignKey vers modules.documentation.models.Document
        — RH ne doit jamais importer les modèles internes de
        Documentation (architecture §15)."""
        contract_field = Contract._meta.get_field("document_ref")
        link_field = EmployeeDocument._meta.get_field("document_ref")

        assert contract_field.get_internal_type() == "IntegerField"
        assert link_field.get_internal_type() == "IntegerField"
        assert not contract_field.is_relation
        assert not link_field.is_relation

    def test_no_import_of_documentation_models_in_rh_models(self):
        import ast
        import inspect

        from modules.rh import models as rh_models

        source = inspect.getsource(rh_models)
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)

        assert not any("documentation" in name for name in imported_modules)

    def test_no_field_beyond_those_specified_by_architecture_or_confirmed_decisions(
        self, employee
    ):
        """Aucun timestamp/statut de contrat/champ non spécifié n'a été
        ajouté — vérifié sur les champs réellement présents de chaque
        modèle. `email` est désormais attendu (décision produit
        confirmée, TECH-031) : mise à jour nécessaire de ce test, pas
        une régression — même situation que documentation/test_smoke.py
        en TECH-020/030 lorsqu'un champ est ajouté par conception."""
        employee_fields = {f.name for f in Employee._meta.get_fields()}
        assert employee_fields == {
            "id", "user", "first_name", "last_name", "email", "position",
            "hire_date", "status", "contracts", "leave_requests", "employee_documents",
        }

        contract_fields = {f.name for f in Contract._meta.get_fields()}
        assert contract_fields == {
            "id", "employee", "type", "start_date", "end_date", "document_ref",
        }
