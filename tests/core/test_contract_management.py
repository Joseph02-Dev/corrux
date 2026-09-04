"""Tests de la gestion des contrats — TECH-032.

Backend pur, aucune vue HTTP. Critère d'acceptation explicite : aucun
stockage fichier propre à RH (§8 architecture) — document_ref transite
exclusivement par documents_v1 (TECH-024), jamais par un accès direct
au stockage.
"""

from datetime import date

import pytest
from django.test import override_settings

from core.identity.models import User
from modules.documentation.documents_v1 import attach
from modules.rh.models import Contract
from modules.rh.services import (
    create_contract,
    create_employee,
    link_document_to_contract,
    update_contract,
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
    return User.objects.create(username="proprietaire_contrat", full_name="Propriétaire")


# --- A. Création -------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateContract:
    def test_creates_a_real_contract(self, employee):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        assert contract.pk is not None
        assert Contract.objects.filter(pk=contract.pk).exists()

    def test_defaults_to_active_status(self, employee):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        assert contract.status == Contract.Status.ACTIVE

    def test_end_date_defaults_to_none(self, employee):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        assert contract.end_date is None

    def test_can_set_end_date_and_status_explicitly(self, employee):
        contract = create_contract(
            employee=employee, type="CDD", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), status=Contract.Status.EXPIRED,
        )
        assert contract.end_date == date(2026, 12, 31)
        assert contract.status == Contract.Status.EXPIRED

    def test_document_ref_defaults_to_none(self, employee):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        assert contract.document_ref is None


# --- B. Édition -----------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateContract:
    def test_updates_all_fields(self, employee):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        updated = update_contract(
            contract=contract, type="CDD", start_date=date(2026, 2, 1),
            end_date=date(2026, 12, 31), status=Contract.Status.TERMINATED,
            document_ref=99,
        )

        updated.refresh_from_db()
        assert updated.type == "CDD"
        assert updated.start_date == date(2026, 2, 1)
        assert updated.end_date == date(2026, 12, 31)
        assert updated.status == Contract.Status.TERMINATED
        assert updated.document_ref == 99

    def test_status_is_a_required_parameter(self, employee):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        with pytest.raises(TypeError):
            update_contract(
                contract=contract, type="CDD", start_date=date(2026, 1, 1),
                end_date=None, document_ref=None,
            )

    def test_persists_to_database(self, employee):
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))
        update_contract(
            contract=contract, type="Persisté", start_date=date(2026, 1, 1),
            end_date=None, status=Contract.Status.ACTIVE, document_ref=None,
        )
        assert Contract.objects.get(pk=contract.pk).type == "Persisté"


# --- C. Liaison de document — critère d'acceptation explicite -------------------


@pytest.mark.django_db
class TestDocumentLinking:
    def test_link_document_to_contract(self, employee, storage_root):
        owner = User.objects.create(username="proprietaire_link_contrat", full_name="P")
        document_ref = attach(content=b"x", filename="contrat.pdf", owner_user=owner)
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        updated = link_document_to_contract(
            contract=contract, document_ref=document_ref, requesting_user=owner
        )

        updated.refresh_from_db()
        assert updated.document_ref == document_ref

    def test_link_does_not_touch_other_fields(self, employee, storage_root):
        owner = User.objects.create(username="proprietaire_link_contrat2", full_name="P2")
        document_ref = attach(content=b"x", filename="x.pdf", owner_user=owner)
        contract = create_contract(
            employee=employee, type="CDI", start_date=date(2026, 1, 1),
            status=Contract.Status.ACTIVE,
        )

        link_document_to_contract(
            contract=contract, document_ref=document_ref, requesting_user=owner
        )

        contract.refresh_from_db()
        assert contract.type == "CDI"
        assert contract.status == Contract.Status.ACTIVE

    def test_link_refuses_a_document_the_requester_cannot_access(self, employee, storage_root):
        """Correction de sécurité (UI-404, audit Phase 1) : ne fait
        plus jamais confiance aveuglément au document_ref fourni."""
        from modules.documentation.documents_v1 import DocumentNotAccessibleError

        owner = User.objects.create(username="proprietaire_link_contrat3", full_name="P3")
        other_user = User.objects.create(username="sans_acces_link_contrat", full_name="Autre")
        document_ref = attach(content=b"x", filename="prive.pdf", owner_user=owner)
        contract = create_contract(employee=employee, type="CDI", start_date=date(2026, 1, 1))

        with pytest.raises(DocumentNotAccessibleError):
            link_document_to_contract(
                contract=contract, document_ref=document_ref, requesting_user=other_user
            )

        contract.refresh_from_db()
        assert contract.document_ref is None

    def test_create_with_a_real_document_ref_end_to_end(
        self, employee, owner, storage_root
    ):
        """Test explicitement requis par le contrat du ticket :
        création avec document lié, via le vrai documents.v1
        (TECH-024), pas un entier arbitraire."""
        document_ref = attach(
            content=b"contenu contractuel", filename="contrat.pdf", owner_user=owner
        )

        contract = create_contract(
            employee=employee, type="CDI", start_date=date(2026, 1, 1),
            document_ref=document_ref,
        )

        assert contract.document_ref == document_ref

    def test_no_file_is_ever_written_outside_the_documentation_tree(
        self, employee, owner, storage_root
    ):
        """Critère d'acceptation explicite du ticket : « aucun stockage
        fichier propre à RH pour les contrats » (§8 architecture).
        Vérifié directement sur le système de fichiers réel — pas
        supposé."""
        document_ref = attach(
            content=b"contenu contractuel", filename="contrat.pdf", owner_user=owner
        )
        create_contract(
            employee=employee, type="CDI", start_date=date(2026, 1, 1),
            document_ref=document_ref,
        )

        top_level_entries = {p.name for p in storage_root.iterdir()}
        assert top_level_entries == {"documentation"}
        assert not (storage_root / "rh").exists()


# --- D. Contrainte architecturale ---------------------------------------------------


class TestNoStorageImport:
    def test_services_never_imports_core_storage(self):
        """RH ne doit jamais écrire de fichier lui-même — vérifié par
        analyse AST du code réellement importé, même patron que la
        vérification équivalente pour modules.documentation dans
        test_rh_models.py (TECH-030)."""
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

    def test_services_never_imports_documentation_internal_models(self):
        """Seule la façade documents_v1 peut être importée — jamais les
        modèles internes de Documentation (architecture §15)."""
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
