from django.apps import AppConfig


class RhConfig(AppConfig):
    """Module Ressources Humaines — squelette vide (INIT-001).

    Modèles ajoutés à partir de TECH-030. Dépend du module Documentation/
    Archivage (cf. vision-produit-v1.md §7, manifest.yaml ajouté en TECH-035).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.rh"
    label = "rh"
    verbose_name = "Ressources Humaines"
