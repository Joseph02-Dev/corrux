"""Tests des états communs — UI-104.

Cf. maquettes-ui-v1-lot1.md planche 9, ux-ui-design-v1.md §7. Rendu réel
(même patron que UI-101/102/103), pas de simple présence de chaîne.
"""

from django.template import engines


def _render(snippet: str, context: dict | None = None) -> str:
    template = engines["django"].from_string("{% load ui_tags %}" + snippet)
    return template.render(context or {})


class TestLoadingSkeleton:
    def test_default_renders_three_rows(self):
        html = _render("{% corrux_loading_skeleton %}")
        assert html.count("corrux-loading-skeleton__row") == 3

    def test_custom_row_count_is_respected(self):
        html = _render("{% corrux_loading_skeleton rows=5 %}")
        assert html.count("corrux-loading-skeleton__row") == 5

    def test_zero_rows_renders_empty_container_without_error(self):
        html = _render("{% corrux_loading_skeleton rows=0 %}")
        assert "corrux-loading-skeleton__row" not in html
        assert "corrux-loading-skeleton" in html

    def test_has_accessible_status_role_and_label(self):
        html = _render("{% corrux_loading_skeleton %}")
        assert 'role="status"' in html
        assert 'aria-label="Chargement en cours"' in html

    def test_rows_are_decorative_and_hidden_from_screen_readers(self):
        html = _render("{% corrux_loading_skeleton rows=2 %}")
        # Chaque ligne décorative porte aria-hidden ; seul le conteneur
        # porte l'annonce accessible (pas de répétition parasite).
        assert html.count('aria-hidden="true"') == 2

    def test_does_not_duplicate_the_existing_button_spinner_mechanism(self):
        """Non-duplication explicite : le spinner de bouton (UI-101) et
        le skeleton (UI-104) sont deux mécanismes distincts."""
        html = _render("{% corrux_loading_skeleton %}")
        assert "corrux-button__spinner" not in html
        assert "corrux-button--loading" not in html

    def test_no_django_comment_leaks_into_output(self):
        html = _render("{% corrux_loading_skeleton %}")
        assert "{#" not in html
        assert "{% comment %}" not in html


class TestEmptyState:
    def test_renders_message_only_without_action(self):
        html = _render('{% corrux_empty_state message="Aucun document" %}')
        assert "Aucun document" in html
        assert "corrux-button" not in html  # aucun CTA sans action fournie

    def test_renders_primary_cta_when_action_provided(self):
        html = _render(
            '{% corrux_empty_state message="Aucun document" '
            'action_label="Déposer un document" action_href="/documents/nouveau/" %}'
        )
        assert "corrux-button--primary" in html
        assert 'href="/documents/nouveau/"' in html
        assert "Déposer un document" in html

    def test_action_label_alone_without_href_renders_no_button(self):
        """Jamais un bouton non fonctionnel : les deux paramètres sont requis ensemble."""
        html = _render(
            '{% corrux_empty_state message="Aucun document" action_label="Déposer" %}'
        )
        assert "corrux-button" not in html

    def test_optional_icon_is_rendered_when_provided(self):
        html = _render('{% corrux_empty_state message="Aucun document" icon="folder" %}')
        assert "<svg" in html

    def test_no_icon_by_default(self):
        html = _render('{% corrux_empty_state message="Aucun document" %}')
        assert "<svg" not in html

    def test_message_is_html_escaped(self):
        html = _render(
            '{% corrux_empty_state message=message %}',
            {"message": "<script>alert(1)</script>"},
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestErrorState:
    def test_renders_message_only_without_action(self):
        html = _render('{% corrux_error_state message="Une erreur est survenue." %}')
        assert "Une erreur est survenue." in html
        assert "corrux-button" not in html

    def test_renders_secondary_retry_action_when_provided(self):
        html = _render(
            '{% corrux_error_state message="Erreur." '
            'action_label="Réessayer" action_href="/page-actuelle/" %}'
        )
        assert "corrux-button--secondary" in html
        assert 'href="/page-actuelle/"' in html
        assert "Réessayer" in html

    def test_has_alert_role_for_immediate_announcement(self):
        html = _render('{% corrux_error_state message="Erreur." %}')
        assert 'role="alert"' in html

    def test_action_href_alone_without_label_renders_no_button(self):
        html = _render('{% corrux_error_state message="Erreur." action_href="/x/" %}')
        assert "corrux-button" not in html

    def test_message_is_html_escaped(self):
        html = _render(
            "{% corrux_error_state message=message %}",
            {"message": "<img src=x onerror=alert(1)>"},
        )
        assert "<img" not in html
        assert "&lt;img" in html


class TestPermissionDenied:
    def test_renders_default_generic_message(self):
        html = _render("{% corrux_permission_denied %}")
        assert "n&#x27;avez pas la permission" in html or "n'avez pas la permission" in html

    def test_default_message_does_not_leak_a_specific_permission_code(self):
        html = _render("{% corrux_permission_denied %}")
        # Le message générique par défaut ne doit jamais contenir un code
        # de permission au format module.resource.action.
        assert "module_id" not in html
        assert "documentation." not in html
        assert "rh." not in html
        assert "core." not in html

    def test_custom_message_is_rendered_when_provided(self):
        html = _render(
            '{% corrux_permission_denied message="Accès réservé aux administrateurs." %}'
        )
        assert "Accès réservé aux administrateurs." in html

    def test_never_renders_any_action_button(self):
        """Spec explicite : sans action, jamais — même si on essaie de
        forcer un paramètre inexistant, le composant n'expose aucune
        possibilité d'ajouter un bouton."""
        html = _render("{% corrux_permission_denied %}")
        assert "corrux-button" not in html
        assert "<a " not in html

    def test_has_alert_role(self):
        html = _render("{% corrux_permission_denied %}")
        assert 'role="alert"' in html

    def test_message_is_html_escaped(self):
        html = _render(
            "{% corrux_permission_denied message=message %}",
            {"message": "<script>alert(1)</script>"},
        )
        assert "<script>" not in html


class TestButtonHrefExtension:
    """Extension rétrocompatible de corrux_button (UI-104)."""

    def test_button_without_href_still_renders_a_button_element(self):
        html = _render('{% corrux_button label="Enregistrer" %}')
        assert "<button" in html
        assert "<a " not in html

    def test_button_with_href_renders_an_anchor_styled_as_button(self):
        html = _render('{% corrux_button label="Réessayer" href="/retry/" %}')
        assert "<a" in html
        assert "</a>" in html
        assert 'href="/retry/"' in html
        assert "corrux-button--primary" in html
        assert "<button" not in html

    def test_existing_usages_without_href_are_unaffected(self):
        """Non-régression explicite : tout usage existant de corrux_button
        (UI-101/102/103) omet `href` et doit continuer à produire un
        <button>, jamais un <a>."""
        for variant in ("primary", "secondary", "tertiary", "danger"):
            html = _render(f'{{% corrux_button label="X" variant="{variant}" %}}')
            assert "<button" in html
