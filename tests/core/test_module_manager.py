"""Tests du Module Manager — TECH-006.

Cf. architecture-technique-v1.md §12/§13/§14. Cas de référence obligatoire :
RH dépend de Documentation. Manifestes de test minimaux réutilisés pour les
scénarios génériques (Documentation/RH n'ont pas encore de manifest.yaml
réel : TECH-025/TECH-035).
"""

import pytest

from core.audit.models import AuditLog
from core.authz.models import Permission
from core.identity.models import User
from core.modules.manager import (
    DependencyError,
    ModuleManagerError,
    activate_module,
    install_module,
    version_satisfies,
)
from core.modules.manifest import parse_manifest_text
from core.modules.models import Module, ModuleDependency
from tests.core.test_manifest_parser import RH_MANIFEST_YAML

DOCUMENTATION_MANIFEST_YAML = """
id: documentation
name: "Documentation / Archivage"
version: "1.0.0"
db_schema: documentation
migrations_path: migrations/
"""

MODULE_B_NO_DEP_MANIFEST_YAML = """
id: module_b
name: "Module B (test)"
version: "1.0.0"
db_schema: module_b
migrations_path: migrations/
"""


@pytest.fixture
def actor(db):
    return User.objects.create(username="technicien", full_name="Technicien Test")


@pytest.mark.django_db
class TestInstallAndActivateWithoutDependency:
    def test_install_module_without_dependency(self, actor):
        manifest = parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML)
        module = install_module(manifest, actor=actor)
        assert module.pk == "module_b"
        assert module.state == Module.State.INSTALLED

    def test_activate_module_without_dependency(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        module = activate_module("module_b", actor=actor)
        assert module.state == Module.State.ACTIVATED


@pytest.mark.django_db
class TestRhDocumentationReferenceScenario:
    """Cas obligatoire : RH dépend de Documentation."""

    def test_install_rh_without_documentation_present_succeeds(self, actor):
        """RH doit être installable même sans aucune ligne Documentation en
        base (§ scénario obligatoire : « Documentation absent »)."""
        module = install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        assert module.pk == "rh"
        assert module.state == Module.State.INSTALLED
        assert not Module.objects.filter(pk="documentation").exists()

    def test_activate_rh_with_documentation_absent_is_refused(self, actor):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        with pytest.raises(DependencyError) as exc_info:
            activate_module("rh", actor=actor)
        assert exc_info.value.unsatisfied[0].module == "documentation"
        assert exc_info.value.unsatisfied[0].reason == "absent"
        assert "documentation" in str(exc_info.value)
        assert Module.objects.get(pk="rh").state == Module.State.INSTALLED

    def test_activate_rh_with_documentation_installed_but_inactive_is_refused(self, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        with pytest.raises(DependencyError) as exc_info:
            activate_module("rh", actor=actor)
        reason = exc_info.value.unsatisfied[0].reason
        assert "non activé" in reason
        assert "installé" in reason  # état actuel de Documentation : INSTALLED
        assert Module.objects.get(pk="rh").state == Module.State.INSTALLED

    def test_activate_documentation_then_activate_rh_succeeds(self, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)

        documentation = activate_module("documentation", actor=actor)
        assert documentation.state == Module.State.ACTIVATED

        rh = activate_module("rh", actor=actor)
        assert rh.state == Module.State.ACTIVATED
        assert rh.dependencies.filter(depends_on_module_id="documentation").exists()

    def test_error_message_lists_missing_dependency_explicitly(self, actor):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        with pytest.raises(DependencyError) as exc_info:
            activate_module("rh", actor=actor)
        assert "rh" in str(exc_info.value)
        assert "documentation" in str(exc_info.value)
        assert "absent" in str(exc_info.value)


@pytest.mark.django_db
class TestVersionConstraints:
    def test_compatible_version_satisfies_constraint(self):
        assert version_satisfies("1.2.0", ">=1.0.0") is True
        assert version_satisfies("1.0.0", ">=1.0.0") is True
        assert version_satisfies("1.0.0", "==1.0.0") is True

    def test_incompatible_version_does_not_satisfy_constraint(self):
        assert version_satisfies("0.9.0", ">=1.0.0") is False
        assert version_satisfies("2.0.0", "==1.0.0") is False

    def test_activation_refused_on_incompatible_installed_version(self, actor):
        old_documentation = DOCUMENTATION_MANIFEST_YAML.replace(
            'version: "1.0.0"', 'version: "0.5.0"'
        )
        install_module(parse_manifest_text(old_documentation), actor=actor)
        activate_module("documentation", actor=actor)  # actif, mais version 0.5.0

        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)  # exige >=1.0.0
        with pytest.raises(DependencyError) as exc_info:
            activate_module("rh", actor=actor)
        assert "version incompatible" in exc_info.value.unsatisfied[0].reason

    def test_dependency_absent_and_dependency_installed_but_inactive_are_distinguished(
        self, actor
    ):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        with pytest.raises(DependencyError) as exc_info:
            activate_module("rh", actor=actor)
        assert exc_info.value.unsatisfied[0].reason == "absent"


@pytest.mark.django_db
class TestAtomicity:
    def test_failed_activation_leaves_module_installed_not_activated(self, actor):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

        # Vérification réelle en base, pas seulement l'exception Python.
        rh = Module.objects.get(pk="rh")
        assert rh.state == Module.State.INSTALLED

    def test_failed_activation_creates_no_module_dependency_row(self, actor):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)
        assert ModuleDependency.objects.count() == 0

    def test_no_partial_permission_registered_after_failed_activation(self, actor):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        count_after_install = Permission.objects.filter(module_id="rh").count()

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

        assert Permission.objects.filter(module_id="rh").count() == count_after_install


@pytest.mark.django_db
class TestPermissionRegistration:
    def test_installing_registers_declared_permissions(self, actor):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        assert Permission.objects.filter(
            module_id="rh", resource="employee", action="read"
        ).exists()
        assert Permission.objects.filter(
            module_id="rh", resource="leave_request", action="approve"
        ).exists()

    def test_repeated_activation_does_not_duplicate_permissions_or_dependencies(self, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)

        before = Permission.objects.filter(module_id="rh").count()
        activate_module("rh", actor=actor)
        activate_module("rh", actor=actor)  # activation répétée : idempotente
        after = Permission.objects.filter(module_id="rh").count()

        assert before == after
        assert ModuleDependency.objects.filter(module_id="rh").count() == 1


@pytest.mark.django_db
class TestFinalStateAfterSuccess:
    def test_final_state_correct_after_install_and_activate(self, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        rh = activate_module("rh", actor=actor)

        assert rh.state == Module.State.ACTIVATED
        assert rh.name == "Ressources Humaines"
        dependency = rh.dependencies.get()
        assert dependency.depends_on_module_id == "documentation"
        assert dependency.version_constraint == ">=1.0.0"


@pytest.mark.django_db
class TestAuditTrail:
    def test_successful_install_is_audited(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        entry = AuditLog.objects.get(action="module.install", target="module_b")
        assert entry.actor_user == actor
        assert entry.metadata["status"] == "success"

    def test_successful_activation_is_audited(self, actor):
        install_module(parse_manifest_text(MODULE_B_NO_DEP_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        entry = AuditLog.objects.get(
            action="module.activate", target="module_b", metadata__status="success"
        )
        assert entry.actor_user == actor

    def test_denied_activation_is_audited_despite_the_rollback(self, actor):
        """L'entrée d'audit du refus doit survivre : aucune transaction
        d'écriture n'a même démarré (le refus est détecté avant)."""
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

        entry = AuditLog.objects.get(
            action="module.activate", target="rh", metadata__status="denied"
        )
        assert entry.actor_user == actor
        assert entry.metadata["unsatisfied"][0]["module"] == "documentation"


@pytest.mark.django_db
class TestActivatingUnknownModule:
    def test_activating_a_non_installed_module_raises_explicit_error(self, actor):
        with pytest.raises(ModuleManagerError):
            activate_module("does-not-exist", actor=actor)
