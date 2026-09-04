"""Service CRUD Fiche employé — TECH-031.

Backend pur : aucune vue HTTP, aucune route (même patron que TECH-021/
TECH-030 pour les tickets "Fichiers/modules : modules/rh/" — la couche
HTTP réelle appartient à un futur ticket UI, UI-401+, non anticipée
ici).

Critère d'acceptation explicite : la création ne crée JAMAIS de compte
utilisateur (décision Lot 4 finale #1/#2) — `Employee.user` reste
`None`, jamais renseigné automatiquement ici (déjà garanti nullable par
le modèle, TECH-030).

« Marquer inactif conserve l'historique (contrats, congés, documents) »
(critère d'acceptation explicite) : garanti par construction —
`Contract`/`LeaveRequest`/`EmployeeDocument` utilisent tous `PROTECT`
vers `Employee` (TECH-030), et `deactivate_employee()` ne modifie que
le champ `status`, jamais ces relations.
"""

from __future__ import annotations

from datetime import date

from core.identity.models import User
from modules.documentation.documents_v1 import DocumentMeta, attach, get, list_for_owner
from modules.rh.models import Contract, Employee, EmployeeDocument, LeaveRequest


def create_employee(
    *,
    first_name: str,
    last_name: str,
    position: str,
    hire_date: date,
    email: str = "",
) -> Employee:
    """Crée une fiche employé.

    Ne crée jamais de compte utilisateur — critère d'acceptation
    explicite du ticket. `Employee.user` reste `None` : aucun paramètre
    ne permet de le renseigner ici, aucune association automatique.
    """
    return Employee.objects.create(
        first_name=first_name,
        last_name=last_name,
        email=email,
        position=position,
        hire_date=hire_date,
    )


def update_employee(
    *,
    employee: Employee,
    first_name: str,
    last_name: str,
    position: str,
    hire_date: date,
    status: str,
    email: str = "",
) -> Employee:
    """Modifie les champs éditables d'une fiche employé.

    `status` est requis explicitement (pas de valeur par défaut) :
    évite qu'un appel omettant ce paramètre ne réinitialise
    silencieusement le statut d'un employé déjà désactivé. Ne modifie
    jamais `user` : aucun mécanisme de liaison/déliaison de compte
    n'est prévu par ce ticket.
    """
    employee.first_name = first_name
    employee.last_name = last_name
    employee.email = email
    employee.position = position
    employee.hire_date = hire_date
    employee.status = status
    employee.save(
        update_fields=[
            "first_name",
            "last_name",
            "email",
            "position",
            "hire_date",
            "status",
        ]
    )
    return employee


def deactivate_employee(*, employee: Employee) -> Employee:
    """Marque l'employé comme inactif.

    Conserve l'historique (contrats/congés/documents) — critère
    d'acceptation explicite : modifie uniquement `status`, jamais les
    relations `PROTECT` déjà posées par TECH-030 (aucune suppression,
    aucun détachement).
    """
    employee.status = Employee.Status.INACTIVE
    employee.save(update_fields=["status"])
    return employee


def employee_full_name(employee: Employee) -> str:
    """Combinaison à l'affichage de `first_name`/`last_name`.

    Le « Nom » unique mentionné par TECH-031 et maquettes-ui-v1-lot4.md
    reste une présentation, pas un champ fusionné en base — décision
    documentée dans `modules/rh/models.py`.
    """
    return f"{employee.first_name} {employee.last_name}"


# ============================================================================
# TECH-032 — Gestion des contrats
# ============================================================================
#
# Critère d'acceptation explicite : « aucun stockage fichier propre à
# RH pour les contrats » (§8 architecture). Ce module n'importe jamais
# `core.storage` — `document_ref` est une référence opaque déjà
# obtenue via `modules.documentation.documents_v1.attach()`/`get()`
# (TECH-024), jamais orchestrée ici : le document est sélectionné/
# déposé via l'Explorateur Documentation (maquette Lot 4, Drawer
# Contrat : « Lier un document » ouvre l'Explorateur, pas un champ
# d'upload direct sur ce formulaire).
#
# Dépendance déclarée du ticket (TECH-034) non requise en pratique :
# TECH-032 dépend littéralement de TECH-034 selon le plan, mais l'ordre
# d'exécution recommandé du même document place TECH-032 avant
# TECH-034 — incohérence interne signalée lors de l'audit Phase 1, pas
# ignorée. Résolu sans blocage : le besoin réel de TECH-032
# (document_ref obtenu via documents.v1) est déjà entièrement couvert
# par TECH-024, disponible et testé ; TECH-034 n'ajoutera que la
# liaison côté fiche employé (EmployeeDocument), une capacité que
# TECH-032 n'utilise pas directement.


def create_contract(
    *,
    employee: Employee,
    type: str,
    start_date: date,
    end_date: date | None = None,
    status: str = Contract.Status.ACTIVE,
    document_ref: int | None = None,
) -> Contract:
    """Crée un contrat."""
    return Contract.objects.create(
        employee=employee,
        type=type,
        start_date=start_date,
        end_date=end_date,
        status=status,
        document_ref=document_ref,
    )


def update_contract(
    *,
    contract: Contract,
    type: str,
    start_date: date,
    end_date: date | None,
    status: str,
    document_ref: int | None,
) -> Contract:
    """Modifie un contrat.

    `status` requis explicitement (même justification que
    `update_employee`) : évite qu'un appel omettant ce paramètre ne
    réinitialise silencieusement un statut déjà changé.
    """
    contract.type = type
    contract.start_date = start_date
    contract.end_date = end_date
    contract.status = status
    contract.document_ref = document_ref
    contract.save(
        update_fields=["type", "start_date", "end_date", "status", "document_ref"]
    )
    return contract


def link_document_to_contract(
    *, contract: Contract, document_ref: int, requesting_user: User
) -> Contract:
    """Lie un document déjà déposé/sélectionné (via l'Explorateur
    Documentation) à un contrat existant — action séparée de l'édition
    complète (maquette : « Lier un document » comme action dédiée,
    distincte des autres champs du Drawer Contrat).

    Correction (UI-404, audit Phase 1) : revérifie l'accès via
    `documents_v1.get()` avant de lier — même garde que
    `link_document_to_employee()` (TECH-034). Écart de sécurité réel
    dans la version initiale de cette fonction (TECH-032), qui faisait
    confiance aveuglément au `document_ref` fourni sans jamais vérifier
    que l'appelant y avait seulement accès — surfacé par l'usage réel
    de UI-404 (un utilisateur choisit un document via un sélecteur, il
    faut revérifier qu'il y avait bien accès), corrigé ici plutôt que
    laissé tel quel.
    """
    get(document_ref, requesting_user)
    contract.document_ref = document_ref
    contract.save(update_fields=["document_ref"])
    return contract


# ============================================================================
# TECH-033 — Congés/absences (création, statut, validation/refus)
# ============================================================================
#
# « Solde de jours hors périmètre » (décision Lot 4 finale #3) : aucun
# calcul, aucun champ de solde nulle part dans ce module.
#
# Critère d'acceptation explicite : refus sans commentaire rejeté —
# appliqué côté service (LeaveRequestDecisionError), pas une contrainte
# NOT NULL en base (une validation n'a besoin d'aucun commentaire).


class LeaveRequestDecisionError(Exception):
    """Levée quand une décision de congé est invalide — ex. refus
    tenté sans commentaire (critère d'acceptation explicite)."""


def create_leave_request(
    *,
    employee: Employee,
    type: str,
    start_date: date,
    end_date: date,
    comment: str = "",
) -> LeaveRequest:
    """Crée une demande de congé. Statut par défaut : En attente
    (LeaveRequest.Status.PENDING, TECH-030). Aucun calcul de solde."""
    return LeaveRequest.objects.create(
        employee=employee,
        type=type,
        start_date=start_date,
        end_date=end_date,
        comment=comment,
    )


def approve_leave_request(*, leave_request: LeaveRequest, approver: User) -> LeaveRequest:
    """Approuve une demande — aucun commentaire requis pour une
    approbation (maquette : « Variante A — Validation (confirmation
    simple) », contrairement au refus)."""
    leave_request.status = LeaveRequest.Status.APPROVED
    leave_request.approver_user = approver
    leave_request.save(update_fields=["status", "approver_user"])
    return leave_request


def reject_leave_request(
    *, leave_request: LeaveRequest, approver: User, comment: str
) -> LeaveRequest:
    """Refuse une demande.

    Critère d'acceptation explicite du ticket : un commentaire est
    obligatoire (« refus exige un commentaire du valideur, visible par
    l'employé ») — une chaîne vide ou uniquement des espaces est
    refusée, aucune écriture n'a lieu si la validation échoue
    (LeaveRequestDecisionError levée avant tout .save()).
    """
    if not comment.strip():
        raise LeaveRequestDecisionError(
            "Un commentaire est requis pour refuser une demande de congé."
        )
    leave_request.status = LeaveRequest.Status.REJECTED
    leave_request.approver_user = approver
    leave_request.approver_comment = comment
    leave_request.save(update_fields=["status", "approver_user", "approver_comment"])
    return leave_request


def list_pending_leave_requests() -> list[LeaveRequest]:
    """Toutes les demandes en attente, tous employés confondus — file
    d'attente du valideur (maquette : « Congés à traiter »). Critère
    d'acceptation explicite : « demande visible par le valideur »."""
    return list(
        LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING)
        .select_related("employee")
        .order_by("start_date")
    )


def list_leave_requests_for_employee(employee: Employee) -> list[LeaveRequest]:
    """Toutes les demandes d'un employé donné — vue personnelle
    (maquette : onglet Congés de la fiche employé). Critère
    d'acceptation explicite : « statut visible par l'employé » (le
    champ `status` de chaque demande retournée)."""
    return list(LeaveRequest.objects.filter(employee=employee).order_by("-start_date"))


# ============================================================================
# TECH-034 — Rattachement de documents RH via `documents.v1`
# ============================================================================
#
# Contrainte transverse impérative : RH ne crée jamais son propre
# système documentaire. Tout document RH transite exclusivement par
# `documents_v1.attach()`/`get()`/`list_for_owner()` (TECH-024) —
# jamais un import de `modules.documentation.models`, jamais un accès
# direct à `core.storage` (vérifié par analyse AST, tests dédiés).
#
# `EmployeeDocument` (TECH-030) est la seule table de liaison connue du
# système — Documentation ne la connaît jamais (aucune relation dans
# l'autre sens).
#
# Maquette Lot 4 (onglet Documents de la fiche employé) : « réutilisation
# stricte de l'Explorateur Documentation... sans variante RH ». Le
# dépôt réel passera par l'écran Documentation existant (UI-301/302,
# déjà construit) ; ce service fournit donc à la fois une fonction de
# dépôt+liaison en un seul appel (utile hors contexte HTTP, ex. tests,
# scripts) ET une fonction de liaison seule pour un document déjà
# déposé/sélectionné via l'Explorateur.


def attach_document_to_employee(
    *, employee: Employee, content: bytes, filename: str, owner_user: User, category: str = ""
) -> int:
    """Dépose un nouveau document ET le rattache à l'employé, en un
    seul appel — via `documents_v1.attach()` exclusivement, jamais un
    accès direct au stockage. Retourne le `document_ref` opaque.

    `category` : transmis tel quel à `attach()`, qui le supporte déjà
    (TECH-024) — pas une extension de la frontière documents_v1.
    `description` n'est volontairement pas exposé ici : `attach()` ne
    le supporte pas (construit avant l'ajout de `description` à
    `upload_document()`, UI-302) — plutôt que l'ignorer silencieusement
    si un appelant le fournissait, ce paramètre n'existe simplement pas
    tant que documents_v1 ne l'expose pas lui-même.
    """
    document_ref = attach(
        content=content, filename=filename, owner_user=owner_user, category=category
    )
    EmployeeDocument.objects.get_or_create(employee=employee, document_ref=document_ref)
    return document_ref


def link_document_to_employee(
    *, employee: Employee, document_ref: int, requesting_user: User
) -> EmployeeDocument:
    """Rattache un document déjà existant (déposé ou sélectionné via
    l'Explorateur Documentation, pas déposé ici) à un employé.

    Revérifie l'accès via `documents_v1.get()` avant de créer le lien
    — lève `DocumentNotAccessibleError` si `requesting_user` n'a pas le
    droit de lire ce document, jamais une confiance aveugle dans le
    `document_ref` fourni par l'appelant.
    """
    get(document_ref, requesting_user)
    link, _ = EmployeeDocument.objects.get_or_create(
        employee=employee, document_ref=document_ref
    )
    return link


def list_employee_documents(
    *, employee: Employee, requesting_user: User
) -> list[DocumentMeta]:
    """Documents rattachés à un employé.

    Résout d'abord la table de liaison RH (`employee_documents`), puis
    délègue exclusivement à `documents_v1.list_for_owner()` la
    résolution des métadonnées ET la vérification de permission
    (Option A, TECH-024) — RH ne connaît jamais les métadonnées
    Documentation directement, jamais un accès aux modèles internes.
    """
    document_refs = list(
        EmployeeDocument.objects.filter(employee=employee).values_list(
            "document_ref", flat=True
        )
    )
    return list_for_owner(document_refs, requesting_user)
