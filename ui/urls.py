from django.urls import path

from ui import views

urlpatterns = [
    path("design-system/", views.design_system_showcase, name="ui-design-system-showcase"),
    path("shell-demo/", views.shell_showcase, name="ui-shell-showcase"),
    path("login/", views.login_page, name="ui-login"),
]
