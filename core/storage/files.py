"""Primitives génériques de stockage fichiers — TECH-004.

Cf. architecture-technique-v1.md §8 : racine unique de stockage, structure
`<module>/<uuid>/<filename>`. Ce module ne connaît aucune notion métier
(Document, Folder, employé, permissions documentaires, catégories...) :
il sera consommé exclusivement par le module Documentation à partir de
TECH-021, qui portera cette logique métier.

Sécurité : le chemin final est systématiquement canonicalisé (Path.resolve)
et vérifié comme étant contenu dans la racine de stockage avant toute
opération disque — en plus de la validation individuelle de chaque
composant (module, identifiant, nom de fichier). Ne fait jamais confiance
à un simple os.path.join().
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

_MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class StorageError(Exception):
    """Erreur générique de stockage (ex. fichier introuvable en lecture)."""


class InvalidPathError(StorageError):
    """Composant de chemin invalide, ou tentative de sortir du répertoire
    de stockage (path traversal)."""


@dataclass(frozen=True)
class StoredFile:
    """Référence vers un fichier stocké : de quoi le relire ensuite."""

    module: str
    file_id: str
    filename: str


def _storage_root() -> Path:
    """Racine de stockage courante (lue depuis settings à chaque appel,
    pour rester compatible avec `override_settings` en test)."""
    root = Path(settings.CORRUX_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _validate_module(module: str) -> str:
    if not isinstance(module, str) or not _MODULE_NAME_RE.match(module):
        raise InvalidPathError(f"Nom de module invalide : {module!r}")
    return module


def _validate_file_id(file_id: str) -> str:
    try:
        return str(uuid.UUID(str(file_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidPathError(f"Identifiant de fichier invalide : {file_id!r}") from exc


def _validate_filename(filename: str) -> str:
    if not filename or not isinstance(filename, str):
        raise InvalidPathError("Nom de fichier vide ou invalide.")
    if "\x00" in filename:
        raise InvalidPathError("Nom de fichier invalide (octet nul).")
    # Rejette tout séparateur de chemin, quel que soit l'OS d'origine du
    # nom de fichier (ex. dépôt depuis un client Windows).
    if "/" in filename or "\\" in filename:
        raise InvalidPathError(
            f"Nom de fichier invalide (séparateur de chemin) : {filename!r}"
        )
    if filename in (".", ".."):
        raise InvalidPathError(f"Nom de fichier invalide : {filename!r}")
    if Path(filename).is_absolute():
        raise InvalidPathError(f"Nom de fichier invalide (chemin absolu) : {filename!r}")
    return filename


def _resolve_and_verify(root: Path, *parts: str) -> Path:
    """Construit le chemin final et garantit qu'il reste sous `root`.

    Défense en profondeur : même si une validation de champ individuel
    était contournée, cette vérification finale (canonicalisation +
    containment check) empêche toute écriture/lecture hors du répertoire
    de stockage.
    """
    candidate = (root / Path(*parts)).resolve()
    if not candidate.is_relative_to(root):
        raise InvalidPathError("Chemin résolu hors du répertoire de stockage.")
    return candidate


def write(module: str, filename: str, content: bytes) -> StoredFile:
    """Écrit `content` sous `<root>/<module>/<uuid>/<filename>`.

    Un nouvel identifiant UUID est toujours généré ici (jamais fourni par
    l'appelant) : ce point d'entrée ne permet donc pas d'écraser un
    fichier existant.
    """
    module = _validate_module(module)
    filename = _validate_filename(filename)
    file_id = str(uuid.uuid4())

    root = _storage_root()
    directory = _resolve_and_verify(root, module, file_id)
    directory.mkdir(parents=True, exist_ok=False)
    file_path = _resolve_and_verify(root, module, file_id, filename)
    file_path.write_bytes(content)

    return StoredFile(module=module, file_id=file_id, filename=filename)


def read(module: str, file_id: str, filename: str) -> bytes:
    """Lit le contenu précédemment écrit sous `<module>/<file_id>/<filename>`."""
    module = _validate_module(module)
    file_id = _validate_file_id(file_id)
    filename = _validate_filename(filename)

    root = _storage_root()
    file_path = _resolve_and_verify(root, module, file_id, filename)
    if not file_path.is_file():
        raise StorageError(f"Fichier introuvable : {module}/{file_id}/{filename}")
    return file_path.read_bytes()
