"""Module Manager — cycle installation/activation — TECH-006.

Cf. architecture-technique-v1.md §12, §13, §14. Réutilise strictement les
modèles (TECH-005), le parser de manifeste (TECH-005) et le moteur RBAC
(TECH-003) existants — ne les réimplémente ni ne les modifie.

--- Résolution d'ambiguïté (documentée, pas inventée silencieusement) ---
§12 (« Cycle d'installation ») place textuellement la vérification des
dépendances à l'étape d'installation. Mais :
  - le critère d'acceptation produit (vision-produit-v1.md §8) ne teste
    que le blocage à l'ACTIVATION : « tenter d'activer le module RH sans
    Documentation... échoue » (jamais « installer... échoue ») ;
  - le scénario obligatoire de ce ticket exige que « Documentation
    absent » soit un état atteignable pour ensuite tenter
    activate_module("rh") — ce qui suppose que RH ait pu être installé
    alors même qu'aucune ligne Documentation n'existe encore en base ;
  - bloquer l'installation sur l'état d'un module tiers rendrait ce
    scénario irréalisable, et casserait la contrainte de clé étrangère
    de `core.module_dependencies` si elle était peuplée à l'installation
    (le module dépendant pourrait ne pas exister du tout).
Décision retenue : la vérification stricte « installé ET activé » est
appliquée uniquement à l'ACTIVATION (§12 étape 4 : « identique côté
dépendances », qui revérifie). Les lignes `core.module_dependencies`
(dénormalisées, §7) ne sont donc créées qu'au moment où l'activation
réussit — moment où le module dépendant est garanti d'exister et actif,
ce qui rend la clé étrangère toujours satisfiable.

--- Routes ---
Aucune infrastructure de montage dynamique de routes n'existe encore dans
le repository : `modules/documentation` et `modules/rh` restent des
squelettes d'app Django vides (INIT-001), sans `urls.py`. Il n'y a donc
rien de réel à monter. Cette limite est documentée ici plutôt que
comblée par un mécanisme inventé ; le montage deviendra pertinent à
partir des tickets qui donnent réellement une couche HTTP aux modules
métier (TECH-021+, TECH-031+).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction

from core.audit.service import record_audit_event
from core.authz.models import Permission
from core.identity.models import User
from core.modules.manifest import ParsedManifest
from core.modules.models import Module, ModuleDependency

_VERSION_TUPLE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_CONSTRAINT_RE = re.compile(r"^(>=|<=|==|~=|>|<)?(\d+\.\d+\.\d+)$")


class ModuleManagerError(Exception):
    """Erreur générique du Module Manager."""


@dataclass(frozen=True)
class UnsatisfiedDependency:
    module: str
    version_constraint: str
    reason: str


class DependencyError(ModuleManagerError):
    """Activation refusée : au moins une dépendance n'est pas satisfaite.

    Le message liste explicitement chaque dépendance manquante et sa
    raison (absente / installée mais inactive / version incompatible).
    """

    def __init__(self, module_id: str, unsatisfied: list[UnsatisfiedDependency]):
        self.module_id = module_id
        self.unsatisfied = unsatisfied
        details = "; ".join(f"{d.module} ({d.reason})" for d in unsatisfied)
        super().__init__(
            f"Activation de « {module_id} » refusée : "
            f"dépendance(s) non satisfaite(s) — {details}"
        )


class ActiveDependentError(ModuleManagerError):
    """Désactivation refusée : au moins un module dépendant est actif — TECH-007, §14.

    Le message liste explicitement chaque module dépendant actuellement
    actif, exploitable par la couche appelante.
    """

    def __init__(self, module_id: str, active_dependents: list[str]):
        self.module_id = module_id
        self.active_dependents = active_dependents
        details = ", ".join(active_dependents)
        super().__init__(
            f"Désactivation de « {module_id} » refusée : "
            f"module(s) dépendant(s) actuellement actif(s) — {details}"
        )


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = _VERSION_TUPLE_RE.match(version)
    if not match:
        raise ModuleManagerError(f"Version mal formée : {version!r}")
    a, b, c = match.groups()
    return int(a), int(b), int(c)


def version_satisfies(installed_version: str, constraint: str) -> bool:
    """True si `installed_version` satisfait `constraint` (ex. ">=1.0.0").

    Réutilise le format déjà validé par le parser de TECH-005 ; ajoute la
    logique de comparaison sémantique (absente de TECH-005, qui ne fait
    que valider le format) — nécessaire aux critères explicites de ce
    ticket (version compatible/incompatible).
    """
    match = _CONSTRAINT_RE.match(constraint)
    if not match:
        raise ModuleManagerError(f"Contrainte de version mal formée : {constraint!r}")
    operator, required_str = match.group(1), match.group(2)
    installed = _version_tuple(installed_version)
    required = _version_tuple(required_str)

    if operator in (None, "=="):
        return installed == required
    if operator == ">=":
        return installed >= required
    if operator == "<=":
        return installed <= required
    if operator == ">":
        return installed > required
    if operator == "<":
        return installed < required
    if operator == "~=":
        return installed[:2] == required[:2] and installed[2] >= required[2]
    raise ModuleManagerError(f"Opérateur de contrainte inconnu : {operator!r}")  # pragma: no cover


def _declared_dependencies(module: Module) -> list[dict]:
    """Dépendances déclarées, lues depuis le snapshot du manifeste stocké
    à l'installation (source de vérité, §13) — jamais recodées en dur."""
    return module.manifest_snapshot.get("depends_on", []) or []


def check_dependencies(module: Module, *, for_update: bool = False) -> list[UnsatisfiedDependency]:
    """Vérifie les dépendances déclarées de `module` contre l'état actuel
    en base. Ne modifie rien (fonction de lecture pure). `for_update`
    verrouille les lignes lues (à utiliser uniquement depuis un bloc
    transactionnel, pour la revérification avant écriture)."""
    unsatisfied: list[UnsatisfiedDependency] = []
    for dep in _declared_dependencies(module):
        dep_id = dep["module"]
        constraint = dep["version"]
        queryset = Module.objects.filter(pk=dep_id)
        if for_update:
            queryset = queryset.select_for_update()
        target = queryset.first()

        if target is None:
            unsatisfied.append(UnsatisfiedDependency(dep_id, constraint, "absent"))
        elif target.state != Module.State.ACTIVATED:
            reason = f"non activé (état actuel : {target.get_state_display().lower()})"
            unsatisfied.append(UnsatisfiedDependency(dep_id, constraint, reason))
        elif not version_satisfies(target.version, constraint):
            reason = (
                f"version incompatible (installée : {target.version}, requise : {constraint})"
            )
            unsatisfied.append(UnsatisfiedDependency(dep_id, constraint, reason))
    return unsatisfied


def _active_dependent_ids(module: Module, *, for_update: bool = False) -> list[str]:
    """Identifiants des modules actuellement ACTIFS qui déclarent dépendre
    de `module`, via core.module_dependencies (§7) — cette table n'est
    peuplée qu'à l'activation réussie du dépendant (TECH-006), donc son
    seul contenu suffit à retrouver qui dépend réellement de `module`.

    Verrouille les lignes Module des dépendants (pas une jointure) — même
    logique que `check_dependencies(for_update=True)` : verrouiller la
    ressource dont l'état conditionne la décision, pas une jointure.
    """
    dependent_ids = list(
        ModuleDependency.objects.filter(depends_on_module=module).values_list(
            "module_id", flat=True
        )
    )
    if not dependent_ids:
        return []
    queryset = Module.objects.filter(pk__in=dependent_ids, state=Module.State.ACTIVATED)
    if for_update:
        queryset = queryset.select_for_update()
    return list(queryset.values_list("pk", flat=True))


def deactivate_module(module_id: str, actor: User) -> Module:
    """Désactive un module — §12 étape 5, §14 (règle inverse de l'activation).

    Refuse si au moins un module dépendant est actuellement ACTIVATED
    (ActiveDependentError, liste explicite). Idempotent : désactiver un
    module déjà non actif (installed ou deactivated) est un no-op réussi.
    Ne supprime jamais rien : données, permissions et lignes
    ModuleDependency restent intactes (§12 : « les données et le schéma
    DB du module restent intacts »).
    """
    module = Module.objects.filter(pk=module_id).first()
    if module is None:
        raise ModuleManagerError(f"Module non installé : {module_id!r}")

    if module.state != Module.State.ACTIVATED:
        return module

    active_dependents = _active_dependent_ids(module)
    if active_dependents:
        record_audit_event(
            actor=actor,
            action="module.deactivate",
            target=module_id,
            metadata={"status": "denied", "active_dependents": active_dependents},
        )
        raise ActiveDependentError(module_id, active_dependents)

    with transaction.atomic():
        # Reverrouille et revérifie sous transaction (protection TOCTOU),
        # même logique que activate_module().
        module = Module.objects.select_for_update().get(pk=module_id)
        if module.state != Module.State.ACTIVATED:
            return module

        active_dependents = _active_dependent_ids(module, for_update=True)
        if active_dependents:
            raise ActiveDependentError(module_id, active_dependents)

        module.state = Module.State.DEACTIVATED
        module.save(update_fields=["state"])

        record_audit_event(
            actor=actor,
            action="module.deactivate",
            target=module_id,
            metadata={"status": "success"},
        )

    return module


@transaction.atomic
def install_module(parsed_manifest: ParsedManifest, actor: User) -> Module:
    """Installe (ou met à jour) un module — §12 étape 3.

    Enregistre le module et ses permissions déclarées. Ne vérifie PAS les
    dépendances des tiers (voir note de résolution d'ambiguïté en tête de
    fichier) : seule l'ACTIVATION vérifie. Une réinstallation conserve
    l'état d'activation existant (ne désactive jamais un module en cours
    d'usage).
    """
    existing = Module.objects.filter(pk=parsed_manifest.id).first()
    state = existing.state if existing else Module.State.INSTALLED

    module, _created = Module.objects.update_or_create(
        id=parsed_manifest.id,
        defaults={
            "name": parsed_manifest.name,
            "version": parsed_manifest.version,
            "state": state,
            "manifest_snapshot": parsed_manifest.raw,
        },
    )

    # Idempotent (get_or_create) : une réinstallation ne duplique jamais
    # les permissions déjà enregistrées (contrainte d'unicité TECH-001).
    for perm in parsed_manifest.permissions:
        for action in perm.actions:
            Permission.objects.get_or_create(
                module_id=parsed_manifest.id, resource=perm.resource, action=action
            )

    record_audit_event(
        actor=actor,
        action="module.install",
        target=parsed_manifest.id,
        metadata={"version": parsed_manifest.version, "status": "success"},
    )
    return module


def activate_module(module_id: str, actor: User) -> Module:
    """Active un module — §12 étape 4, §14.

    Idempotent : activer un module déjà actif est un no-op réussi (aucune
    duplication de ModuleDependency ni de permission). Le refus est
    atomique : aucune écriture n'a lieu si une dépendance manque, mais
    l'entrée d'audit du refus est écrite (hors de la transaction d'écriture
    qui, elle, n'a jamais commencé — donc rien à annuler).
    """
    module = Module.objects.filter(pk=module_id).first()
    if module is None:
        raise ModuleManagerError(f"Module non installé : {module_id!r}")

    if module.state == Module.State.ACTIVATED:
        return module

    unsatisfied = check_dependencies(module)
    if unsatisfied:
        record_audit_event(
            actor=actor,
            action="module.activate",
            target=module_id,
            metadata={
                "status": "denied",
                "unsatisfied": [
                    {
                        "module": d.module,
                        "version_constraint": d.version_constraint,
                        "reason": d.reason,
                    }
                    for d in unsatisfied
                ],
            },
        )
        raise DependencyError(module_id, unsatisfied)

    with transaction.atomic():
        # Reverrouille et revérifie sous transaction : protège contre une
        # modification concurrente d'une dépendance entre la vérification
        # ci-dessus et l'écriture (TOCTOU).
        module = Module.objects.select_for_update().get(pk=module_id)
        if module.state == Module.State.ACTIVATED:
            return module

        unsatisfied = check_dependencies(module, for_update=True)
        if unsatisfied:
            raise DependencyError(module_id, unsatisfied)

        for dep in _declared_dependencies(module):
            ModuleDependency.objects.get_or_create(
                module=module,
                depends_on_module_id=dep["module"],
                defaults={"version_constraint": dep["version"]},
            )

        module.state = Module.State.ACTIVATED
        module.save(update_fields=["state"])

        record_audit_event(
            actor=actor,
            action="module.activate",
            target=module_id,
            metadata={"status": "success"},
        )

    return module
