from django.urls import path

from ui import views

urlpatterns = [
    path("design-system/", views.design_system_showcase, name="ui-design-system-showcase"),
]
