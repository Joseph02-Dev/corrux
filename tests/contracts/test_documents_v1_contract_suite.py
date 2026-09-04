"""Suite de tests de contrat inter-modules — documents.v1 — TECH-041.

Consolide en un point de référence unique la vérification du contrat
`documents.v1` (TECH-024) :
- Documentation le respecte indépendamment de RH
  (tests/contracts/test_documents_v1.py, déjà existant, exerce le
  comportement réel — attach/get/list_for_owner fonctionnels, sans
  aucun code RH impliqué).
- RH ne s'appuie que sur ce contrat exact
  (tests/contracts/test_rh_documents_v1_integration.py, déjà existant,
  côté consommateur).

Valeur ajoutée propre à ce ticket, pas déjà couverte ailleurs par ces
deux suites : une vérification EXPLICITE de la SIGNATURE du contrat
(noms de fonctions, noms de paramètres, structure de DocumentMeta) —
critère d'acceptation explicite du ticket : « la suite échoue si le
contrat change sans adaptation côté RH ». Les tests de comportement
existants (TECH-024/034) échoueraient déjà indirectement si la
signature changeait (RH n'importerait plus les bons noms, une erreur
d'import ferait échouer toute sa suite) — cette suite le vérifie
DIRECTEMENT par introspection, sans dépendre d'un effet de bord d'un
autre fichier de test, et documente explicitement la forme exacte du
contrat en un seul endroit lisible.

Chevauchement partiel avec les tests existants, signalé pas caché :
même logique que TECH-040 — ce n'est pas une duplication accidentelle
mais l'objet même de ce ticket (« consolider »). Aucun test existant
supprimé.
"""

import ast
import dataclasses
import inspect

from modules.documentation import documents_v1
from modules.rh import services as rh_services

# Le contrat déclaré — unique source de vérité pour cette suite. Toute
# modification de cette liste doit être délibérée (une évolution réelle
# du contrat), jamais un ajustement silencieux pour faire passer un
# test après une régression.
DECLARED_CONTRACT_NAMES = frozenset(
    {"attach", "get", "list_for_owner", "DocumentMeta", "DocumentNotAccessibleError"}
)


class TestContractSignature:
    """Documente et vérifie la forme exacte du contrat documents.v1 —
    au-delà de ce que les tests de comportement (TECH-024) exercent
    déjà."""

    def test_attach_signature(self):
        params = list(inspect.signature(documents_v1.attach).parameters)
        assert params == ["content", "filename", "owner_user", "folder", "category"]

    def test_get_signature(self):
        params = list(inspect.signature(documents_v1.get).parameters)
        assert params == ["document_ref", "requesting_user"]

    def test_list_for_owner_signature(self):
        """Signature issue de la résolution d'ambiguïté Option A
        (TECH-024) : document_refs pré-résolus par l'appelant, pas
        owner_module/owner_ref — Documentation ne connaît jamais la
        relation employé<->document, qu'elle ne doit jamais posséder."""
        params = list(inspect.signature(documents_v1.list_for_owner).parameters)
        assert params == ["document_refs", "requesting_user"]

    def test_document_meta_has_the_expected_fields(self):
        fields = {f.name for f in dataclasses.fields(documents_v1.DocumentMeta)}
        assert fields == {
            "document_ref", "filename", "mime_type", "size_bytes", "created_at",
        }

    def test_document_meta_is_frozen(self):
        """Immutabilité déjà décidée (TECH-024) — une métadonnée
        renvoyée par le contrat ne doit jamais pouvoir être modifiée
        après coup par l'appelant."""
        assert documents_v1.DocumentMeta.__dataclass_params__.frozen is True

    def test_document_not_accessible_error_is_an_exception(self):
        assert issubclass(documents_v1.DocumentNotAccessibleError, Exception)


class TestRhOnlyUsesTheDeclaredContract:
    """RH n'importe que ce que documents_v1 expose réellement.

    Si le contrat renommait ou retirait un nom que RH utilise, cet
    import échouerait immédiatement (ImportError), avant même
    d'exécuter le moindre test de comportement — critère d'acceptation
    explicite du ticket vérifié directement, pas supposé.
    """

    def test_rh_services_imports_only_declared_contract_names(self):
        source = inspect.getsource(rh_services)
        tree = ast.parse(source)

        imported_from_documents_v1 = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "modules.documentation.documents_v1"
            ):
                imported_from_documents_v1.update(alias.name for alias in node.names)

        assert imported_from_documents_v1, (
            "modules/rh/services.py devrait importer au moins un nom de "
            "documents_v1 (TECH-034) — si cette liste est vide, soit RH "
            "n'utilise plus le contrat, soit l'import a changé de forme "
            "et cette vérification doit être adaptée, pas contournée."
        )
        assert imported_from_documents_v1 <= DECLARED_CONTRACT_NAMES

    def test_rh_never_imports_documentation_internal_modules_directly(self):
        """Contrainte architecturale impérative (§15) — RH ne doit
        jamais importer autre chose que la façade documents_v1 depuis
        Documentation."""
        source = inspect.getsource(rh_services)
        tree = ast.parse(source)

        documentation_imports = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("modules.documentation")
            ):
                documentation_imports.add(node.module)

        assert documentation_imports == {"modules.documentation.documents_v1"}
