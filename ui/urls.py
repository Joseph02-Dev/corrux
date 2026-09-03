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
    path("utilisateurs/roles/", views.role_list, name="ui-role-list"),
    path("utilisateurs/roles/matrice/", views.role_matrix, name="ui-role-matrix"),
    path(
        "utilisateurs/roles/matrice/toggler/",
        views.role_matrix_toggle,
        name="ui-role-matrix-toggle",
    ),
    path("modules/", views.module_list, name="ui-module-list"),
    path("modules/<str:module_id>/activer/", views.module_activate, name="ui-module-activate"),
    path(
        "modules/<str:module_id>/desactiver/",
        views.module_deactivate,
        name="ui-module-deactivate",
    ),
    path("sauvegardes/", views.backup_list, name="ui-backup-list"),
    path("journal-audit/", views.audit_list, name="ui-audit-list"),
    path("documents/", views.document_explorer, name="ui-document-explorer"),
    path(
        "documents/dossier/<int:folder_id>/",
        views.document_explorer,
        name="ui-document-explorer-folder",
    ),
    path("documents/deposer/", views.document_upload, name="ui-document-upload"),
    path(
        "documents/dossier/<int:folder_id>/deposer/",
        views.document_upload,
        name="ui-document-upload-folder",
    ),
    path("documents/<int:document_id>/", views.document_detail, name="ui-document-detail"),
    path(
        "documents/<int:document_id>/modifier/",
        views.document_edit,
        name="ui-document-edit",
    ),
    path(
        "documents/<int:document_id>/permissions/",
        views.document_permissions,
        name="ui-document-permissions",
    ),
    path(
        "documents/<int:document_id>/permissions/basculer/",
        views.document_permissions_toggle,
        name="ui-document-permissions-toggle",
    ),
    path(
        "documents/<int:document_id>/permissions/retirer/",
        views.document_permissions_remove,
        name="ui-document-permissions-remove",
    ),
    path(
        "documents/<int:document_id>/permissions/ajouter/",
        views.document_permissions_add,
        name="ui-document-permissions-add",
    ),
    path(
        "documents/dossier/<int:folder_id>/permissions/",
        views.folder_permissions,
        name="ui-folder-permissions",
    ),
    path(
        "documents/dossier/<int:folder_id>/permissions/basculer/",
        views.folder_permissions_toggle,
        name="ui-folder-permissions-toggle",
    ),
    path(
        "documents/dossier/<int:folder_id>/permissions/retirer/",
        views.folder_permissions_remove,
        name="ui-folder-permissions-remove",
    ),
    path(
        "documents/dossier/<int:folder_id>/permissions/ajouter/",
        views.folder_permissions_add,
        name="ui-folder-permissions-add",
    ),
]
