"""URLconf isolé, utilisé uniquement par tests/core/test_authz_engine.py
(via pytest.mark.urls) pour exercer require_permission sur une route HTTP
réelle, sans ajouter de route factice au routage applicatif réel."""

from django.http import JsonResponse
from django.urls import path

from core.authz.decorators import require_permission


@require_permission("test.module.lire")
def dummy_protected_view(request):
    return JsonResponse({"detail": "accès autorisé"})


urlpatterns = [
    path("dummy-protected/", dummy_protected_view, name="dummy-protected"),
]
