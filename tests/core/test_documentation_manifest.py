"""Installation/activation de Documentation via son vrai manifest.yaml
— TECH-025.

Critère d'acceptation explicite du ticket : « le Module Manager
installe/active Documentation sans dépendance à satisfaire ». Charge le
fichier réel sur disque (pas une chaîne synthétique) et appelle les
primitives réelles de TECH-006/007 — aucune logique de manifeste
réimplémentée ici.
"""

from pathlib import Path

import pytest

from core.authz.models import Permission
from core.identity.models import User
from core.modules.manager import activate_module, install_module
from core.modules.manifest import parse_manifest_file
from core.modules.models import Module

MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "modules"
    / "documentation"
    / "manifest.yaml"
)


@pytest.fixture
def actor(db):
    return User.objects.create(username="technicien_manifest", full_name="Technicien")


@pytest.mark.django_db
class TestDocumentationManifestFile:
    def test_the_real_file_exists_and_parses(self):
        assert MANIFEST_PATH.exists()
        manifest = parse_manifest_file(MANIFEST_PATH)
        assert manifest.id == "documentation"

    def test_manifest_declares_no_dependency(self):
        manifest = parse_manifest_file(MANIFEST_PATH)
        assert manifest.depends_on == ()

    def test_manifest_provides_documents_v1(self):
        manifest = parse_manifest_file(MANIFEST_PATH)
        assert "documents.v1" in manifest.provides_api

    def test_manifest_declares_document_read_permission(self):
        manifest = parse_manifest_file(MANIFEST_PATH)
        resources = {p.resource: p.actions for p in manifest.permissions}
        assert resources.get("document") == ("read",)


@pytest.mark.django_db
class TestDocumentationInstallActivateIsolated:
    def test_install_from_the_real_manifest_succeeds_without_any_dependency(self, actor):
        manifest = parse_manifest_file(MANIFEST_PATH)

        module = install_module(manifest, actor=actor)

        assert module.id == "documentation"
        assert Module.objects.filter(pk="documentation").exists()

    def test_activate_from_the_real_manifest_succeeds_without_any_dependency(self, actor):
        manifest = parse_manifest_file(MANIFEST_PATH)
        install_module(manifest, actor=actor)

        module = activate_module("documentation", actor=actor)

        assert module.state == Module.State.ACTIVATED

    def test_install_registers_the_declared_permission(self, actor):
        manifest = parse_manifest_file(MANIFEST_PATH)
        install_module(manifest, actor=actor)

        assert Permission.objects.filter(
            module_id="documentation", resource="document", action="read"
        ).exists()

    def test_manifest_snapshot_matches_the_real_file(self, actor):
        manifest = parse_manifest_file(MANIFEST_PATH)
        module = install_module(manifest, actor=actor)

        assert module.manifest_snapshot["id"] == "documentation"
        assert module.manifest_snapshot["provides_api"] == ["documents.v1"]
