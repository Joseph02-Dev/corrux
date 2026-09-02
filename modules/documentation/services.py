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

from datetime import datetime

from django.db import transaction
from django.db.models import Q

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


# ============================================================================
# Recherche documentaire — TECH-022
# ============================================================================
#
# Backend pur, lecture seule : aucun accès filesystem (jamais
# storage.read()/Document.storage_path comme chemin), aucun audit,
# aucune modification de core.authz. Le filtrage par permission est
# appliqué DANS la requête ORM (jamais après coup, jamais un filtrage
# côté appelant) — conforme au critère d'acceptation explicite du
# ticket : « un document hors permission n'apparaît jamais, même par
# mot-clé exact ».
#
# Portée du filtrage par permission (décision Phase 2, documentée, pas
# silencieuse) : DocumentPermission ciblant DIRECTEMENT le document
# recherché (action="read", pour un rôle de l'utilisateur ou pour
# l'utilisateur individuellement) + accès implicite du propriétaire
# (Document.owner_user — confirmé par maquettes-ui-v1-lot3.md §4 :
# « liste vide -> au moins un accès (le propriétaire) »). Aucun
# héritage depuis une permission portée par le dossier parent n'est
# appliqué : aucune source consultée ne le confirme, et le contrat
# interdit explicitement d'anticiper une règle de TECH-023 au-delà du
# strict nécessaire. TECH-022 ne crée aucun nouveau moteur RBAC objet,
# ne modifie pas core/authz/engine.py, et core.authz.has_permission()
# n'est pas utilisé ici (il n'a aucune notion d'objet spécifique — seule
# DocumentPermission, propre au schéma documentation, le permet).

_NO_FOLDER_FILTER = object()  # sentinel : distinct de None (= racine)


def search_documents(
    *,
    user: User,
    keyword: str = "",
    mime_type: str = "",
    folder: Folder | None = _NO_FOLDER_FILTER,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[Document]:
    """Recherche filtrable par mot-clé/type/dossier/date, résultats
    toujours filtrés par permission (jamais de document interdit dans
    le résultat, quel que soit le critère de recherche).

    - `keyword` : correspondance partielle, insensible à la casse, sur
      `Document.filename` OU `DocumentMetadata.value` (un même mot-clé
      retrouve un document par son nom ou par une métadonnée).
    - `mime_type` : correspondance exacte.
    - `folder` : paramètre absent (défaut) = aucun filtre de dossier ;
      `None` = uniquement les documents à la racine ; `Folder` =
      documents directement dans ce dossier — jamais récursif dans les
      sous-dossiers (décision Phase 2, limite le périmètre de ce
      ticket).
    - `created_after`/`created_before` : bornes sur `Document.created_at`.

    Tous les filtres fournis sont combinés par ET. Résultat dédupliqué
    (`distinct()`) et trié par nom de fichier (ordre déterministe le
    plus simple, aucune convention de tri n'existe pour Document).
    """
    role_ids = user.user_roles.values_list("role_id", flat=True)

    permission_filter = (
        Q(owner_user=user)
        | Q(permissions__action="read", permissions__role_id__in=role_ids)
        | Q(permissions__action="read", permissions__user=user)
    )
    queryset = Document.objects.filter(permission_filter)

    if keyword:
        queryset = queryset.filter(
            Q(filename__icontains=keyword) | Q(metadata_entries__value__icontains=keyword)
        )

    if mime_type:
        queryset = queryset.filter(mime_type=mime_type)

    if folder is not _NO_FOLDER_FILTER:
        queryset = queryset.filter(folder=folder)

    if created_after is not None:
        queryset = queryset.filter(created_at__gte=created_after)

    if created_before is not None:
        queryset = queryset.filter(created_at__lte=created_before)

    return list(queryset.distinct().order_by("filename"))
