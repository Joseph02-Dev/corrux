from django.urls import path

from ui import views

urlpatterns = [
    path("design-system/", views.design_system_showcase, name="ui-design-system-showcase"),
    path("shell-demo/", views.shell_showcase, name="ui-shell-showcase"),
    path("login/", views.login_page, name="ui-login"),
    path("profil/", views.profile_page, name="ui-profile"),
    path("deconnexion/", views.logout_action, name="ui-logout"),
    path("utilisateurs/", views.user_list, name="ui-user-list"),
    path("utilisateurs/nouveau/", views.user_create, name="ui-user-create"),
    path("utilisateurs/<int:user_id>/modifier/", views.user_edit, name="ui-user-edit"),
    path(
        "utilisateurs/<int:user_id>/reinitialiser-mot-de-passe/",
        views.user_reset_password,
        name="ui-user-reset-password",
    ),
    path(
        "utilisateurs/<int:user_id>/desactiver/",
        views.user_deactivate,
        name="ui-user-deactivate",
    ),
]
