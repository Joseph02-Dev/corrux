"""Journal d'audit — table `core.audit_log` (§7, §17).

Instrumentation minimale nécessaire à TECH-006 : §16 liste explicitement
« activation/désactivation de module » parmi les actions sensibles devant
être journalisées, et TECH-006 demande de ne pas différer une
instrumentation directement requise. La formalisation complète (login/
logout, écran de consultation, filtrage — TECH-008) reste hors périmètre :
ce fichier ne porte que la table et l'écriture.
"""

from django.db import models

from core.identity.models import User


class AuditLog(models.Model):
    # Pas de NULLABLE marqué au §7 pour actor_user_id ; requis dans la
    # majorité des cas (technicien/administrateur identifié). Nullable
    # depuis TECH-009 : une sauvegarde planifiée par systemd (timer) n'a
    # aucun utilisateur interactif — correction minimale indispensable,
    # sans laquelle l'exécution automatisée exigée par TECH-009 ne peut
    # pas être journalisée du tout. PROTECT conservé : un enregistrement
    # d'audit ne doit jamais disparaître silencieusement.
    actor_user = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_events"
    )
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        app_label = "core"
        db_table = '"core"."audit_log"'
        verbose_name = "Entrée de journal d'audit"
        verbose_name_plural = "Journal d'audit"

    def __str__(self):
        return f"{self.action} · {self.target} · {self.timestamp}"
