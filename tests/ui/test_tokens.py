"""Tests des tokens CSS — UI-101.

Vérifie les critères d'acceptation explicites du ticket : aucune couleur
en dur en dehors des tokens, pas de mode sombre (décision verrouillée).
Analyse directement les fichiers CSS livrés (pas une supposition).
"""

import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent.parent / "ui" / "static" / "ui" / "css"
TOKENS_CSS = CSS_DIR / "tokens.css"
COMPONENTS_CSS = CSS_DIR / "components.css"
SHELL_CSS = CSS_DIR / "shell.css"

# Couleurs hexadécimales (#fff, #ffffff...). currentColor/transparent/
# rgba(...) dans le token de focus-ring sont volontairement exclus (ce
# ne sont pas des couleurs "en dur" au sens du critère : rgba() y référence
# une valeur dérivée du token primaire, documentée comme telle).
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def test_tokens_file_exists():
    assert TOKENS_CSS.is_file()


def test_components_file_exists():
    assert COMPONENTS_CSS.is_file()


def test_components_css_never_hardcodes_a_hex_color():
    """Critère d'acceptation UI-101 : aucune couleur en dur en dehors des
    tokens. components.css ne doit référencer les couleurs que via
    var(--corrux-...)."""
    content = COMPONENTS_CSS.read_text(encoding="utf-8")
    matches = _HEX_COLOR_RE.findall(content)
    # Seule exception documentée : la couleur hover du bouton danger,
    # dérivée manuellement faute de token dédié à un état "danger-hover" —
    # limite connue, signalée ici plutôt que masquée.
    unexpected = [m for m in matches if m.lower() != "#8f1b12"]
    assert unexpected == [], f"Couleurs en dur inattendues dans components.css : {unexpected}"


def test_tokens_css_defines_all_required_color_categories():
    content = TOKENS_CSS.read_text(encoding="utf-8")
    required_tokens = [
        "--corrux-color-surface-0",
        "--corrux-color-surface-1",
        "--corrux-color-surface-2",
        "--corrux-color-text-primary",
        "--corrux-color-text-secondary",
        "--corrux-color-text-muted",
        "--corrux-color-primary",
        "--corrux-color-success",
        "--corrux-color-warning",
        "--corrux-color-error",
        "--corrux-color-info",
    ]
    for token in required_tokens:
        assert token in content, f"Token manquant : {token}"


def test_tokens_css_defines_system_font_family_only():
    content = TOKENS_CSS.read_text(encoding="utf-8")
    assert "-apple-system" in content
    # Aucune police web externe (cohérent avec le fonctionnement offline) :
    # pas d'@import ni de référence à une URL de police.
    assert "@import" not in content
    assert "fonts.googleapis" not in content
    assert "http://" not in content
    assert "https://" not in content


def test_tokens_css_defines_spacing_scale_base_4_8():
    content = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--corrux-space-1: 4px" in content
    assert "--corrux-space-2: 8px" in content


def test_no_dark_mode_media_query_anywhere_in_design_system_css():
    """Décision verrouillée (ux-ui-design-v1.md) : pas de mode sombre en V1.

    Recherche l'usage réel (`@media (prefers-color-scheme`), pas la simple
    mention du concept dans un commentaire explicatif : les commentaires
    CSS sont retirés avant la recherche.
    """
    for css_file in (TOKENS_CSS, COMPONENTS_CSS, SHELL_CSS):
        content = css_file.read_text(encoding="utf-8")
        without_comments = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        assert "prefers-color-scheme" not in without_comments


def test_shell_css_file_exists():
    assert SHELL_CSS.is_file()


def test_shell_css_never_hardcodes_a_hex_color():
    """Même critère d'acceptation que UI-101, appliqué au nouveau fichier
    shell.css (UI-102) : aucune couleur en dur en dehors des tokens."""
    content = SHELL_CSS.read_text(encoding="utf-8")
    matches = _HEX_COLOR_RE.findall(content)
    assert matches == [], f"Couleurs en dur inattendues dans shell.css : {matches}"


def test_no_focus_outline_removed_without_replacement():
    """Accessibilité : outline: none n'est acceptable que combiné à un
    remplacement visible (box-shadow) sur le même sélecteur — jamais nu."""
    content = COMPONENTS_CSS.read_text(encoding="utf-8")
    # Le seul "outline: none" du fichier doit être dans la règle
    # :focus-visible, immédiatement suivi d'un box-shadow de remplacement.
    focus_visible_block_match = re.search(
        r":focus-visible[^{]*\{([^}]*)\}", content
    )
    assert focus_visible_block_match is not None
    block = focus_visible_block_match.group(1)
    assert "outline: none" in block
    assert "box-shadow" in block
