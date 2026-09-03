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

from core.audit.service import record_audit_event
from core.authz.models import Role
from core.authz.object_permissions import has_object_permission
from core.identity.models import User
from core.storage import files as storage
from modules.documentation.models import Document, DocumentMetadata, DocumentPermission, Folder

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
    description: str = "",
) -> Document:
    """Dépose un document.

    1. Valide le type (extension) et la taille — AVANT toute écriture,
       aucune écriture disque ni base n'a lieu si la validation échoue
       (DocumentUploadError levée).
    2. Écrit le contenu via `core.storage.files.write("documentation",
       filename, content)` (TECH-004, seule primitive de stockage,
       jamais contournée).
    3. Crée le `Document` (`storage_path` = `file_id` retourné par
       `write()`, jamais un chemin physique) et, si `category`/
       `description` sont fournies, la `DocumentMetadata` associée
       (`key="categorie"`/`key="description"`) — ces écritures dans une
       même transaction DB.

    `folder=None` : document déposé à la racine.

    `description` (UI-302) : extension mineure suivant exactement le
    même patron que `category` (une DocumentMetadata de plus, pas un
    nouveau mécanisme) — le champ "Description" est explicitement listé
    par la maquette Lot 3 §2 du formulaire de dépôt, absent du modèle
    `Document` lui-même (TECH-020), donc porté par la même table
    extensible que `category`.
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
        if description:
            DocumentMetadata.objects.create(
                document=document, key="description", value=description
            )

    return document


def create_folder(*, name: str, parent: Folder | None = None) -> Folder:
    """Crée un dossier. `parent=None` : dossier racine."""
    return Folder.objects.create(name=name, parent_folder=parent)


def _upsert_document_metadata(document: Document, key: str, value: str) -> None:
    if value:
        DocumentMetadata.objects.update_or_create(
            document=document, key=key, defaults={"value": value}
        )
    else:
        DocumentMetadata.objects.filter(document=document, key=key).delete()


def document_metadata_value(document: Document, key: str) -> str:
    """Valeur d'une métadonnée (`categorie`/`description`) ou chaîne
    vide si absente — UI-303."""
    entry = document.metadata_entries.filter(key=key).first()
    return entry.value if entry else ""


def update_document(
    *,
    document: Document,
    folder: Folder | None,
    category: str = "",
    description: str = "",
) -> Document:
    """Modifie les métadonnées d'un document — dossier, catégorie,
    description — UI-303.

    Ne modifie JAMAIS `filename`/`storage_path`/`mime_type`/
    `size_bytes`/`owner_user` : le nom de fichier est indissociable du
    chemin physique réel — `core.storage.files.write()` construit le
    chemin de stockage à partir du `filename` exact (TECH-004,
    `<root>/<module>/<uuid>/<filename>`). Le renommer en base sans
    renommer le fichier casserait sa lecture ultérieure
    (`storage.read()` recevrait un `filename` ne correspondant plus au
    fichier réel sur disque), et `core.storage.files` ne fournit aucune
    primitive de renommage. Limite documentée, pas contournée : le nom
    reste volontairement non éditable dans ce ticket (contrairement à
    la maquette, qui le liste comme champ éditable — écart signalé,
    pas silencieux).

    `folder` : déplacement pur en base — le chemin de stockage physique
    n'est jamais indexé par `Folder`, aucun impact sur le fichier.
    """
    with transaction.atomic():
        document.folder = folder
        document.save(update_fields=["folder"])
        _upsert_document_metadata(document, "categorie", category)
        _upsert_document_metadata(document, "description", description)

    return document


def list_folder_contents(folder: Folder | None) -> tuple[list[Document], list[Folder]]:
    """Documents et sous-dossiers directement contenus dans `folder`
    (`None` = racine). Navigation minimale requise par TECH-021 — pas
    une API de recherche documentaire (TECH-022, hors périmètre)."""
    documents = list(Document.objects.filter(folder=folder).order_by("filename"))
    subfolders = list(Folder.objects.filter(parent_folder=folder).order_by("name"))
    return documents, subfolders


def list_visible_folder_contents(
    folder: Folder | None, user: User
) -> tuple[list[Document], list[Folder]]:
    """Comme `list_folder_contents()`, filtré par permission de lecture
    — UI-301, critère d'acceptation : « un document sans permission
    n'apparaît pas ».

    Réutilise directement `has_document_permission()`/
    `has_folder_permission()` (TECH-023), pas une nouvelle règle : le
    filtrage porte sur les enfants DIRECTS d'un seul dossier (borné,
    typiquement quelques éléments pour une PME/TPE), un filtrage Python
    par appel unitaire reste donc approprié ici — contrairement à
    `search_documents()` qui doit filtrer sur l'ensemble du corpus et
    justifie à ce titre sa propre requête ORM en masse
    (`_document_read_permission_filter`)."""
    documents, subfolders = list_folder_contents(folder)
    visible_documents = [d for d in documents if has_document_permission(user, d, "read")]
    visible_subfolders = [f for f in subfolders if has_folder_permission(user, f, "read")]
    return visible_documents, visible_subfolders


def folder_breadcrumb(folder: Folder | None) -> list[Folder]:
    """Chemin de la racine jusqu'à `folder` inclus (liste vide = racine)
    — UI-301, `Folder.parent_folder` déjà posé par TECH-020."""
    path = []
    current = folder
    while current is not None:
        path.append(current)
        current = current.parent_folder
    return list(reversed(path))


# ============================================================================
# Permissions par document/dossier, intégration à authz — TECH-023
# ============================================================================
#
# core.authz.object_permissions.has_object_permission() (générique, ne
# connaît aucun modèle métier) devient la primitive officielle pour une
# décision unitaire. Pour le filtrage en masse (search_documents), une
# clause WHERE ORM reste nécessaire (has_object_permission() ne peut pas
# être composé efficacement dans une requête portant sur de nombreux
# documents sans provoquer un N+1) — _document_read_permission_filter()
# exprime EXACTEMENT la même règle que has_document_permission(...,
# "read"), nommée et documentée une seule fois, plutôt que la
# duplication non nommée qu'avait TECH-022. Les deux représentations
# sont nécessaires pour des raisons de performance (décision unitaire vs
# filtrage en masse), mais expriment une seule et même règle.
#
# Règle (décision Phase 2, documentée, pas inventée silencieusement) :
# - propriétaire -> accès "read" implicite uniquement (aucune source ne
#   confirme un accès "write" implicite par simple propriété) ;
# - sinon, DocumentPermission ciblant DIRECTEMENT l'objet (rôle ou
#   utilisateur, modèle additif pur, aucun deny, aucune priorité) ;
# - aucun héritage dossier -> document, aucune propagation vers les
#   sous-dossiers (aucune source ne le confirme) ;
# - Folder n'a pas de champ propriétaire (vérifié) : aucune règle de
#   propriétaire de dossier.


def _document_read_permission_filter(user: User) -> Q:
    """Filtre ORM (bulk) exprimant la même règle que
    `has_document_permission(user, doc, "read")`, nécessaire pour
    `search_documents()` (éviter un N+1).

    Garde de statut explicite (bug corrigé : cette fonction affirmait
    exprimer « exactement la même règle » que `has_document_permission`
    sans réellement appliquer sa garde `user.status == ACTIVE` — un
    utilisateur inactif obtenait donc, en pratique, aucun résultat via
    le raccourci propriétaire de la version unitaire mais PAS via cette
    version en masse, un vrai risque en cas d'appel hors du flux HTTP
    normal, où le middleware ne filtre pas déjà les utilisateurs
    inactifs)."""
    if user is None or user.status != User.Status.ACTIVE:
        return Q(pk__in=[])  # aucun document ne correspond jamais
    role_ids = user.user_roles.values_list("role_id", flat=True)
    return (
        Q(owner_user=user)
        | Q(permissions__action="read", permissions__role_id__in=role_ids)
        | Q(permissions__action="read", permissions__user=user)
    )


def has_document_permission(user: User, document: Document, action: str) -> bool:
    """Décision d'autorisation officielle pour un document précis.

    Propriétaire : accès implicite pour `action="read"` uniquement.
    Sinon, délègue entièrement à la primitive générique core.authz
    (aucun moteur RBAC concurrent créé ici).

    Vérifie explicitement le statut actif de `user` avant tout raccourci
    propriétaire — le raccourci ne passe pas par `has_object_permission()`
    (qui l'applique déjà), donc cette garde doit être répétée ici pour
    rester réellement équivalente à la primitive générique."""
    if user is None or user.status != User.Status.ACTIVE:
        return False
    if action == "read" and document.owner_user_id == user.id:
        return True
    return has_object_permission(user, document.permissions.all(), action)


def has_folder_permission(user: User, folder: Folder, action: str) -> bool:
    """Décision d'autorisation officielle pour un dossier précis.

    Aucune règle de propriétaire implicite (Folder n'a pas de champ
    propriétaire). Uniquement DocumentPermission directe sur ce
    dossier — aucune propagation vers son contenu ni depuis un dossier
    parent.

    Garde de statut explicite ici aussi (redondante avec
    `has_object_permission()` à ce jour, mais garde le point d'entrée
    robuste si cette fonction évolue un jour vers un raccourci qui ne
    passerait plus systématiquement par la primitive générique — même
    logique que `has_document_permission`)."""
    if user is None or user.status != User.Status.ACTIVE:
        return False
    return has_object_permission(user, folder.permissions.all(), action)


def _permission_grantee_label(permission: DocumentPermission) -> str:
    if permission.user_id:
        return f"user:{permission.user.username}"
    return f"role:{permission.role.name}"


def _permission_target_label(document: Document | None, folder: Folder | None) -> str:
    if document is not None:
        return f"document:{document.id}"
    return f"folder:{folder.id}"


def grant_permission(
    *,
    actor: User,
    action: str,
    document: Document | None = None,
    folder: Folder | None = None,
    user: User | None = None,
    role: Role | None = None,
) -> DocumentPermission:
    """Attribue une permission sur un document ou un dossier, à un
    utilisateur ou à un rôle — exactement une cible et un bénéficiaire
    (contrainte déjà portée par le modèle, TECH-020, CheckConstraint).

    `actor` : utilisateur qui effectue l'attribution — utilisé
    uniquement pour l'audit, jamais pour une vérification RBAC. Aucune
    règle déterminant qui est autorisé à modifier une permission n'a
    été identifiée dans les sources (limite signalée, pas inventée).

    Idempotent (get_or_create) : aucun doublon inutile ; l'audit
    (`documentation.permission_grant`) n'est émis que si une ligne a
    réellement été créée — cohérent avec le comportement déjà établi de
    `activate_module()`/`deactivate_module()` (TECH-006), qui n'auditent
    pas un no-op.
    """
    with transaction.atomic():
        permission, created = DocumentPermission.objects.get_or_create(
            document=document, folder=folder, user=user, role=role, action=action,
        )
        if created:
            record_audit_event(
                actor=actor,
                action="documentation.permission_grant",
                target=_permission_target_label(document, folder),
                metadata={"action": action, "grantee": _permission_grantee_label(permission)},
            )
    return permission


def revoke_permission(*, actor: User, permission: DocumentPermission) -> None:
    """Retire une permission — suppression de la ligne correspondante.

    Modèle strictement additif : aucun mécanisme de deny, le retrait
    est une suppression, jamais un refus explicite ajouté. Après
    retrait, une nouvelle vérification (has_document_permission/
    has_folder_permission) recalcule l'état actuel de
    DocumentPermission — aucune valeur n'est mise en cache.
    """
    target = _permission_target_label(permission.document, permission.folder)
    metadata = {"action": permission.action, "grantee": _permission_grantee_label(permission)}
    with transaction.atomic():
        permission.delete()
        record_audit_event(
            actor=actor,
            action="documentation.permission_revoke",
            target=target,
            metadata=metadata,
        )


def list_permissions_for(
    *, document: Document | None = None, folder: Folder | None = None
) -> list[DocumentPermission]:
    """Toutes les DocumentPermission portant directement sur ce document
    OU ce dossier — UI-304. Exactement un des deux doit être fourni
    (même contrainte que le modèle, TECH-020)."""
    if document is not None:
        return list(
            DocumentPermission.objects.filter(document=document).select_related("role", "user")
        )
    return list(
        DocumentPermission.objects.filter(folder=folder).select_related("role", "user")
    )

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

    Permission (TECH-023) : `_document_read_permission_filter()` est la
    même règle, nommée une seule fois, que `has_document_permission(...,
    "read")` — plus de duplication indépendante de la logique de
    permission (dette explicitement introduite par TECH-022, corrigée
    ici).
    """
    queryset = Document.objects.filter(_document_read_permission_filter(user))

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
