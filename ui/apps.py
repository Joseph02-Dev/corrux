from django.apps import AppConfig


class UiConfig(AppConfig):
    """Couche présentation CORRUX (Django templates, décision actée avant
    UI-101) — cf. ux-ui-design-v1.md §4, maquettes-ui-v1-lot1.md.

    Ce ticket (UI-101) pose les fondations du Design System : tokens CSS
    et composants primitifs (Bouton, Champ, Badge, Icône). Le shell
    applicatif (Topbar/Sidebar), l'écran Login et les états communs
    appartiennent à UI-102/103/104 — pas à cette app en l'état.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "ui"
    label = "ui"
    verbose_name = "Design System"
