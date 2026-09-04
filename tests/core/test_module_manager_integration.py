"""Suite de tests d'intégration Module Manager — TECH-040.

Consolide en un seul endroit les 4 scénarios explicitement requis par
§18 (architecture-technique-v1.md) et repris directement des critères
d'acceptation produit (vision-produit-v1.md §8/§13) : installation OK,
installation refusée (dépendance manquante), activation refusée,
désactivation bloquée si dépendant actif. Utilise les VRAIS manifestes
Documentation/RH (chargés depuis le disque) — critère d'acceptation
explicite du ticket : « les 4 scénarios passent avec Documentation et
RH réels (pas de mock) ».

Terminologie du scénario 2 : §18/TECH-040 disent littéralement
« installation refusée (dépendance manquante) », mais la vérification
des dépendances a lieu à l'ACTIVATION, pas à l'installation — décision
déjà documentée et résolue dans core/modules/manager.py (tête de
fichier, TECH-006), pas remise en cause ici. Le scénario réel testé
(activation refusée pour une dépendance manquante) correspond
exactement au critère d'acceptation produit réel (vision-produit-v1.md
§8 : « tenter d'activer le module RH sans Documentation installé et
activé échoue »).

Chevauchement partiel avec les tests existants, signalé pas caché :
ces 4 scénarios recoupent substantiellement des tests déjà présents
dans tests/core/test_rh_manifest.py (TECH-035) et
tests/core/test_documentation_manifest.py (TECH-025). Ce n'est pas une
duplication accidentelle mais l'objet même de ce ticket
(« consolider... en une suite consolidée ») : cette suite devient le
point de référence unique pour ces 4 scénarios précis, en une seule
lecture, sans avoir à les chercher dans plusieurs fichiers. Aucun test
existant n'est supprimé (prudence délibérée, pas une décision prise
sans confirmation).
"""

from pathlib import Path

import pytest

from core.identity.models import User
from core.modules.manager import (
    ActiveDependentError,
    DependencyError,
    activate_module,
    deactivate_module,
    install_module,
)
from core.modules.manifest import parse_manifest_file
from core.modules.models import Module

DOCUMENTATION_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "modules"
    / "documentation"
    / "manifest.yaml"
)
RH_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent.parent / "modules" / "rh" / "manifest.yaml"
)


@pytest.fixture
def actor(db):
    return User.objects.create(username="technicien_tech040", full_name="Technicien")


def _install_and_activate_documentation(actor):
    manifest = parse_manifest_file(DOCUMENTATION_MANIFEST_PATH)
    install_module(manifest, actor=actor)
    return activate_module("documentation", actor=actor)


def _install_rh(actor):
    manifest = parse_manifest_file(RH_MANIFEST_PATH)
    return install_module(manifest, actor=actor)


# --- Scénario 1 : installation OK ------------------------------------------------


@pytest.mark.django_db
class TestScenario1InstallationOk:
    def test_documentation_installs_successfully(self, actor):
        """Documentation n'a aucune dépendance — installation directe,
        sans aucune condition préalable."""
        manifest = parse_manifest_file(DOCUMENTATION_MANIFEST_PATH)
        module = install_module(manifest, actor=actor)
        assert module.state == Module.State.INSTALLED
        assert Module.objects.filter(pk="documentation").exists()

    def test_rh_installs_successfully_even_without_documentation(self, actor):
        """L'installation ne vérifie aucune dépendance (seule
        l'activation le fait, décision documentée dans manager.py) —
        RH s'installe même si Documentation n'existe pas du tout en
        base, condition nécessaire au scénario 2 ci-dessous."""
        assert not Module.objects.filter(pk="documentation").exists()

        module = _install_rh(actor)

        assert module.state == Module.State.INSTALLED


# --- Scénarios 2 et 3 : activation refusée pour dépendance manquante -----------
# (§18/TECH-040 nomment ces deux scénarios séparément — « installation
# refusée (dépendance manquante) » et « activation refusée » — mais il
# s'agit de la même mécanique réelle : activate_module() est l'unique
# point de vérification des dépendances (décision déjà documentée dans
# core/modules/manager.py, TECH-006). Regroupés en une seule classe
# plutôt que dupliqués artificiellement.


@pytest.mark.django_db
class TestScenarios2And3ActivationRefusedForMissingDependency:
    def test_activating_rh_without_documentation_installed_fails(self, actor):
        """Cas explicitement demandé par le produit : Documentation
        totalement absent (jamais installé)."""
        _install_rh(actor)

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

    def test_activating_rh_with_documentation_installed_but_not_activated_fails(
        self, actor
    ):
        """Deuxième sous-cas : Documentation installé mais jamais
        activé — la vérification exige « installé ET activé », pas
        seulement installé (§14)."""
        documentation_manifest = parse_manifest_file(DOCUMENTATION_MANIFEST_PATH)
        install_module(documentation_manifest, actor=actor)
        _install_rh(actor)

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

    def test_refused_activation_leaves_rh_not_activated_and_atomic(self, actor):
        """Aucun état partiel n'est écrit — critère d'acceptation
        explicite de TECH-006 (§14), revérifié ici avec le cas réel."""
        _install_rh(actor)

        with pytest.raises(DependencyError):
            activate_module("rh", actor=actor)

        assert Module.objects.get(pk="rh").state != Module.State.ACTIVATED

    def test_error_message_explicitly_names_the_missing_dependency(self, actor):
        """Message explicite — critère d'acceptation produit (§8) :
        « échoue avec un message explicite »."""
        _install_rh(actor)

        with pytest.raises(DependencyError) as excinfo:
            activate_module("rh", actor=actor)

        assert "documentation" in str(excinfo.value).lower()


# --- Scénario 4 : désactivation bloquée si dépendant actif ---------------------


@pytest.mark.django_db
class TestScenario4DeactivationBlockedIfDependentActive:
    def test_deactivating_documentation_is_blocked_while_rh_is_active(self, actor):
        _install_and_activate_documentation(actor)
        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)
        activate_module("rh", actor=actor)

        with pytest.raises(ActiveDependentError):
            deactivate_module("documentation", actor=actor)

    def test_documentation_remains_active_after_blocked_deactivation(self, actor):
        _install_and_activate_documentation(actor)
        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)
        activate_module("rh", actor=actor)

        with pytest.raises(ActiveDependentError):
            deactivate_module("documentation", actor=actor)

        assert Module.objects.get(pk="documentation").state == Module.State.ACTIVATED

    def test_deactivating_documentation_succeeds_once_rh_is_deactivated_first(
        self, actor
    ):
        """Contrôle négatif : la désactivation devient possible une
        fois le dépendant lui-même désactivé — la règle porte
        précisément sur « dépendant actif », pas sur « dépendant
        installé »."""
        _install_and_activate_documentation(actor)
        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)
        activate_module("rh", actor=actor)

        deactivate_module("rh", actor=actor)
        module = deactivate_module("documentation", actor=actor)

        assert module.state == Module.State.DEACTIVATED

    def test_error_message_explicitly_names_the_active_dependent(self, actor):
        _install_and_activate_documentation(actor)
        rh_manifest = parse_manifest_file(RH_MANIFEST_PATH)
        install_module(rh_manifest, actor=actor)
        activate_module("rh", actor=actor)

        with pytest.raises(ActiveDependentError) as excinfo:
            deactivate_module("documentation", actor=actor)

        assert "rh" in str(excinfo.value).lower()
