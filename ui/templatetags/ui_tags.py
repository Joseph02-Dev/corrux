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
}


@register.inclusion_tag("ui/components/button.html")
def corrux_button(label, variant="primary", type="button", disabled=False, loading=False):  # noqa: A002
    """Bouton — variantes primaire/secondaire/tertiaire/danger.

    États disabled/loading gérés côté serveur (rendu initial) ; le
    basculement dynamique loading<->défaut appartient au JS d'un écran
    consommateur (hors périmètre UI-101, aucun JS de composant ici).
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
):
    """Champ de formulaire (texte) — label, aide, validation inline.

    `error` et `success` sont mutuellement exclusifs (erreur prioritaire
    si les deux sont fournis) ; à défaut, `help_text` s'affiche.
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
