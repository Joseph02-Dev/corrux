from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Platform Core — cf. architecture-technique-v1.md §6.

    Les modèles (identity, authz, storage, modules, backup) sont ajoutés à
    partir du ticket TECH-001 ; ce squelette (INIT-001) ne porte aucune
    logique métier.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    label = "core"
    verbose_name = "Platform Core"
