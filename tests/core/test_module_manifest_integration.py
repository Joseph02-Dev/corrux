"""Tests d'intégration parser + modèles — TECH-005 (partie C).

Vérifie que la sortie du parseur est directement utilisable pour peupler
les modèles Module/ModuleDependency (sans anticiper la logique
d'installation/activation de TECH-006), et qu'un manifeste rejeté ne
laisse jamais d'état partiel en base.
"""

import pytest

from core.modules.manifest import ManifestError, parse_manifest_text
from core.modules.models import Module, ModuleDependency
from tests.core.test_manifest_parser import RH_MANIFEST_YAML


@pytest.mark.django_db
class TestParsedManifestFeedsModuleManager:
    def test_valid_manifest_can_populate_module_and_dependency_rows(self):
        # documentation doit exister pour que la FK de dépendance soit posable
        # (TECH-006 décidera de l'ordre/atomicité réels de l'installation ;
        # ici on vérifie seulement que les données du parser sont directement
        # exploitables pour peupler les modèles TECH-005).
        Module.objects.create(
            id="documentation",
            name="Documentation / Archivage",
            version="1.0.0",
            state=Module.State.ACTIVATED,
            manifest_snapshot={"id": "documentation"},
        )

        manifest = parse_manifest_text(RH_MANIFEST_YAML)

        rh_module = Module.objects.create(
            id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            state=Module.State.INSTALLED,
            manifest_snapshot=manifest.raw,
        )
        for dep in manifest.depends_on:
            ModuleDependency.objects.create(
                module=rh_module,
                depends_on_module=Module.objects.get(pk=dep.module),
                version_constraint=dep.version_constraint,
            )

        rh_module.refresh_from_db()
        assert rh_module.name == "Ressources Humaines"
        assert rh_module.manifest_snapshot["id"] == "rh"
        assert rh_module.dependencies.count() == 1
        assert rh_module.dependencies.first().depends_on_module_id == "documentation"


@pytest.mark.django_db
class TestNoPartialStateOnRejectedManifest:
    def test_invalid_manifest_raises_before_any_data_is_produced(self):
        with pytest.raises(ManifestError):
            parse_manifest_text("id: rh\n")  # champs obligatoires manquants

        # Le parseur n'écrit jamais en base : un rejet ne peut donc laisser
        # aucun état partiel, quel que soit le module testé.
        assert Module.objects.count() == 0
        assert ModuleDependency.objects.count() == 0

    def test_invalid_dependency_leaves_no_partial_module_dependency_row(self):
        Module.objects.create(
            id="rh", name="RH", version="1.0.0", state=Module.State.INSTALLED,
            manifest_snapshot={},
        )
        malformed = (
            'id: rh\nname: "RH"\nversion: "1.0.0"\ndb_schema: rh\n'
            'migrations_path: migrations/\n'
            "depends_on:\n  - module: documentation\n"  # version manquante
        )
        with pytest.raises(ManifestError):
            parse_manifest_text(malformed)

        assert ModuleDependency.objects.count() == 0
