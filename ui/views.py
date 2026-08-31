"""Vue de référence du Design System — UI-101.

Page de documentation vivante des tokens/composants, indépendante de
toute navigation produit (pas le shell applicatif, qui est UI-102) :
aucune authentification requise, pas de sidebar/topbar, pas de logique
métier. Sert à valider visuellement le rendu et à documenter l'usage pour
les tickets UI suivants.
"""

from django.shortcuts import render


def design_system_showcase(request):
    return render(request, "ui/showcase.html")
