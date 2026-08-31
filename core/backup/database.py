"""Sauvegarde de la base PostgreSQL via `pg_dump` — §11.

Construction sûre de la commande : arguments toujours passés en liste
(jamais `shell=True`, jamais d'interpolation de chaîne) — aucune surface
d'injection de commande possible. Le mot de passe transite exclusivement
par la variable d'environnement `PGPASSWORD` (convention standard
PostgreSQL), jamais en ligne de commande ni dans un message d'erreur.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class DatabaseBackupError(Exception):
    """pg_dump a échoué. Ne contient jamais le mot de passe."""


@dataclass(frozen=True)
class DatabaseConnectionParams:
    name: str
    user: str
    password: str
    host: str
    port: str


def dump_database(
    params: DatabaseConnectionParams,
    destination: Path,
    *,
    runner=subprocess.run,
) -> Path:
    """Exécute `pg_dump` (format personnalisé, tous schémas) vers `destination`.

    `runner` est injectable (tests) ; par défaut `subprocess.run` réel.
    Lève DatabaseBackupError si pg_dump échoue — aucun fichier de sortie
    n'est alors considéré valide.
    """
    command = [
        "pg_dump",
        "--host", params.host,
        "--port", str(params.port),
        "--username", params.user,
        "--no-password",
        "--format=custom",
        "--file", str(destination),
        params.name,
    ]
    env = {**os.environ, "PGPASSWORD": params.password}
    result = runner(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Le message d'erreur pg_dump natif ne contient jamais le mot de
        # passe lui-même (seulement, le cas échéant, le nom d'utilisateur).
        raise DatabaseBackupError(
            f"pg_dump a échoué (code {result.returncode}) : {result.stderr.strip()}"
        )
    return destination
