"""Parsing du manifeste de mise à jour — `update-manifest.yaml` (§19.2).

Format (§19.2, §19.1) :

```yaml
version: "1.1.0"
packages:
  - filename: corrux-core_1.1.0_amd64.deb
    sha256: <empreinte hexadécimale>
modules_impacted:
  - core
```

Ce module ne fait que parser — aucune vérification de signature ni de
checksum ici (cf. ops/update_service.py pour l'orchestration complète).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class UpdateManifestError(Exception):
    """Le manifeste de mise à jour est invalide ou incomplet."""


@dataclass(frozen=True)
class PackageEntry:
    filename: str
    sha256: str


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    packages: tuple[PackageEntry, ...]
    modules_impacted: tuple[str, ...]


def parse_update_manifest(manifest_path: Path) -> UpdateManifest:
    """Parse un update-manifest.yaml — lève UpdateManifestError si un
    champ obligatoire est absent ou mal formé, jamais une exception
    YAML brute non explicite."""
    try:
        raw = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as exc:
        raise UpdateManifestError(f"YAML invalide : {exc}") from exc

    if not isinstance(raw, dict):
        raise UpdateManifestError("Le manifeste doit être un mapping YAML.")

    for field_name in ("version", "packages", "modules_impacted"):
        if field_name not in raw:
            raise UpdateManifestError(f"Champ obligatoire manquant : {field_name}")

    if not isinstance(raw["packages"], list) or not raw["packages"]:
        raise UpdateManifestError("« packages » doit être une liste non vide.")

    packages = []
    for entry in raw["packages"]:
        if not isinstance(entry, dict) or "filename" not in entry or "sha256" not in entry:
            raise UpdateManifestError(
                "Chaque entrée de « packages » doit avoir filename et sha256."
            )
        packages.append(PackageEntry(filename=entry["filename"], sha256=entry["sha256"]))

    if not isinstance(raw["modules_impacted"], list):
        raise UpdateManifestError("« modules_impacted » doit être une liste.")

    return UpdateManifest(
        version=str(raw["version"]),
        packages=tuple(packages),
        modules_impacted=tuple(raw["modules_impacted"]),
    )
