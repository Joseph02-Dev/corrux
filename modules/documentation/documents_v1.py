"""Interface interne `documents.v1` — TECH-024.

Frontière stable et versionnée, exposée par Documentation, consommée
par tout autre module (RH en premier lieu, TECH-034, futur) — conforme
à architecture-technique-v1.md §15 : « aucun import direct des modèles
internes du module fournisseur », uniquement via ces fonctions.

--- Décision Phase 2 (résolution de blocage architectural, Option A) ---
`list_for_owner()` ne résout PAS elle-même une association
`(owner_module, owner_ref) -> document_ref` : Documentation ne possède
et ne doit jamais posséder cette association — elle appartient
exclusivement au module consommateur (ex. RH via
`employee_documents(employee_id, document_ref)`, §7, "table de liaison
pure"). Le module appelant résout d'abord cette association dans son
propre schéma, puis fournit à `list_for_owner()` la liste de
`document_ref` qu'il a déjà obtenue. La sémantique de la méthode change
en conséquence par rapport à l'exemple illustratif de §15 (le texte
source le qualifie lui-même d'« exemples de méthodes », pas une
signature imposée).

Aucun accès filesystem ici (jamais `storage.read()`/`storage_path`
exposé), aucune duplication de la logique de permission (délègue
entièrement à `has_document_permission()`, TECH-023), aucun nouvel
audit (`attach()` n'hérite d'aucun audit puisque `upload_document()`
lui-même n'en émet pas — TECH-021), aucune migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from core.identity.models import User
from modules.documentation.models import Document, Folder
from modules.documentation.services import has_document_permission, upload_document


class DocumentNotAccessibleError(Exception):
    """Levée aussi bien pour une référence inexistante que pour une
    référence existante mais interdite.

    Non-divulgation volontaire d'existence (décision Phase 2) : le
    consommateur ne doit jamais pouvoir distinguer les deux cas à
    partir du comportement de la façade. Ne jamais exposer
    `Document.DoesNotExist`, un détail ORM/SQL, ni `storage_path`.
    """


@dataclass(frozen=True)
class DocumentMeta:
    """Type de données stable de la façade — jamais le modèle Django
    `Document` retourné directement.

    Volontairement minimal (décision Phase 2, principe V1 de
    minimisation des données exposées) : ni `storage_path`, ni
    `owner_user`, ni `folder`.
    """

    document_ref: int
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime


def _to_document_meta(document: Document) -> DocumentMeta:
    return DocumentMeta(
        document_ref=document.id,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        created_at=document.created_at,
    )


def attach(
    *,
    content: bytes,
    filename: str,
    owner_user: User,
    folder: Folder | None = None,
    category: str = "",
) -> int:
    """Dépose un document, retourne son `document_ref` (`Document.id`).

    Adapte l'appel à `upload_document()` (TECH-021) — aucune validation
    de type/taille, écriture storage ou transaction Document/Metadata
    n'est réimplémentée ici. `owner_module`/`owner_ref` ne sont pas des
    paramètres : Documentation ne les persiste jamais (décision Phase 2)
    — le consommateur conserve seul sa propre association métier.

    Toute `DocumentUploadError` (type/taille refusés, TECH-021) ou
    erreur de stockage (`core.storage.files`) remonte telle quelle,
    sans être enveloppée.
    """
    document = upload_document(
        content=content,
        filename=filename,
        owner_user=owner_user,
        folder=folder,
        category=category,
    )
    return document.id


def get(document_ref: int, requesting_user: User) -> DocumentMeta:
    """Retourne les métadonnées d'un document si `requesting_user` a le
    droit de le lire — sinon lève `DocumentNotAccessibleError`, que la
    référence soit inexistante ou simplement interdite (non-divulgation,
    décision Phase 2).

    Réutilise entièrement `has_document_permission()` (TECH-023) :
    aucune logique propriétaire/rôle/utilisateur/révocation dupliquée
    ici. L'état de `DocumentPermission` étant relu à chaque appel (pas
    de cache), une révocation est immédiatement effective.
    """
    try:
        document = Document.objects.get(pk=document_ref)
    except Document.DoesNotExist as exc:
        raise DocumentNotAccessibleError(document_ref) from exc

    if not has_document_permission(requesting_user, document, "read"):
        raise DocumentNotAccessibleError(document_ref)

    return _to_document_meta(document)


def list_for_owner(
    document_refs: Sequence[int], requesting_user: User
) -> list[DocumentMeta]:
    """Retourne les métadonnées des documents de `document_refs`
    accessibles à `requesting_user`.

    Toute référence inexistante ou interdite est silencieusement omise
    du résultat — même politique de non-divulgation que `get()`, même
    comportement que `search_documents()` (TECH-022) : jamais d'erreur
    distincte pour un document interdit, jamais de document interdit
    exposé dans une étape intermédiaire.

    Sémantique (décision Phase 2, Option A) : `document_refs` provient
    de la résolution, par le module APPELANT, de sa propre association
    propriétaire -> documents (ex. RH : `employee_documents`). Cette
    fonction ne résout aucune association elle-même — Documentation ne
    connaît et ne stocke aucune notion de « propriétaire » inter-module.
    """
    results = []
    for ref in document_refs:
        try:
            results.append(get(ref, requesting_user))
        except DocumentNotAccessibleError:
            continue
    return results
