"""Garde-fou support de sauvegarde — §11.1.

La destination de sauvegarde doit être un volume physiquement distinct du
disque système — jamais un simple sous-dossier du disque système.
Vérification par comparaison `st_dev` (device id du filesystem), pas par
heuristique de chemin.
"""

from __future__ import annotations

import os
from pathlib import Path


class BackupDestinationError(Exception):
    """La destination de sauvegarde n'est pas un volume distinct du disque système."""


def is_separate_volume(destination: Path, system_root: Path = Path("/")) -> bool:
    """True si `destination` est sur un filesystem différent de `system_root`."""
    return os.stat(destination).st_dev != os.stat(system_root).st_dev


def ensure_separate_volume(destination: Path, system_root: Path = Path("/")) -> None:
    """Lève BackupDestinationError si `destination` n'existe pas ou est sur
    le même volume que `system_root` (§11.1)."""
    if not destination.exists():
        raise BackupDestinationError(f"Destination de sauvegarde introuvable : {destination}")
    if not is_separate_volume(destination, system_root):
        raise BackupDestinationError(
            f"Destination de sauvegarde refusée : {destination} est sur le même "
            f"volume que le disque système ({system_root}) — un volume "
            f"physiquement distinct est requis (§11.1)."
        )
