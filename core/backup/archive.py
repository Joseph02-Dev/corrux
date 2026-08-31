"""Archivage du stockage documentaire — §11.

Utilise `tarfile` (bibliothèque standard) plutôt qu'un appel shell à
`tar` : aucune commande construite, donc aucune surface d'injection
possible pour cette étape. Archive fidèle de l'arborescence
`<module>/<uuid>/<filename>` de TECH-004, sans réinterpréter son
contenu (aucune logique métier documentaire ici, cohérent avec TECH-004).
"""

from __future__ import annotations

import tarfile
from pathlib import Path


class StorageArchiveError(Exception):
    """L'archivage du stockage documentaire a échoué."""


def archive_storage(storage_root: Path, destination: Path) -> Path:
    """Archive récursivement `storage_root` (tar.gz) vers `destination`."""
    if not storage_root.exists():
        raise StorageArchiveError(f"Racine de stockage introuvable : {storage_root}")
    try:
        with tarfile.open(destination, "w:gz") as tar:
            tar.add(storage_root, arcname=storage_root.name)
    except (OSError, tarfile.TarError) as exc:
        destination.unlink(missing_ok=True)
        raise StorageArchiveError(f"Archivage du stockage échoué : {exc}") from exc
    return destination
