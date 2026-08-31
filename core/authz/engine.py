"""Moteur d'autorisation générique `module.resource.action` — TECH-003.

Cf. architecture-technique-v1.md §9 : les permissions sont assignables aux
rôles (§7 : aucune table de permission individuelle au niveau `core` — les
exceptions par ressource, ex. document, sont une table locale au module
concerné, hors périmètre de ce moteur générique).

Pur Python, sans dépendance HTTP : réutilisable telle quelle par
Documentation, RH et tout futur module, quel que soit le framework de vue
utilisé (vue Django classique, DRF, etc.).

Autorité exclusivement côté serveur : ne lit jamais de rôle/permission
fourni par le client (headers, body, session côté client) — uniquement
l'état persistant en base, résolu depuis `user`.
"""

from __future__ import annotations

from core.authz.models import Permission
from core.identity.models import User


def get_effective_permission_codes(user: User | None) -> set[str]:
    """Ensemble des permissions `module.resource.action` de l'utilisateur.

    Union de toutes les permissions accordées via chacun des rôles de
    l'utilisateur. Retourne un ensemble vide si `user` est None ou inactif
    — l'absence de permission n'est jamais une autorisation.
    """
    if user is None or user.status != User.Status.ACTIVE:
        return set()

    role_ids = user.user_roles.values_list("role_id", flat=True)
    permissions = (
        Permission.objects.filter(role_permissions__role_id__in=role_ids)
        .values_list("module_id", "resource", "action")
        .distinct()
    )
    return {f"{module_id}.{resource}.{action}" for module_id, resource, action in permissions}


def has_permission(user: User | None, permission_code: str) -> bool:
    """True si `user` possède `permission_code` (ex. "documentation.document.read")."""
    return permission_code in get_effective_permission_codes(user)
