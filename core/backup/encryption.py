"""Chiffrement de la sauvegarde finale — §11.

GPG asymétrique (clé publique du technicien) : le service de sauvegarde
ne manipule jamais de clé privée — il chiffre uniquement avec une clé
publique déjà présente sur le système. La génération et la conservation
de cette clé sont une tâche de `corrux-setup` (TECH-012, hors périmètre
de ce ticket) : voir le point signalé dans le rapport TECH-009 sur ce
sujet. TECH-009 se contente de *consommer* un chemin de clé fourni par
configuration (`CORRUX_BACKUP_GPG_RECIPIENT_KEY_PATH`, même convention
que le reste du projet) — aucun nouveau système de gestion de clé créé.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class BackupEncryptionError(Exception):
    """Le chiffrement de la sauvegarde a échoué. Ne contient jamais de clé."""


def encrypt_file(
    source: Path,
    destination: Path,
    recipient_key_path: Path,
    *,
    runner=subprocess.run,
) -> Path:
    """Chiffre `source` vers `destination` avec la clé publique GPG
    `recipient_key_path` (encryption directe au fichier de clé, sans
    importer celle-ci dans un trousseau système)."""
    if not recipient_key_path.exists():
        raise BackupEncryptionError(f"Clé de chiffrement introuvable : {recipient_key_path}")

    command = [
        "gpg",
        "--batch",
        "--yes",
        "--trust-model", "always",
        "--recipient-file", str(recipient_key_path),
        "--output", str(destination),
        "--encrypt", str(source),
    ]
    result = runner(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        raise BackupEncryptionError(
            f"Chiffrement échoué (code {result.returncode}) : {result.stderr.strip()}"
        )
    return destination
