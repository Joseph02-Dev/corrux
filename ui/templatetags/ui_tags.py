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

from dataclasses import dataclass

from django import template

from core.authz.engine import has_permission
from ui.navigation import get_navigation, module_is_activated

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
    "trash": (
        '<path d="M3 6h18"/>'
        '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    ),
}


@register.inclusion_tag("ui/components/button.html")
def corrux_button(
    label,
    variant="primary",
    type="button",  # noqa: A002
    disabled=False,
    loading=False,
    href="",
    formaction="",
):
    """Bouton — variantes primaire/secondaire/tertiaire/danger.

    États disabled/loading gérés côté serveur (rendu initial) ; le
    basculement dynamique loading<->défaut appartient au JS d'un écran
    consommateur (hors périmètre UI-101, aucun JS de composant ici).

    `href` (UI-104) : rend un <a> stylé bouton plutôt qu'un <button> pour
    les actions de navigation.
    `formaction` (UI-201, rétrocompatible) : override HTML natif de la
    cible d'un bouton de soumission au sein d'un formulaire existant (ex.
    « Réinitialiser le mot de passe » dans le Drawer d'édition, qui cible
    une URL différente de l'action principale du formulaire) — aucun
    JavaScript nécessaire, mécanisme natif du navigateur.
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
        "formaction": formaction,
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
    options=None,
):
    """Champ de formulaire — label, aide, validation inline.

    `error` et `success` sont mutuellement exclusifs (erreur prioritaire
    si les deux sont fournis) ; à défaut, `help_text` s'affiche.
    `autofocus`/`autocomplete` (UI-103) et `options` (UI-201,
    input_type="select" : liste de tuples (valeur, libellé)) sont
    rétrocompatibles — tout usage existant sans ces paramètres est
    inchangé.
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
        "options": options or [],
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
    """Barre supérieure — marque, recherche, notifications (non
    fonctionnelles), menu utilisateur.

    Propage explicitement `request` dans le contexte rendu : une
    inclusion tag imbriquée (corrux_user_menu, appelée depuis
    topbar.html) reçoit un contexte isolé reconstruit par Django — elle
    n'hérite PAS automatiquement de `request` depuis le contexte parent
    (vérifié : sans cette ligne, le menu utilisateur disparaît
    silencieusement, request.get("request") valant None dans le tag
    imbriqué).

    `search_available` (UI-305) : la recherche n'est un formulaire
    fonctionnel que si le module Documentation est activé ET
    l'utilisateur possède documentation.document.read — même garde que
    la Sidebar (ui/navigation.py), jamais une fonctionnalité présentée
    comme disponible sans l'être réellement."""
    request = context.get("request")
    user = getattr(request, "corrux_user", None) if request else None
    search_available = bool(
        user is not None
        and module_is_activated("documentation")
        and has_permission(user, "documentation.document.read")
    )
    return {"request": request, "search_available": search_available}


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


# --- Modal de confirmation destructive (UI-204) -----------------------------
# <dialog> natif (décision utilisateur explicite, Phase 2/3 UI-204) : focus
# trap, focus initial, fermeture Échap, aria-modal gérés nativement par le
# navigateur — aucune réimplémentation manuelle. Ouverture déclenchée à
# distance via ui/static/ui/js/modal.js (générique, sans logique métier,
# chargé une seule fois par ui/templates/ui/shell/base.html).


@register.inclusion_tag("ui/components/modal.html")
def corrux_modal(modal_id, title, message, confirm_label, action, cancel_label="Annuler"):
    """Modal de confirmation destructive — UI-204.

    Réutilisable tel quel par tout écran (ex. UI-201 désactivation de
    compte, UI-203 désactivation de module) sans variante graphique —
    seuls les paramètres textuels et l'URL cible changent. Formulaire de
    confirmation réellement POST, jeton CSRF inclus. Aucune vérification
    RBAC ici : l'autorisation reste entièrement du ressort du serveur au
    moment de la soumission du formulaire.
    """
    return {
        "modal_id": modal_id,
        "title": title,
        "message": message,
        "confirm_label": confirm_label,
        "action": action,
        "cancel_label": cancel_label,
    }


@register.inclusion_tag("ui/components/modal_trigger.html")
def corrux_modal_trigger(modal_id, label, variant="secondary"):
    """Déclencheur générique d'un modal distant (UI-204).

    Réutilise les classes .corrux-button existantes (components.css,
    UI-101) plutôt qu'un second système de bouton. La liaison au
    <dialog> correspondant se fait via l'attribut data-modal-target,
    résolu par le script générique modal.js.
    """
    if variant not in _BUTTON_VARIANTS:
        raise ValueError(
            f"corrux_modal_trigger: variant invalide {variant!r}, "
            f"attendu parmi {_BUTTON_VARIANTS}"
        )
    return {"modal_id": modal_id, "label": label, "variant": variant}


# --- Table / Drawer génériques (UI-201) -------------------------------------
# Premiers composants structurels au-delà des primitives UI-101 — même
# registre, aucun second système. Réutilisables tels quels par UI-205
# (Sauvegardes) et UI-206 (Journal d'audit) pour Table ; par tout futur
# formulaire latéral pour Drawer.


@dataclass(frozen=True)
class TableRow:
    """Une ligne de corrux_table.

    `cells` : valeurs texte, échappées automatiquement par le template
    ({{ cell }}). `actions_html` : fragment HTML de confiance déjà rendu
    par l'appelant (ex. via render_to_string sur modal_trigger.html) —
    jamais une donnée utilisateur brute insérée directement.
    """

    cells: tuple[str, ...]
    actions_html: str = ""


@register.inclusion_tag("ui/components/table.html")
def corrux_table(headers, rows):
    """Table générique — UI-201, réutilisable telle quelle par UI-205/206.

    Aucune logique métier : `headers` (libellés de colonnes) et `rows`
    (liste de TableRow) sont entièrement fournis par l'appelant.
    """
    return {"headers": headers, "rows": rows}


@register.inclusion_tag("ui/components/drawer.html")
def corrux_drawer(
    drawer_id, title, content, action="", method="post",
    submit_label="Enregistrer", cancel_label="Annuler", open=False, enctype="",  # noqa: A002
):
    """Panneau latéral générique — structure/layout uniquement (UI-201).

    <dialog> natif, même mécanisme d'ouverture que corrux_modal (UI-204) :
    ouvert via corrux_modal_trigger ciblant `drawer_id` (le mécanisme est
    générique, pas spécifique à un "modal" au sens visuel). `content` est
    un fragment HTML de confiance déjà rendu par l'appelant — ce
    composant ne connaît aucun champ de formulaire spécifique, il ne
    fournit que l'habillage (titre, zone de contenu, actions Annuler/
    Soumettre).

    `open` (UI-201) : ouvre le <dialog> nativement dès le rendu (attribut
    HTML `open`), sans JavaScript — utilisé pour réafficher un formulaire
    en erreur après une soumission invalide, ou pour un accès GET direct
    à l'URL d'édition.

    `enctype` (UI-302) : optionnel, vide par défaut (comportement
    inchangé pour tous les appelants existants) — `"multipart/form-data"`
    pour un formulaire contenant un champ fichier (seul cas où le
    navigateur transmet réellement le contenu binaire).
    """
    return {
        "drawer_id": drawer_id,
        "title": title,
        "content": content,
        "action": action,
        "method": method,
        "submit_label": submit_label,
        "cancel_label": cancel_label,
        "open": open,
        "enctype": enctype,
    }


# --- Modal d'information (UI-203) -------------------------------------------
# Distinct de corrux_modal (UI-204, confirmation destructive : Annuler +
# action + formulaire POST) — sémantique différente, explicitement
# signalée par maquettes-ui-v1-lot2.md §6 : « pas le composant de
# confirmation destructive... pas de choix Annuler/Confirmer, une seule
# sortie ». Ne détourne pas corrux_modal ; réutilise le même mécanisme
# d'ouverture natif (<dialog> + modal.js, non modifié).


@register.inclusion_tag("ui/components/info_modal.html")
def corrux_info_modal(modal_id, title, message, items=None, close_label="Fermer", open=False):  # noqa: A002
    """Modal d'information — un seul bouton de sortie, aucun formulaire,
    aucune requête réseau, aucun CSRF nécessaire.

    `items` : liste de chaînes affichées comme une liste à puces
    (échappées automatiquement par le template) — ex. noms des modules
    dépendants actifs bloquant une désactivation (TECH-007).
    `open` (UI-203) : même mécanisme que corrux_drawer — ouverture
    native après une action refusée, sans JavaScript.
    """
    return {
        "modal_id": modal_id,
        "title": title,
        "message": message,
        "items": items or [],
        "close_label": close_label,
        "open": open,
    }


# --- Carte de synthèse générique (UI-205) -----------------------------------
# Purement présentationnel : label + valeur, aucune connaissance d'aucun
# domaine (pas de référence à BackupRun ni à quoi que ce soit d'autre) —
# réutilisable par tout futur écran ayant besoin d'un résumé chiffré/
# textuel (ex. UI-206).


@register.inclusion_tag("ui/components/stat_card.html")
def corrux_stat_card(label, value):
    """Carte de synthèse — un label et une valeur, rien d'autre.

    Aucune permission, aucune action, aucune donnée en dur : la valeur
    est entièrement fournie par l'appelant (déjà mise en forme si
    nécessaire, ex. une date déjà formatée ou un fragment de badge
    HTML de confiance déjà rendu).
    """
    return {"label": label, "value": value}


@register.inclusion_tag("ui/components/toggle_cell.html")
def corrux_toggle_cell(hidden_fields, granted, toggle_url, label):
    """Cellule togglable générique — introduit en UI-202 (matrice de
    permissions de rôle), généralisé pour être réutilisé tel quel par
    UI-304 (permissions document/dossier) : même mécanisme exact
    (formulaire minimal par cellule, zéro JavaScript, aller-retour
    serveur), seuls les champs cachés transmis diffèrent selon
    l'appelant — pas un second composant quasi identique créé.

    `hidden_fields` : dict `{nom: valeur}` des champs cachés à
    transmettre (ex. `{"role_id": ..., "module_id": ...}` pour la
    matrice de rôle, ou `{"document_id": ..., "user_id": ...,
    "action": ...}` pour une permission document). `granted` détermine
    uniquement le rendu visuel/accessible ; la décision réelle
    (créer/supprimer la permission) est prise côté serveur par la vue
    cible, jamais ici. `label` : libellé accessible de la bascule (ex.
    "core.audit.read" ou "Jean Dupont — lecture")."""
    return {
        "hidden_fields": hidden_fields,
        "granted": granted,
        "toggle_url": toggle_url,
        "label": label,
    }


@register.inclusion_tag("ui/components/breadcrumb.html")
def corrux_breadcrumb(items):
    """Fil d'Ariane — UI-301.

    `items` : liste de tuples (label, url). Le dernier élément (position
    actuelle) n'est jamais un lien même si une url est fournie — un seul
    composant, réutilisable par tout futur écran à arborescence (ex.
    future navigation RH)."""
    return {"items": items}


@register.inclusion_tag("ui/components/content_modal.html")
def corrux_content_modal(modal_id, title, content, close_label="Fermer", open=False):  # noqa: A002
    """Modal de contenu riche — UI-304.

    Troisième variante de la famille Modal, aux côtés de corrux_modal
    (UI-204, confirmation destructive : Annuler + action) et
    corrux_info_modal (UI-203, information à liste, une seule sortie) :
    un seul bouton de sortie comme corrux_info_modal, mais `content`
    est un fragment HTML de confiance déjà rendu par l'appelant (comme
    corrux_drawer) — permet d'y inclure ses propres formulaires
    internes (ex. les cellules togglables de gestion des permissions,
    chacune son propre aller-retour serveur, indépendant du bouton
    Fermer)."""
    return {
        "modal_id": modal_id,
        "title": title,
        "content": content,
        "close_label": close_label,
        "open": open,
    }


@register.inclusion_tag("ui/components/inline_action_form.html")
def corrux_inline_action_form(hidden_fields, action_url, label, variant="secondary"):
    """Formulaire minimal à un seul bouton — UI-304.

    Action serveur ponctuelle et à sens unique (ex. retirer un accès),
    avec des champs cachés, zéro JavaScript. Distinct de
    corrux_toggle_cell (état on/off visuel persistant) : ici il n'y a
    pas d'état "activé"/"désactivé" à représenter, seulement une
    action déclenchée une fois."""
    return {
        "hidden_fields": hidden_fields,
        "action_url": action_url,
        "label": label,
        "variant": variant,
    }
