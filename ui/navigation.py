"""Construction de la navigation applicative (Sidebar) — UI-102.

Cf. ux-ui-design-v1.md §2 : la sidebar est générée dynamiquement, filtrée
par permission. Réutilise exclusivement `core.authz.engine.has_permission`
(TECH-003) — AUCUNE seconde logique de permission n'est créée ici. Pour
Documentation/RH, vérifie en plus l'état d'activation du module via
`core.modules.models.Module` (TECH-005/006) — deux conditions distinctes
et nécessaires (décision produit : « module activé + permission »), pas
une duplication du moteur RBAC.

Ce module est pur Python (aucune dépendance HTTP) : testable directement
avec de vrais objets User/Role/Permission/Module, indépendamment du rendu.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.authz.engine import has_permission
from core.identity.models import User
from core.modules.models import Module


@dataclass(frozen=True)
class NavItem:
    label: str
    href: str
    icon: str


@dataclass(frozen=True)
class NavGroup:
    label: str
    items: tuple[NavItem, ...]


def _module_is_activated(module_id: str) -> bool:
    """Lecture directe de core.modules (TECH-005/006) — aucun état parallèle."""
    return Module.objects.filter(pk=module_id, state=Module.State.ACTIVATED).exists()


def get_navigation(user: User | None) -> tuple[NavGroup, ...]:
    """Groupes de navigation visibles pour `user`.

    Décision produit (ux-ui-design-v1.md §2, décisions #8/#9) :
    - un groupe entier est absent si aucun de ses items n'est visible ;
    - un item isolé est absent (jamais grisé) si sa permission manque.

    Retourne un tuple vide si `user` est None — même garde par défaut que
    `core.authz.engine.has_permission` (l'absence de permission n'est
    jamais une autorisation).

    Destinations (`href`) : "#" en placeholder — aucun écran Documentation/
    RH/Administration n'existe encore (UI-2xx/3xx/4xx non commencés).
    Décision de rendu non finale, signalée dans le rapport UI-102.
    """
    if user is None:
        return ()

    groups: list[NavGroup] = []

    admin_items: list[NavItem] = []
    if has_permission(user, "core.user.read"):
        admin_items.append(NavItem("Utilisateurs & rôles", "/utilisateurs/", "user"))
    if has_permission(user, "core.module.read"):
        admin_items.append(NavItem("Modules", "#", "settings"))
    if has_permission(user, "core.backup.read"):
        admin_items.append(NavItem("Sauvegardes", "#", "check"))
    if has_permission(user, "core.audit.read"):
        admin_items.append(NavItem("Journal d'audit", "#", "search"))
    if admin_items:
        groups.append(NavGroup("Administration", tuple(admin_items)))

    documentation_items: list[NavItem] = []
    if _module_is_activated("documentation") and has_permission(
        user, "documentation.document.read"
    ):
        documentation_items.append(NavItem("Documents", "#", "folder"))
        documentation_items.append(NavItem("Recherche", "#", "search"))
    if documentation_items:
        groups.append(NavGroup("Documentation", tuple(documentation_items)))

    rh_items: list[NavItem] = []
    if _module_is_activated("rh"):
        if has_permission(user, "rh.employe.lire"):
            rh_items.append(NavItem("Employés", "#", "user"))
        if has_permission(user, "rh.conge.lire"):
            rh_items.append(NavItem("Congés", "#", "check"))
    if rh_items:
        groups.append(NavGroup("Ressources Humaines", tuple(rh_items)))

    return tuple(groups)
