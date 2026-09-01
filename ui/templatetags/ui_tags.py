"""Composants du Design System CORRUX — UI-101.

Cf. ux-ui-design-v1.md §4. Chaque composant est une inclusion tag Django
(interface de paramètres explicite, testable, sans JavaScript requis pour
le rendu de base — cohérent avec un stack Django + templates serveur).

Usage dans un template :
    {% load ui_tags %}
    {% corrux_button label="Enregistrer" variant="primary" %}
    {% corrux_badge label="Actif" tone="success" %}
    {% corrux_field label="Identifiant" name="username" field_id="id_username" %}
    {% corrux_icon name="check" %}
"""

from __future__ import annotations

from django import template

from ui.navigation import get_navigation

register = template.Library()

_BUTTON_VARIANTS = {"primary", "secondary", "tertiary", "danger"}
_BADGE_TONES = {"neutral", "success", "warning", "error", "info"}

# Jeu d'icônes minimal (trait, 24px, un seul style) — cf. component css.
# Chemin SVG uniquement (le <svg> englobant est dans icon.html) : ajouter
# une icône = ajouter une entrée ici, aucune nouvelle dépendance.
_ICON_PATHS: dict[str, str] = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "close": '<path d="M18 6 6 18"/><path d="M6 6l12 12"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "user": (
        '<path d="M20 21a8 8 0 0 0-16 0"/>'
        '<circle cx="12" cy="7" r="4"/>'
    ),
    "warning": (
        '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 '
        '1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
        '<line x1="12" x2="12" y1="9" y2="13"/>'
        '<line x1="12" x2="12.01" y1="17" y2="17"/>'
    ),
    # Ajoutés pour UI-102 (Sidebar/Topbar) — mêmes conventions (trait,
    # viewBox 24x24), aucun second système d'icônes créé.
    "folder": (
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 '
        '2H5a2 2 0 0 1-2-2V7z"/>'
    ),
    "settings": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 '
        '2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 '
        '2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06'
        '.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 '
        '0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 '
        '0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 '
        '1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 '
        '0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06'
        '.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 '
        '0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'
    ),
    "logout": (
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
        '<polyline points="16 17 21 12 16 7"/>'
        '<line x1="21" x2="9" y1="12" y2="12"/>'
    ),
    "bell": (
        '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
        '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'
    ),
}


@register.inclusion_tag("ui/components/button.html")
def corrux_button(
    label, variant="primary", type="button", disabled=False, loading=False, href=""  # noqa: A002
):
    """Bouton — variantes primaire/secondaire/tertiaire/danger.

    États disabled/loading gérés côté serveur (rendu initial) ; le
    basculement dynamique loading<->défaut appartient au JS d'un écran
    consommateur (hors périmètre UI-101, aucun JS de composant ici).

    `href` (UI-104, rétrocompatible) : si fourni, rend un lien <a> stylé
    comme un bouton plutôt qu'un <button> — pour les actions de
    navigation (ex. CTA d'un état vide, « Réessayer » d'un état erreur),
    par opposition aux soumissions de formulaire.
    """
    if variant not in _BUTTON_VARIANTS:
        raise ValueError(
            f"corrux_button: variant invalide {variant!r}, attendu parmi {_BUTTON_VARIANTS}"
        )
    return {
        "label": label,
        "variant": variant,
        "type": type,
        "disabled": disabled,
        "loading": loading,
        "href": href,
    }


@register.inclusion_tag("ui/components/badge.html")
def corrux_badge(label, tone="neutral"):
    """Badge de statut — un seul composant, ton sémantique paramétrable.

    Le mapping statut métier -> ton (ex. "Actif" -> "success") est décidé
    par l'écran appelant, pas par ce composant générique.
    """
    if tone not in _BADGE_TONES:
        raise ValueError(f"corrux_badge: tone invalide {tone!r}, attendu parmi {_BADGE_TONES}")
    return {"label": label, "tone": tone}


@register.inclusion_tag("ui/components/field.html")
def corrux_field(
    label,
    name,
    field_id=None,
    input_type="text",
    value="",
    placeholder="",
    required=False,
    disabled=False,
    error="",
    success="",
    help_text="",
    autofocus=False,
    autocomplete="",
):
    """Champ de formulaire (texte) — label, aide, validation inline.

    `error` et `success` sont mutuellement exclusifs (erreur prioritaire
    si les deux sont fournis) ; à défaut, `help_text` s'affiche.
    `autofocus`/`autocomplete` ajoutés en UI-103 (rétrocompatibles, défaut
    inchangé pour tout usage existant).
    """
    return {
        "label": label,
        "name": name,
        "field_id": field_id or f"id_{name}",
        "input_type": input_type,
        "value": value,
        "placeholder": placeholder,
        "required": required,
        "disabled": disabled,
        "error": error,
        "success": success if not error else "",
        "help_text": help_text,
        "autofocus": autofocus,
        "autocomplete": autocomplete,
    }


@register.inclusion_tag("ui/components/icon.html")
def corrux_icon(name, label="", small=False):
    """Icône SVG en ligne — trait, 24px (16px si `small`), un seul style.

    Décorative par défaut (aria-hidden). Fournir `label` uniquement pour
    une icône seule sans texte adjacent (ex. bouton icône-only), afin de
    lui donner un nom accessible.
    """
    if name not in _ICON_PATHS:
        raise ValueError(
            f"corrux_icon: icône inconnue {name!r}, attendu parmi {sorted(_ICON_PATHS)}"
        )
    return {"paths": _ICON_PATHS[name], "label": label, "small": small}


# --- États communs (UI-104) ------------------------------------------------
# Cf. maquettes-ui-v1-lot1.md planche 9, ux-ui-design-v1.md §7. Composants
# purement présentationnels : aucun ne consulte request.user, les
# permissions ou l'état des modules — la décision reste au backend
# (critère explicite du mandat UI-104), ce fichier ne fait qu'afficher ce
# qu'on lui fournit.

DEFAULT_PERMISSION_DENIED_MESSAGE = "Vous n'avez pas la permission d'accéder à cette page."


@register.inclusion_tag("ui/components/loading_skeleton.html")
def corrux_loading_skeleton(rows=3):
    """Squelette de chargement pour listes/tableaux.

    Différent du spinner de corrux_button(loading=True) (UI-101, action
    ponctuelle) — non dupliqué ici, réservé au contenu en cours de
    chargement (liste, tableau).
    """
    return {"row_range": range(rows)}


@register.inclusion_tag("ui/components/empty_state.html")
def corrux_empty_state(message, icon="", action_label="", action_href=""):
    """État vide — message court + CTA primaire optionnel (navigation).

    Le CTA n'apparaît que si `action_label` ET `action_href` sont fournis
    ensemble ; jamais un bouton non fonctionnel.
    """
    return {
        "message": message,
        "icon": icon,
        "action_label": action_label,
        "action_href": action_href,
    }


@register.inclusion_tag("ui/components/error_state.html")
def corrux_error_state(message, action_label="", action_href=""):
    """État d'erreur — message explicite + action de reprise secondaire
    optionnelle (navigation, ex. rechargement). Le message affiché est
    strictement celui fourni par l'appelant : ce composant n'invente ni
    ne transforme aucun message technique brut."""
    return {"message": message, "action_label": action_label, "action_href": action_href}


@register.inclusion_tag("ui/components/permission_denied.html")
def corrux_permission_denied(message=DEFAULT_PERMISSION_DENIED_MESSAGE):
    """Accès refusé — message dédié, SANS action (jamais de CTA).

    Ne consulte aucune permission : affiche uniquement le message fourni
    (ou le message générique par défaut, qui ne révèle jamais quelle
    permission précise manque)."""
    return {"message": message}


@register.inclusion_tag("ui/shell/topbar.html", takes_context=True)
def corrux_topbar(context):
    """Barre supérieure — marque, recherche (non fonctionnelle),
    notifications (non fonctionnelles), menu utilisateur.

    Propage explicitement `request` dans le contexte rendu : une
    inclusion tag imbriquée (corrux_user_menu, appelée depuis
    topbar.html) reçoit un contexte isolé reconstruit par Django — elle
    n'hérite PAS automatiquement de `request` depuis le contexte parent
    (vérifié : sans cette ligne, le menu utilisateur disparaît
    silencieusement, request.get("request") valant None dans le tag
    imbriqué).
    """
    return {"request": context.get("request")}


@register.inclusion_tag("ui/shell/sidebar.html", takes_context=True)
def corrux_sidebar(context):
    """Navigation latérale, filtrée par permission via get_navigation()
    (aucune seconde logique de permission — réutilise TECH-003)."""
    request = context.get("request")
    user = getattr(request, "corrux_user", None) if request is not None else None
    current_path = request.path if request is not None else ""
    return {"groups": get_navigation(user), "current_path": current_path}


@register.inclusion_tag("ui/components/user_menu.html", takes_context=True)
def corrux_user_menu(context):
    """Menu utilisateur (Mon profil / Se déconnecter) — UI-105.

    <details>/<summary> natif : aucun JavaScript, opérable au clavier par
    défaut (Entrée/Espace). Ne rend rien si aucun utilisateur authentifié
    (composant défensif, même si Topbar ne l'appelle qu'en contexte
    authentifié).
    """
    request = context.get("request")
    user = getattr(request, "corrux_user", None) if request is not None else None
    if user is None:
        return {"user": None}

    role_names = ", ".join(
        user.user_roles.select_related("role")
        .order_by("role__name")
        .values_list("role__name", flat=True)
    )
    initial = (user.full_name or user.username or "?")[0].upper()
    return {"user": user, "role_names": role_names, "initial": initial}
