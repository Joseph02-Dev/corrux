from django.apps import AppConfig


class DocumentationConfig(AppConfig):
    """Module Documentation/Archivage — squelette vide (INIT-001).

    Modèles ajoutés à partir de TECH-020. Ne dépend d'aucun autre module
    métier (cf. vision-produit-v1.md §7).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.documentation"
    label = "documentation"
    verbose_name = "Documentation / Archivage"
