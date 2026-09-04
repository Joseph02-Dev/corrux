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

from modules.rh.models import Contract, Employee


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


def link_document_to_contract(*, contract: Contract, document_ref: int) -> Contract:
    """Lie un document déjà déposé/sélectionné (via l'Explorateur
    Documentation) à un contrat existant — action séparée de l'édition
    complète (maquette : « Lier un document » comme action dédiée,
    distincte des autres champs du Drawer Contrat)."""
    contract.document_ref = document_ref
    contract.save(update_fields=["document_ref"])
    return contract
