"""Modèles du Module Manager — tables `core.modules`, `core.module_dependencies`.

Cf. architecture-technique-v1.md §7. Ce fichier ne porte que la structure
de données : aucune logique d'installation/activation/désactivation
(Module Manager complet = TECH-006/TECH-007).
"""

from django.core.validators import RegexValidator
from django.db import models

from core.modules.manifest import MODULE_ID_PATTERN, VERSION_CONSTRAINT_PATTERN, VERSION_PATTERN

_module_id_validator = RegexValidator(
    MODULE_ID_PATTERN, "Identifiant de module invalide (attendu : slug minuscule)."
)
_version_validator = RegexValidator(
    VERSION_PATTERN, "Version invalide (format attendu : MAJOR.MINOR.PATCH)."
)
_version_constraint_validator = RegexValidator(
    VERSION_CONSTRAINT_PATTERN,
    "Contrainte de version invalide (ex. \">=1.0.0\").",
)


class Module(models.Model):
    """Un module enregistré (`core.modules`).

    `id` est l'identifiant string du module (le champ `id` du manifest.yaml,
    ex. "documentation", "rh") — pas un entier surrogate : c'est déjà la
    convention utilisée par `core.permissions.module_id` (TECH-001).
    """

    class State(models.TextChoices):
        INSTALLED = "installed", "Installé"
        ACTIVATED = "activated", "Activé"
        DEACTIVATED = "deactivated", "Désactivé"

    id = models.CharField(max_length=100, primary_key=True, validators=[_module_id_validator])
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=50, validators=[_version_validator])
    # Pas de valeur par défaut : la décision de l'état à la création
    # (cycle d'installation) appartient à TECH-006, pas à ce ticket.
    state = models.CharField(max_length=20, choices=State.choices)
    manifest_snapshot = models.JSONField()

    class Meta:
        app_label = "core"
        db_table = '"core"."modules"'
        verbose_name = "Module"
        verbose_name_plural = "Modules"

    def __str__(self):
        return f"{self.id} ({self.state})"


class ModuleDependency(models.Model):
    """Dépendance déclarée d'un module vers un autre (`core.module_dependencies`).

    Dénormalisée depuis le manifeste (§7) pour requêtage rapide par le
    Module Manager (TECH-006).
    """

    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="dependencies")
    depends_on_module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name="dependents"
    )
    version_constraint = models.CharField(
        max_length=50, validators=[_version_constraint_validator]
    )

    class Meta:
        app_label = "core"
        db_table = '"core"."module_dependencies"'
        constraints = [
            models.UniqueConstraint(
                fields=["module", "depends_on_module"], name="uniq_module_dependency"
            ),
        ]
        verbose_name = "Dépendance de module"
        verbose_name_plural = "Dépendances de module"

    def __str__(self):
        return f"{self.module_id} -> {self.depends_on_module_id} ({self.version_constraint})"
