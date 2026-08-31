"""Tests de désactivation du Module Manager — TECH-007.

Cf. architecture-technique-v1.md §12 étape 5, §14 (règle inverse de
l'activation). Cas de référence obligatoire : Documentation ne doit pas
pouvoir être désactivé tant que RH (qui en dépend) est actif.
"""

import pytest

from core.audit.models import AuditLog
from core.authz.models import Permission
from core.identity.models import User
from core.modules.manager import (
    ActiveDependentError,
    ModuleManagerError,
    activate_module,
    deactivate_module,
    install_module,
)
from core.modules.manifest import parse_manifest_text
from core.modules.models import Module, ModuleDependency
from tests.core.test_manifest_parser import RH_MANIFEST_YAML
from tests.core.test_module_manager import (
    DOCUMENTATION_MANIFEST_YAML,
    MODULE_B_NO_DEP_MANIFEST_YAML,
)


@pytest.fixture
def actor(db):
    return User.objects.create(username="technicien", full_name="Technicien Test")


def _install_and_activate_documentation_and_rh(actor):
    install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
    install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
    activate_module("documentation", actor=actor)
    activate_module("rh", actor=actor)


@pytest.mark.django_db
class TestDeactivationWithoutActiveDependent:
    def test_deactivating_module_without_dependent_succeeds(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)

        module = deactivate_module("module_b", actor=actor)

        assert module.state == Module.State.DEACTIVATED
        assert Module.objects.get(pk="module_b").state == Module.State.DEACTIVATED


@pytest.mark.django_db
class TestDeactivationBlockedByActiveDependent:
    """Cas réel obligatoire : Documentation actif + RH actif."""

    def test_deactivating_documentation_with_rh_active_is_refused(self, actor):
        _install_and_activate_documentation_and_rh(actor)

        with pytest.raises(ActiveDependentError) as exc_info:
            deactivate_module("documentation", actor=actor)

        assert "rh" in exc_info.value.active_dependents
        assert "documentation" in str(exc_info.value)
        assert "rh" in str(exc_info.value)

    def test_refused_deactivation_leaves_no_state_change_in_database(self, actor):
        _install_and_activate_documentation_and_rh(actor)

        with pytest.raises(ActiveDependentError):
            deactivate_module("documentation", actor=actor)

        # Vérification réelle en base, pas seulement l'exception Python.
        assert Module.objects.get(pk="documentation").state == Module.State.ACTIVATED

    def test_refused_deactivation_deletes_no_data(self, actor):
        _install_and_activate_documentation_and_rh(actor)
        permissions_before = set(
            Permission.objects.filter(module_id="documentation").values_list("id", flat=True)
        )
        dependencies_before = set(ModuleDependency.objects.values_list("id", flat=True))

        with pytest.raises(ActiveDependentError):
            deactivate_module("documentation", actor=actor)

        permissions_after = set(
            Permission.objects.filter(module_id="documentation").values_list("id", flat=True)
        )
        dependencies_after = set(ModuleDependency.objects.values_list("id", flat=True))
        assert permissions_before == permissions_after
        assert dependencies_before == dependencies_after


@pytest.mark.django_db
class TestDeactivationAllowedWhenDependentInactive:
    """Cas réel obligatoire : Documentation actif, RH désactivé/non actif
    → désactivation de Documentation autorisée."""

    def test_deactivation_allowed_when_dependent_was_never_activated(self, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)  # RH: installed only
        activate_module("documentation", actor=actor)

        module = deactivate_module("documentation", actor=actor)
        assert module.state == Module.State.DEACTIVATED

    def test_deactivation_allowed_when_dependent_was_deactivated(self, actor):
        _install_and_activate_documentation_and_rh(actor)
        deactivate_module("rh", actor=actor)  # RH n'est plus actif

        module = deactivate_module("documentation", actor=actor)
        assert module.state == Module.State.DEACTIVATED


@pytest.mark.django_db
class TestDataPreservationAndReactivation:
    def test_module_data_remains_present_after_deactivation(self, actor):
        _install_and_activate_documentation_and_rh(actor)
        permission_count_before = Permission.objects.filter(module_id="rh").count()

        deactivate_module("rh", actor=actor)

        # Le module lui-même, ses permissions et sa dépendance restent en base.
        assert Module.objects.filter(pk="rh").exists()
        assert Permission.objects.filter(module_id="rh").count() == permission_count_before

    def test_reactivation_after_deactivation_reaches_activated_state(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        deactivate_module("module_b", actor=actor)

        module = activate_module("module_b", actor=actor)
        assert module.state == Module.State.ACTIVATED

    def test_reactivation_preserves_permissions_without_loss(self, actor):
        _install_and_activate_documentation_and_rh(actor)
        permissions_before = set(
            Permission.objects.filter(module_id="rh").values_list(
                "resource", "action"
            )
        )

        deactivate_module("rh", actor=actor)
        activate_module("rh", actor=actor)

        permissions_after = set(
            Permission.objects.filter(module_id="rh").values_list(
                "resource", "action"
            )
        )
        assert permissions_before == permissions_after
        assert len(permissions_after) > 0

    def test_reactivation_does_not_duplicate_permissions_or_dependencies(self, actor):
        _install_and_activate_documentation_and_rh(actor)
        deactivate_module("rh", actor=actor)

        permission_count_before = Permission.objects.filter(module_id="rh").count()
        dependency_count_before = ModuleDependency.objects.filter(module_id="rh").count()

        activate_module("rh", actor=actor)

        assert Permission.objects.filter(module_id="rh").count() == permission_count_before
        assert ModuleDependency.objects.filter(module_id="rh").count() == dependency_count_before
        assert dependency_count_before == 1


@pytest.mark.django_db
class TestIdempotency:
    def test_deactivating_an_already_deactivated_module_is_a_noop(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        deactivate_module("module_b", actor=actor)

        module = deactivate_module("module_b", actor=actor)  # répété
        assert module.state == Module.State.DEACTIVATED

    def test_deactivating_a_never_activated_module_is_a_noop(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        module = deactivate_module("module_b", actor=actor)  # jamais activé
        assert module.state == Module.State.INSTALLED  # inchangé, pas une erreur


@pytest.mark.django_db
class TestAuditTrail:
    def test_successful_deactivation_is_audited(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)

        deactivate_module("module_b", actor=actor)

        entry = AuditLog.objects.get(
            action="module.deactivate", target="module_b", metadata__status="success"
        )
        assert entry.actor_user == actor

    def test_denied_deactivation_is_audited_despite_the_rollback(self, actor):
        _install_and_activate_documentation_and_rh(actor)

        with pytest.raises(ActiveDependentError):
            deactivate_module("documentation", actor=actor)

        entry = AuditLog.objects.get(
            action="module.deactivate", target="documentation", metadata__status="denied"
        )
        assert entry.actor_user == actor
        assert "rh" in entry.metadata["active_dependents"]


@pytest.mark.django_db
class TestDeactivatingUnknownModule:
    def test_deactivating_a_non_installed_module_raises_explicit_error(self, actor):
        with pytest.raises(ModuleManagerError):
            deactivate_module("does-not-exist", actor=actor)
