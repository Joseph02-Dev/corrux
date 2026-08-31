"""Écriture du journal d'audit — TECH-006 (instrumentation minimale).

Cf. architecture-technique-v1.md §16/§17 : toute action sensible est
journalisée, de façon non modifiable applicativement. Ce module n'expose
volontairement aucune fonction de modification/suppression : écriture
seule.
"""

from __future__ import annotations

from core.audit.models import AuditLog
from core.identity.models import User


def record_audit_event(
    *, actor: User | None, action: str, target: str, metadata: dict | None = None
) -> AuditLog:
    """Écrit une entrée d'audit et la retourne (déjà persistée).

    `actor=None` pour un événement déclenché automatiquement (ex. sauvegarde
    planifiée par systemd, TECH-009), sans utilisateur interactif.
    """
    return AuditLog.objects.create(
        actor_user=actor, action=action, target=target, metadata=metadata or {}
    )
