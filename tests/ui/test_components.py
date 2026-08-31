"""Tests de rendu des composants du Design System — UI-101.

Tests de rendu réel (render_to_string), pas de simple test de
compilation : vérifie les classes CSS, attributs ARIA, et sémantique
HTML produits.
"""

import pytest
from django.template import engines


def _render(snippet: str, context: dict | None = None) -> str:
    template = engines["django"].from_string("{% load ui_tags %}" + snippet)
    return template.render(context or {})


class TestButtonComponent:
    def test_renders_primary_button_by_default(self):
        html = _render('{% corrux_button label="Enregistrer" %}')
        assert 'class="corrux-button corrux-button--primary"' in html
        assert "Enregistrer" in html
        assert 'type="button"' in html

    def test_renders_each_documented_variant(self):
        for variant in ("primary", "secondary", "tertiary", "danger"):
            html = _render(f'{{% corrux_button label="X" variant="{variant}" %}}')
            assert f"corrux-button--{variant}" in html

    def test_invalid_variant_raises_explicitly(self):
        with pytest.raises(ValueError):
            _render('{% corrux_button label="X" variant="inexistant" %}')

    def test_disabled_state_sets_disabled_and_aria(self):
        html = _render('{% corrux_button label="X" disabled=True %}')
        assert "disabled" in html
        assert 'aria-disabled="true"' in html

    def test_loading_state_sets_aria_busy_and_spinner(self):
        html = _render('{% corrux_button label="X" loading=True %}')
        assert 'aria-busy="true"' in html
        assert "corrux-button--loading" in html
        assert "corrux-button__spinner" in html
        # Le texte du bouton reste présent pour un lecteur d'écran.
        assert "Chargement en cours" in html

    def test_submit_type_is_supported(self):
        html = _render('{% corrux_button label="Valider" type="submit" %}')
        assert 'type="submit"' in html


class TestFieldComponent:
    def test_renders_label_linked_to_input_via_for_id(self):
        html = _render(
            '{% corrux_field label="Identifiant" name="username" '
            'field_id="id_username" %}'
        )
        assert 'for="id_username"' in html
        assert 'id="id_username"' in html
        assert 'name="username"' in html

    def test_default_field_id_derived_from_name(self):
        html = _render('{% corrux_field label="X" name="email" %}')
        assert 'id="id_email"' in html

    def test_required_field_has_required_attribute_and_marker(self):
        html = _render('{% corrux_field label="X" name="y" required=True %}')
        assert "required" in html
        assert 'aria-required="true"' in html
        assert "corrux-field__required" in html

    def test_error_state_sets_aria_invalid_and_describedby(self):
        html = _render(
            '{% corrux_field label="X" name="y" field_id="id_y" '
            'error="Champ invalide." %}'
        )
        assert 'aria-invalid="true"' in html
        assert 'aria-describedby="id_y-message"' in html
        assert "Champ invalide." in html
        assert "corrux-field--error" in html

    def test_success_state_renders_success_class_and_message(self):
        html = _render(
            '{% corrux_field label="X" name="y" success="Disponible." %}'
        )
        assert "corrux-field--success" in html
        assert "Disponible." in html

    def test_error_takes_precedence_over_success(self):
        html = _render(
            '{% corrux_field label="X" name="y" error="Erreur." success="OK." %}'
        )
        assert "Erreur." in html
        assert "OK." not in html

    def test_help_text_shown_when_no_error_or_success(self):
        html = _render('{% corrux_field label="X" name="y" help_text="Aide." %}')
        assert "Aide." in html
        assert "corrux-field__help" in html

    def test_password_input_type_is_supported(self):
        html = _render(
            '{% corrux_field label="Mot de passe" name="password" input_type="password" %}'
        )
        assert 'type="password"' in html


class TestBadgeComponent:
    def test_renders_each_semantic_tone(self):
        for tone in ("neutral", "success", "warning", "error", "info"):
            html = _render(f'{{% corrux_badge label="X" tone="{tone}" %}}')
            assert f"corrux-badge--{tone}" in html

    def test_invalid_tone_raises_explicitly(self):
        with pytest.raises(ValueError):
            _render('{% corrux_badge label="X" tone="inexistant" %}')

    def test_default_tone_is_neutral(self):
        html = _render('{% corrux_badge label="Brouillon" %}')
        assert "corrux-badge--neutral" in html


class TestIconComponent:
    def test_renders_known_icon(self):
        html = _render('{% corrux_icon name="check" %}')
        assert "<svg" in html
        assert 'viewBox="0 0 24 24"' in html

    def test_unknown_icon_raises_explicitly(self):
        with pytest.raises(ValueError):
            _render('{% corrux_icon name="does-not-exist" %}')

    def test_decorative_icon_is_aria_hidden_by_default(self):
        html = _render('{% corrux_icon name="check" %}')
        assert 'aria-hidden="true"' in html
        assert "role=" not in html

    def test_labelled_icon_gets_accessible_name_instead_of_hidden(self):
        html = _render('{% corrux_icon name="close" label="Fermer" %}')
        assert 'role="img"' in html
        assert 'aria-label="Fermer"' in html
        assert "<title>Fermer</title>" in html
        assert "aria-hidden" not in html

    def test_small_variant_adds_size_modifier_class(self):
        html = _render('{% corrux_icon name="user" small=True %}')
        assert "corrux-icon--small" in html


class TestNoTemplateCommentLeaksIntoRenderedOutput:
    """Régression : {# ... #} multi-lignes n'est PAS un commentaire valide
    en Django (contrairement à {% comment %}) et fuit tel quel dans le
    HTML rendu — bug réel détecté pendant UI-101 sur les 4 composants."""

    def test_button_output_contains_no_comment_marker(self):
        html = _render('{% corrux_button label="X" %}')
        assert "{#" not in html
        assert "{% comment %}" not in html
        assert "Cf. ux-ui-design-v1.md" not in html

    def test_field_output_contains_no_comment_marker(self):
        html = _render('{% corrux_field label="X" name="y" %}')
        assert "{#" not in html
        assert "{% comment %}" not in html

    def test_badge_output_contains_no_comment_marker(self):
        html = _render('{% corrux_badge label="X" %}')
        assert "{#" not in html
        assert "{% comment %}" not in html

    def test_icon_output_contains_no_comment_marker(self):
        html = _render('{% corrux_icon name="check" %}')
        assert "{#" not in html
        assert "{% comment %}" not in html
        assert "Composant Icône" not in html


class TestShowcasePageRendersAllComponents:
    @pytest.mark.django_db
    def test_showcase_page_renders_without_error(self, client):
        response = client.get("/design-system/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "corrux-button--primary" in content
        assert "corrux-badge--success" in content
        assert "corrux-field__input" in content
        assert "<svg" in content
