"""Tests des modèles Module / ModuleDependency — TECH-005 (partie A).

Cf. architecture-technique-v1.md §7. Aucune logique d'installation/
activation ici (TECH-006) : uniquement la structure de données.
"""

import pytest
from django.db import IntegrityError, transaction

from core.modules.models import Module, ModuleDependency


def _make_module(module_id="documentation", state=Module.State.INSTALLED):
    return Module.objects.create(
        id=module_id,
        name="Documentation / Archivage",
        version="1.0.0",
        state=state,
        manifest_snapshot={"id": module_id, "name": "Documentation / Archivage"},
    )


@pytest.mark.django_db
class TestModule:
    def test_create_valid_module(self):
        module = _make_module()
        assert module.pk == "documentation"
        assert module.state == Module.State.INSTALLED

    def test_id_is_the_primary_key_and_unique(self):
        _make_module("documentation")
        with pytest.raises(IntegrityError), transaction.atomic():
            _make_module("documentation")

    def test_manifest_snapshot_round_trips_as_json(self):
        snapshot = {"id": "rh", "depends_on": [{"module": "documentation", "version": ">=1.0.0"}]}
        module = Module.objects.create(
            id="rh", name="RH", version="1.0.0", state=Module.State.INSTALLED,
            manifest_snapshot=snapshot,
        )
        module.refresh_from_db()
        assert module.manifest_snapshot == snapshot

    def test_state_choices_are_restricted(self):
        module = _make_module()
        assert module.state in {"installed", "activated", "deactivated"}


@pytest.mark.django_db
class TestModuleDependency:
    def test_create_valid_dependency(self):
        rh = _make_module("rh")
        documentation = _make_module("documentation")
        dep = ModuleDependency.objects.create(
            module=rh, depends_on_module=documentation, version_constraint=">=1.0.0"
        )
        assert dep.pk is not None
        assert rh.dependencies.count() == 1
        assert documentation.dependents.count() == 1

    def test_duplicate_dependency_pair_is_rejected(self):
        rh = _make_module("rh")
        documentation = _make_module("documentation")
        ModuleDependency.objects.create(
            module=rh, depends_on_module=documentation, version_constraint=">=1.0.0"
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            ModuleDependency.objects.create(
                module=rh, depends_on_module=documentation, version_constraint=">=1.0.0"
            )

    def test_deleting_module_cascades_to_its_dependencies(self):
        rh = _make_module("rh")
        documentation = _make_module("documentation")
        ModuleDependency.objects.create(
            module=rh, depends_on_module=documentation, version_constraint=">=1.0.0"
        )
        rh.delete()
        assert ModuleDependency.objects.count() == 0
