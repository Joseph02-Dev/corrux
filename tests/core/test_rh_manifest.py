"""Installation/activation de RH via son vrai manifest.yaml — TECH-035.

Critère d'acceptation explicite du ticket : « le Module Manager refuse
l'activation de RH sans Documentation actif ; réussit sinon (critère de
fin V1, cas réel non simulé) ». Complète TECH-006 avec le cas RH réel :
charge les DEUX fichiers réels sur disque (Documentation ET RH, pas des
chaînes synthétiques) et appelle les primitives réelles de TECH-006/007
— aucune logique de manifeste ou de dépendance réimplémentée ici.
"""

from pathlib import Path

import pytest

from core.authz.models import Permission
from core.identity.models import User
from core.modules.manager import (
    ActiveDependentError,
    DependencyError,
    activate_module,
    deactivate_module,
    install_module,
)
from core.modules.manifest import parse_manifest_file
from core.modules.models import Module

RH_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent / "modules" / "rh" / "manifest.yaml"
)
DOCUMENTATION_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "modules"
    / "documentation"
    / "manifest.yaml"
)


@pytest.fixture
def actor(db):
    return User.objects.create(username="technicien_rh_manifest", full_name="Technicien")


def _install_and_activate_documentation(actor):
    manifest = parse_manifest_file(DOCUMENTATION_MANIFEST_PATH)
    install_module(manifest, actor=actor)
    return activate_module("documentation", actor=actor)


# --- A. Parsing du fichier réel --------------------------------------------------


@pytest.mark.django_db
class TestRhManifestFile:
    def test_the_real_file_exists_and_parses(self):
        assert RH_MANIFEST_PATH.exists()
        manifest = parse_manifest_file(RH_MANIFEST_PATH)
        assert manifest.id == "rh"

    def test_manifest_depends_on_documentation(self):
        manifest = parse_manifest_file(RH_MANIFEST_PATH)
        assert len(manifest.depends_on) == 1
        assert manifest.depends_on[0].module == "documentation"
        assert manifest.depends_on[0].version_constraint == ">=1.0.0"

    def test_manifest_consumes_documents_v1(self):
        manifest = parse_manifest_file(RH_MANIFEST_PATH)
        assert len(manifest.consumes_api) == 1
        assert manifest.consumes_api[0].module == "documentation"
        assert manifest.consumes_api[0].interface == "documents.v1"

    def test_manifest_provides_no_api(self):
        manifest = parse_manifest_file(RH_MANIFEST_PATH)
        assert manifest.provides_api == ()

    def test_manifest_declares_the_three_permission_resources(self):
        manifest = parse_manifest_file(RH_MANIFEST_PATH)
        resources = {p.resource: p.actions for p in manifest.permissions}
        assert resources == {
            "employee": ("read", "write"),
            "contract": ("read", "write"),
            "leave_request": ("read", "write", "approve"),
        }


# --- B. Refus sans Documentation active — critère d'acceptation explicite ------


@pytest.mark.django_db
class TestActivationRefusedWithoutDocumentation:
    def test_activation_is_refused_when_documentation_is_not_installed(self, actor):
        manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(manifest, actor=actor)

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

    def test_activation_is_refused_when_documentation_is_installed_but_not_active(
        self, actor
    ):
        documentation_manifest = parse_manifest_file(DOCUMENTATION_MANIFEST_PATH)
        install_module(documentation_manifest, actor=actor)
        # Documentation installée mais jamais activée.

        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

    def test_refused_activation_leaves_rh_not_activated(self, actor):
        manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(manifest, actor=actor)

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

        assert Module.objects.get(pk="rh").state != Module.State.ACTIVATED


# --- C. Réussite avec Documentation active — cas réel non simulé ---------------


@pytest.mark.django_db
class TestActivationSucceedsWithDocumentation:
    def test_install_and_activate_rh_after_documentation_is_active(self, actor):
        _install_and_activate_documentation(actor)

        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)
        module = activate_module("rh", actor=actor)

        assert module.state == Module.State.ACTIVATED

    def test_install_registers_the_three_declared_permissions(self, actor):
        _install_and_activate_documentation(actor)

        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)

        assert Permission.objects.filter(
            module_id="rh", resource="employee", action="read"
        ).exists()
        assert Permission.objects.filter(
            module_id="rh", resource="employee", action="write"
        ).exists()
        assert Permission.objects.filter(
            module_id="rh", resource="contract", action="read"
        ).exists()
        assert Permission.objects.filter(
            module_id="rh", resource="leave_request", action="approve"
        ).exists()

    def test_deactivating_documentation_is_blocked_while_rh_is_active(self, actor):
        """Règle inverse déjà posée par TECH-007 — revérifiée avec le
        cas RH réel, pas seulement un module de test synthétique."""
        _install_and_activate_documentation(actor)
        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)
        activate_module("rh", actor=actor)

        with pytest.raises(ActiveDependentError):
            deactivate_module("documentation", actor=actor)
