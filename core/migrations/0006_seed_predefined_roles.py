"""Sème les 4 rôles prédéfinis — UI-201 (Phase 2, décision utilisateur).

Cf. ux-ui-design-v1.md, décision validée #1 : « Rôles prédéfinis pour
l'usage standard : Administrateur, Administrateur RH, Valideur, Employé. »
Noms déjà validés par la spécification, pas une invention de ce ticket.

Exception explicitement autorisée par le contrat UI-201 (Phase 2) à
l'interdiction générale de modifier core/ : aucune autre solution ne
permet de peupler core.roles, propriété du modèle Role (core.authz).
Idempotente (get_or_create) : ne supprime, ne renomme ni n'écrase jamais
un rôle existant, y compris en cas de ré-exécution.
"""

from django.db import migrations

PREDEFINED_ROLE_NAMES = [
    "Administrateur",
    "Administrateur RH",
    "Valideur",
    "Employé",
]


def seed_predefined_roles(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    for name in PREDEFINED_ROLE_NAMES:
        Role.objects.get_or_create(name=name)


def noop_reverse(apps, schema_editor):
    """Ne supprime jamais les rôles à la baisse : ils peuvent déjà être
    assignés à des utilisateurs réels au moment d'un reverse."""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_backuprun_alter_auditlog_actor_user"),
    ]

    operations = [
        migrations.RunPython(seed_predefined_roles, noop_reverse),
    ]
