"""Tests du parseur de manifest.yaml — TECH-005 (partie B).

Le manifeste RH d'architecture-technique-v1.md §13 est le cas de
référence obligatoire.
"""

import pytest

from core.modules.manifest import (
    ApiConsumption,
    DependencySpec,
    ManifestError,
    PermissionSpec,
    parse_manifest_text,
)

# Cas de référence exact, recopié d'architecture-technique-v1.md §13.
RH_MANIFEST_YAML = """
id: rh
name: "Ressources Humaines"
version: "1.0.0"
depends_on:
  - module: documentation
    version: ">=1.0.0"
permissions:
  - resource: employee
    actions: [read, write]
  - resource: contract
    actions: [read, write]
  - resource: leave_request
    actions: [read, write, approve]
provides_api: []
consumes_api:
  - module: documentation
    interface: "documents.v1"
db_schema: rh
migrations_path: migrations/
"""

MINIMAL_VALID_MANIFEST_YAML = """
id: documentation
name: "Documentation / Archivage"
version: "1.0.0"
db_schema: documentation
migrations_path: migrations/
"""


class TestReferenceManifest:
    def test_rh_manifest_identity(self):
        manifest = parse_manifest_text(RH_MANIFEST_YAML)
        assert manifest.id == "rh"
        assert manifest.name == "Ressources Humaines"
        assert manifest.version == "1.0.0"
        assert manifest.db_schema == "rh"
        assert manifest.migrations_path == "migrations/"

    def test_rh_manifest_dependency_on_documentation(self):
        manifest = parse_manifest_text(RH_MANIFEST_YAML)
        assert manifest.depends_on == (
            DependencySpec(module="documentation", version_constraint=">=1.0.0"),
        )

    def test_rh_manifest_consumes_documents_v1_api(self):
        manifest = parse_manifest_text(RH_MANIFEST_YAML)
        assert manifest.consumes_api == (
            ApiConsumption(module="documentation", interface="documents.v1"),
        )
        assert manifest.provides_api == ()

    def test_rh_manifest_declared_permissions(self):
        manifest = parse_manifest_text(RH_MANIFEST_YAML)
        assert (
            PermissionSpec(resource="employee", actions=("read", "write"))
            in manifest.permissions
        )
        assert PermissionSpec(
            resource="leave_request", actions=("read", "write", "approve")
        ) in manifest.permissions
        assert len(manifest.permissions) == 3

    def test_rh_manifest_raw_snapshot_is_preserved(self):
        manifest = parse_manifest_text(RH_MANIFEST_YAML)
        assert manifest.raw["id"] == "rh"
        assert manifest.raw["depends_on"][0]["module"] == "documentation"


class TestMinimalValidManifest:
    def test_minimal_manifest_is_valid_with_empty_lists_by_default(self):
        manifest = parse_manifest_text(MINIMAL_VALID_MANIFEST_YAML)
        assert manifest.id == "documentation"
        assert manifest.depends_on == ()
        assert manifest.permissions == ()
        assert manifest.provides_api == ()
        assert manifest.consumes_api == ()


class TestInvalidManifests:
    def test_empty_manifest_is_rejected(self):
        with pytest.raises(ManifestError):
            parse_manifest_text("")

    def test_malformed_yaml_is_rejected(self):
        with pytest.raises(ManifestError):
            parse_manifest_text("id: rh\n  name: [unclosed")

    def test_yaml_that_is_not_a_mapping_is_rejected(self):
        with pytest.raises(ManifestError):
            parse_manifest_text("- just\n- a\n- list\n")

    def test_missing_required_scalar_field_is_rejected(self):
        yaml_text = MINIMAL_VALID_MANIFEST_YAML.replace("id: documentation\n", "")
        with pytest.raises(ManifestError, match="id"):
            parse_manifest_text(yaml_text)

    def test_wrong_type_for_scalar_field_is_rejected(self):
        yaml_text = MINIMAL_VALID_MANIFEST_YAML.replace(
            'version: "1.0.0"', "version: [1, 0, 0]"
        )
        with pytest.raises(ManifestError):
            parse_manifest_text(yaml_text)

    def test_malformed_dependency_missing_module_key_is_rejected(self):
        yaml_text = MINIMAL_VALID_MANIFEST_YAML + "depends_on:\n  - version: \">=1.0.0\"\n"
        with pytest.raises(ManifestError, match="depends_on"):
            parse_manifest_text(yaml_text)

    def test_malformed_dependency_not_a_list_is_rejected(self):
        yaml_text = MINIMAL_VALID_MANIFEST_YAML + "depends_on: not-a-list\n"
        with pytest.raises(ManifestError):
            parse_manifest_text(yaml_text)

    def test_malformed_permission_empty_actions_is_rejected(self):
        yaml_text = (
            MINIMAL_VALID_MANIFEST_YAML + "permissions:\n  - resource: employee\n    actions: []\n"
        )
        with pytest.raises(ManifestError, match="permissions"):
            parse_manifest_text(yaml_text)

    def test_malformed_permission_missing_resource_is_rejected(self):
        yaml_text = (
            MINIMAL_VALID_MANIFEST_YAML + "permissions:\n  - actions: [read]\n"
        )
        with pytest.raises(ManifestError):
            parse_manifest_text(yaml_text)

    def test_invalid_version_format_is_rejected(self):
        yaml_text = MINIMAL_VALID_MANIFEST_YAML.replace(
            'version: "1.0.0"', 'version: "not-a-version"'
        )
        with pytest.raises(ManifestError, match="version"):
            parse_manifest_text(yaml_text)

    def test_invalid_dependency_version_constraint_is_rejected(self):
        yaml_text = (
            MINIMAL_VALID_MANIFEST_YAML
            + "depends_on:\n  - module: documentation\n    version: \"whatever\"\n"
        )
        with pytest.raises(ManifestError):
            parse_manifest_text(yaml_text)

    def test_invalid_module_id_is_rejected(self):
        yaml_text = MINIMAL_VALID_MANIFEST_YAML.replace(
            "id: documentation", "id: Not_Valid_ID!"
        )
        with pytest.raises(ManifestError, match="id"):
            parse_manifest_text(yaml_text)

    def test_structurally_valid_yaml_not_matching_contract_is_rejected(self):
        """YAML syntaxiquement correct, mais qui ne respecte pas le contrat
        du manifeste (aucun des champs attendus)."""
        with pytest.raises(ManifestError):
            parse_manifest_text("some_unrelated_key: 42\nanother: true\n")
