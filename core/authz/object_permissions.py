"""Primitive générique d'autorisation par objet — TECH-023.

Complète le moteur RBAC global (`core.authz.engine`, permissions
`module.resource.action`) sans le modifier ni le détourner : celui-ci
reste exclusivement dédié aux permissions globales. Cette primitive
répond à une question différente — « cet utilisateur a-t-il un droit
sur CET objet précis » — via un mécanisme strictement additif (aucun
deny, aucune priorité entre une permission utilisateur et une
permission de rôle : une seule ligne applicable suffit).

Générique par conception : ne connaît aucun modèle métier (pas de
`DocumentPermission` importé ici, jamais `modules/*` importé depuis
`core/*` — convention de dépendance à sens unique déjà respectée dans
tout le projet, vérifiée avant d'écrire ce fichier). L'appelant (ex.
`modules.documentation`) fournit un queryset de lignes de permission
déjà scopées à l'objet concerné, exposant les champs
`user`/`role_id`/`action` — réutilisable par tout futur module ayant un
besoin de permission par objet similaire (ex. RH).
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from core.identity.models import User


def has_object_permission(user: User | None, grants: QuerySet, action: str) -> bool:
    """True si `grants` (déjà filtré par l'appelant sur l'objet précis
    concerné) contient au moins une ligne accordant `action` à `user`
    directement, ou à l'un de ses rôles effectifs.

    Modèle additif pur : aucun deny, aucune priorité entre une
    permission utilisateur et une permission de rôle. Retourne toujours
    False si `user` est None ou inactif — l'absence de permission n'est
    jamais une autorisation (même garde que
    `core.authz.engine.get_effective_permission_codes`).
    """
    if user is None or user.status != User.Status.ACTIVE:
        return False

    role_ids = user.user_roles.values_list("role_id", flat=True)
    return grants.filter(Q(user=user) | Q(role_id__in=role_ids), action=action).exists()
