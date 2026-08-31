"""Vues de référence de la couche présentation — UI-101, UI-102.

Pages de documentation vivante, indépendantes de toute navigation
produit : aucune logique métier, servent à valider visuellement le rendu
pour les tickets UI suivants.
"""

from django.shortcuts import render


def design_system_showcase(request):
    return render(request, "ui/showcase.html")


def shell_showcase(request):
    """Démonstration du shell (UI-102).

    « Login est hors shell » (ux-ui-design-v1.md §2, UI-103 non fait) :
    un utilisateur anonyme ne voit jamais le shell — page simple sans
    Topbar/Sidebar, pas de redirection inventée vers un login inexistant.
    """
    if request.corrux_user is None:
        return render(request, "ui/shell_showcase_anonymous.html")
    return render(request, "ui/shell_showcase.html")
