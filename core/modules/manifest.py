"""Parsing et validation de `manifest.yaml` — TECH-005.

Cf. architecture-technique-v1.md §13 : format de manifeste, exemple de
référence (module RH). Indépendant de HTTP et de toute UI ; ne dépend
d'aucun module métier (Documentation, RH) — le manifeste RH n'est utilisé
ici que comme *donnée de test*, jamais importé comme code.

Sécurité : chargement YAML strictement via `yaml.safe_load` (jamais
`yaml.load`/`unsafe_load`) — le contenu d'un manifeste est une donnée non
fiable, aucune exécution de code n'est permise.

Ce module ne modifie jamais la base de données : c'est une fonction pure
(texte YAML -> structure Python), aucun état partiel ne peut donc être
écrit en base par un manifeste rejeté (le Module Manager, TECH-006,
décidera quand écrire, à partir du résultat déjà validé).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Conventions choisies (non formellement imposées par §13, mais seules
# formes utilisées par tous les exemples du document — cf. rapport
# TECH-005 pour la justification) : slug minuscule pour les identifiants,
# semver MAJOR.MINOR.PATCH pour les versions.
MODULE_ID_PATTERN = r"^[a-z][a-z0-9_-]*$"
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"
VERSION_CONSTRAINT_PATTERN = r"^(>=|<=|==|~=|>|<)?\d+\.\d+\.\d+$"

_MODULE_ID_RE = re.compile(MODULE_ID_PATTERN)
_VERSION_RE = re.compile(VERSION_PATTERN)
_VERSION_CONSTRAINT_RE = re.compile(VERSION_CONSTRAINT_PATTERN)

_REQUIRED_SCALAR_FIELDS = ("id", "name", "version", "db_schema", "migrations_path")
_LIST_FIELDS = ("depends_on", "permissions", "provides_api", "consumes_api")


class ManifestError(Exception):
    """Manifeste invalide : YAML mal formé ou structure non conforme au
    contrat §13. Message toujours explicite, exploitable par TECH-006."""


@dataclass(frozen=True)
class DependencySpec:
    module: str
    version_constraint: str


@dataclass(frozen=True)
class PermissionSpec:
    resource: str
    actions: tuple[str, ...]


@dataclass(frozen=True)
class ApiConsumption:
    module: str
    interface: str


@dataclass(frozen=True)
class ParsedManifest:
    """Résultat structuré d'un manifeste valide, utilisable par le futur
    Module Manager (TECH-006) sans avoir à reparser le YAML."""

    id: str
    name: str
    version: str
    db_schema: str
    migrations_path: str
    depends_on: tuple[DependencySpec, ...] = field(default_factory=tuple)
    permissions: tuple[PermissionSpec, ...] = field(default_factory=tuple)
    provides_api: tuple[str, ...] = field(default_factory=tuple)
    consumes_api: tuple[ApiConsumption, ...] = field(default_factory=tuple)
    raw: dict = field(default_factory=dict)  # snapshot brut, pour manifest_snapshot (JSONB)


def _require_dict(value, context: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"{context} doit être un mapping YAML (objet), reçu : {value!r}")
    return value


def _require_str(value, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{context} doit être une chaîne non vide, reçu : {value!r}")
    return value


def _require_list(value, context: str) -> list:
    if not isinstance(value, list):
        raise ManifestError(f"{context} doit être une liste, reçu : {value!r}")
    return value


def _parse_depends_on(raw_list) -> tuple[DependencySpec, ...]:
    items = []
    for i, entry in enumerate(_require_list(raw_list, "depends_on")):
        entry = _require_dict(entry, f"depends_on[{i}]")
        module = _require_str(entry.get("module"), f"depends_on[{i}].module")
        version = _require_str(entry.get("version"), f"depends_on[{i}].version")
        if not _MODULE_ID_RE.match(module):
            raise ManifestError(f"depends_on[{i}].module invalide : {module!r}")
        if not _VERSION_CONSTRAINT_RE.match(version):
            raise ManifestError(f"depends_on[{i}].version invalide : {version!r}")
        items.append(DependencySpec(module=module, version_constraint=version))
    return tuple(items)


def _parse_permissions(raw_list) -> tuple[PermissionSpec, ...]:
    items = []
    for i, entry in enumerate(_require_list(raw_list, "permissions")):
        entry = _require_dict(entry, f"permissions[{i}]")
        resource = _require_str(entry.get("resource"), f"permissions[{i}].resource")
        actions = _require_list(entry.get("actions"), f"permissions[{i}].actions")
        if not actions:
            raise ManifestError(f"permissions[{i}].actions ne peut pas être vide")
        for j, action in enumerate(actions):
            _require_str(action, f"permissions[{i}].actions[{j}]")
        items.append(PermissionSpec(resource=resource, actions=tuple(actions)))
    return tuple(items)


def _parse_provides_api(raw_list) -> tuple[str, ...]:
    items = _require_list(raw_list, "provides_api")
    return tuple(_require_str(item, "provides_api[]") for item in items)


def _parse_consumes_api(raw_list) -> tuple[ApiConsumption, ...]:
    items = []
    for i, entry in enumerate(_require_list(raw_list, "consumes_api")):
        entry = _require_dict(entry, f"consumes_api[{i}]")
        module = _require_str(entry.get("module"), f"consumes_api[{i}].module")
        interface = _require_str(entry.get("interface"), f"consumes_api[{i}].interface")
        items.append(ApiConsumption(module=module, interface=interface))
    return tuple(items)


def parse_manifest_text(yaml_text: str) -> ParsedManifest:
    """Parse et valide le contenu YAML d'un manifest.yaml.

    Lève ManifestError avec un message explicite pour tout manifeste
    invalide (YAML mal formé, champ obligatoire absent, type incorrect,
    dépendance/permission mal formée, identifiant/version hors format).
    """
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"YAML invalide : {exc}") from exc

    if raw is None:
        raise ManifestError("Manifeste vide.")
    raw = _require_dict(raw, "Le manifeste")

    for field_name in _REQUIRED_SCALAR_FIELDS:
        if field_name not in raw:
            raise ManifestError(f"Champ obligatoire absent : {field_name!r}")

    module_id = _require_str(raw["id"], "id")
    if not _MODULE_ID_RE.match(module_id):
        raise ManifestError(f"id invalide (attendu un slug minuscule) : {module_id!r}")

    name = _require_str(raw["name"], "name")

    version = _require_str(raw["version"], "version")
    if not _VERSION_RE.match(version):
        raise ManifestError(f"version invalide (attendu MAJOR.MINOR.PATCH) : {version!r}")

    db_schema = _require_str(raw["db_schema"], "db_schema")
    migrations_path = _require_str(raw["migrations_path"], "migrations_path")

    depends_on = _parse_depends_on(raw.get("depends_on", []))
    permissions = _parse_permissions(raw.get("permissions", []))
    provides_api = _parse_provides_api(raw.get("provides_api", []))
    consumes_api = _parse_consumes_api(raw.get("consumes_api", []))

    return ParsedManifest(
        id=module_id,
        name=name,
        version=version,
        db_schema=db_schema,
        migrations_path=migrations_path,
        depends_on=depends_on,
        permissions=permissions,
        provides_api=provides_api,
        consumes_api=consumes_api,
        raw=raw,
    )


def parse_manifest_file(path: str | Path) -> ParsedManifest:
    """Charge et parse un manifest.yaml depuis un chemin disque."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Impossible de lire le manifeste {path} : {exc}") from exc
    return parse_manifest_text(text)
