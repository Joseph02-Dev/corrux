"""Vues de référence de la couche présentation — UI-101, UI-102, UI-103.

Pages de documentation vivante (UI-101/102) et écran de connexion réel
(UI-103). Aucune logique métier propre : réutilise directement
core.identity.auth (TECH-002/TECH-008) comme unique autorité.
"""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

from core.identity import auth

# Provisoire : aucune page d'accueil/tableau de bord réelle n'existe
# encore (seuls UI-101/UI-102 fournissent des pages, toutes deux des
# démonstrations, pas des écrans produit). Signalé comme limite connue,
# pas inventé comme définitif — à remplacer dès qu'un vrai point
# d'entrée post-connexion existera.
DEFAULT_LOGIN_REDIRECT = "/shell-demo/"


def design_system_showcase(request):
    return render(request, "ui/showcase.html")


def shell_showcase(request):
    """Démonstration du shell (UI-102).

    « Login est hors shell » (ux-ui-design-v1.md §2) : un utilisateur
    anonyme ne voit jamais le shell — page simple sans Topbar/Sidebar.
    """
    if request.corrux_user is None:
        return render(request, "ui/shell_showcase_anonymous.html")
    return render(request, "ui/shell_showcase.html")


def _safe_redirect_target(request, candidate: str) -> str:
    """Ne redirige vers `candidate` que s'il s'agit d'une URL relative au
    même site (protection open redirect) — utilise le helper Django
    standard, pas une vérification maison."""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return DEFAULT_LOGIN_REDIRECT


def login_page(request):
    """Écran de connexion — UI-103.

    Réutilise directement core.identity.auth.authenticate()/login()
    (TECH-002) : même backend, même instrumentation d'audit (TECH-008,
    déjà intégrée à ces fonctions), aucune seconde logique
    d'authentification créée. Ne réutilise PAS core.identity.views.LoginView
    (API JSON, TECH-002) : cette vue rend/redirige nativement en HTML,
    ce que l'API JSON ne fait pas — TECH-002 n'est pas modifié pour autant.

    Message d'erreur strictement identique quelle que soit la cause
    (identifiant inconnu / mot de passe incorrect / compte verrouillé /
    compte inactif) — propriété de sécurité de TECH-002, préservée à
    l'identique ici, jamais affinée côté UI.
    """
    next_param = request.POST.get("next") or request.GET.get("next", "")

    if request.corrux_user is not None:
        return HttpResponseRedirect(_safe_redirect_target(request, next_param))

    error = ""
    submitted_username = ""

    if request.method == "POST":
        submitted_username = request.POST.get("username", "")
        password = request.POST.get("password", "")

        if not submitted_username or not password:
            error = auth.GENERIC_ERROR_MESSAGE
        else:
            try:
                user = auth.authenticate(username=submitted_username, raw_password=password)
            except auth.AuthenticationError:
                error = auth.GENERIC_ERROR_MESSAGE
            else:
                auth.login(request, user)
                return HttpResponseRedirect(_safe_redirect_target(request, next_param))

    return render(
        request,
        "ui/login.html",
        {"error": error, "username": submitted_username, "next": next_param},
    )
