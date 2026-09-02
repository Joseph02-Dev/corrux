"""Service de dépôt/organisation de documents — TECH-021.

Backend pur : aucune vue HTTP, aucune route, aucun formulaire, aucun
`request.FILES`. Ces fonctions sont appelées directement (par les
tests, et plus tard par la couche HTTP réelle de UI-301/302 — non
anticipée ici, cf. architecture-technique-v1.md §8 et
plan-implementation-v1.md, section Backend distincte de la section
Interface utilisateur Lot 3).

Réutilise exclusivement `core.storage.files` (TECH-004) pour l'écriture
physique — jamais de chemin manipulé directement, jamais d'accès disque
hors de cette primitive. Aucune vérification RBAC (TECH-023, futur),
aucun événement d'audit (non requis par les sources de ce ticket).

--- Limite d'atomicité stockage/DB (documentée, pas comblée) ---
`storage.write()` (écriture disque) et la création des lignes `Document`/
`DocumentMetadata` (écriture PostgreSQL) ne forment pas une transaction
unique : ce sont deux systèmes distincts. Toutes les validations
possibles (type, taille) sont effectuées AVANT l'écriture disque, pour
minimiser le risque d'un échec après coup. Si l'écriture disque réussit
mais que la transaction DB qui suit échoue malgré tout, le fichier
écrit devient orphelin sur disque — `core.storage.files` ne fournit
aucune fonction `delete()` pour le nettoyer, et TECH-021 n'en ajoute
aucune (hors périmètre, cf. contrat). Cette limite est assumée telle
quelle, pas contournée par un mécanisme de rollback inventé.
"""

from __future__ import annotations

from django.db import transaction

from core.identity.models import User
from core.storage import files as storage
from modules.documentation.models import Document, DocumentMetadata, Folder

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".jpg"}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB, cf. contrat TECH-021

# Dérivé de l'extension validée, jamais d'un mime_type déclaré par
# l'appelant : un mime_type client ne serait pas une preuve du contenu
# réel (contrat §4). L'ensemble fermé de 4 extensions autorisées rend
# une correspondance déterministe suffisante, sans dépendance de
# détection par contenu.
_MIME_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".jpg": "image/jpeg",
}


class DocumentUploadError(Exception):
    """Erreur de validation métier (type ou taille refusés).

    Distincte des erreurs de stockage (core.storage.files.StorageError/
    InvalidPathError), qui remontent telles quelles depuis
    `storage.write()` — aucune hiérarchie d'exceptions supplémentaire
    n'est nécessaire.
    """


def _validate_extension(filename: str) -> str:
    """Retourne l'extension validée (minuscule, avec le point) ou lève
    DocumentUploadError. Aucune détection de contenu — validation par
    extension uniquement, conformément au contrat."""
    suffix = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise DocumentUploadError(
            f"Type de fichier non autorisé : {filename!r} "
            f"(extensions acceptées : {sorted(ALLOWED_EXTENSIONS)})."
        )
    return suffix


def _validate_size(content: bytes) -> None:
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise DocumentUploadError(
            f"Fichier trop volumineux : {len(content)} octets "
            f"(maximum autorisé : {MAX_FILE_SIZE_BYTES} octets)."
        )


def upload_document(
    *,
    content: bytes,
    filename: str,
    owner_user: User,
    folder: Folder | None = None,
    category: str = "",
) -> Document:
    """Dépose un document.

    1. Valide le type (extension) et la taille — AVANT toute écriture,
       aucune écriture disque ni base n'a lieu si la validation échoue
       (DocumentUploadError levée).
    2. Écrit le contenu via `core.storage.files.write("documentation",
       filename, content)` (TECH-004, seule primitive de stockage,
       jamais contournée).
    3. Crée le `Document` (`storage_path` = `file_id` retourné par
       `write()`, jamais un chemin physique) et, si `category` est
       fournie, la `DocumentMetadata` associée (`key="categorie"`) —
       ces deux écritures dans une même transaction DB.

    `folder=None` : document déposé à la racine.
    """
    extension = _validate_extension(filename)
    _validate_size(content)

    stored_file = storage.write("documentation", filename, content)

    with transaction.atomic():
        document = Document.objects.create(
            owner_user=owner_user,
            folder=folder,
            filename=stored_file.filename,
            mime_type=_MIME_TYPE_BY_EXTENSION[extension],
            size_bytes=len(content),
            storage_path=stored_file.file_id,
        )
        if category:
            DocumentMetadata.objects.create(
                document=document, key="categorie", value=category
            )

    return document


def create_folder(*, name: str, parent: Folder | None = None) -> Folder:
    """Crée un dossier. `parent=None` : dossier racine."""
    return Folder.objects.create(name=name, parent_folder=parent)


def list_folder_contents(folder: Folder | None) -> tuple[list[Document], list[Folder]]:
    """Documents et sous-dossiers directement contenus dans `folder`
    (`None` = racine). Navigation minimale requise par TECH-021 — pas
    une API de recherche documentaire (TECH-022, hors périmètre)."""
    documents = list(Document.objects.filter(folder=folder).order_by("filename"))
    subfolders = list(Folder.objects.filter(parent_folder=folder).order_by("name"))
    return documents, subfolders
