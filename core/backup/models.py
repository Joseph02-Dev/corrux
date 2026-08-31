"""Modèle `backup_runs` — table `core.backup_runs` (§7, §11).

Trace chaque exécution du mécanisme de sauvegarde. Consultable par
l'administrateur (écran UI-205, hors périmètre de ce ticket).
"""

from django.db import models


class BackupRun(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Succès"
        FAILURE = "failure", "Échec"
        REFUSED = "refused", "Refusé"

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices)
    # §7 ne marque pas ces deux champs NULLABLE, mais un run REFUSED/FAILURE
    # ne produit par définition aucun fichier : size_bytes n'a alors aucune
    # valeur sensée (nullable) et location reste une chaîne vide plutôt que
    # de coder une valeur arbitraire.
    size_bytes = models.BigIntegerField(null=True, blank=True)
    location = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        app_label = "core"
        db_table = '"core"."backup_runs"'
        verbose_name = "Exécution de sauvegarde"
        verbose_name_plural = "Exécutions de sauvegarde"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.status} · {self.started_at}"
