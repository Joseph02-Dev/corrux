"""Routes racine de corrux-core."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def healthz(request):
    """Route de healthcheck minimale (aucune permission, aucune dépendance DB).

    Sert au smoke test INIT-001 : « le projet démarre en local et répond ».
    """
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
]
