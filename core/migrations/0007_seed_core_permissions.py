"""Sème les permissions Platform Core (core.*) — TECH-012 (audit Phase 1).

Découverte lors de l'audit du ticket TECH-012 (corrux-setup) : les
permissions `core.*` (gestion des utilisateurs, rôles, modules,
sauvegardes, journal d'audit) sont vérifiées dans le code des écrans
d'administration (UI-201 à UI-206) mais n'étaient enregistrées nulle
part — seules les permissions déclarées par le manifeste d'un module
MÉTIER (Documentation, RH) sont enregistrées, via
core/modules/manager.py::install_module(). Platform Core n'a pas de
« manifeste » propre, donc jamais rien n'enregistrait ses permissions.

Conséquence corrigée ici : sans cette migration, aucune permission
core.* ne pouvait jamais être attribuée à aucun rôle, sur aucune
installation — le code de gestion des utilisateurs/rôles/modules/
sauvegardes/audit était correct, mais structurellement inaccessible à
quiconque.

Liste exhaustive des permissions core.* réellement vérifiées quelque
part dans le code (recherche exhaustive avant d'écrire cette
migration, pas une liste supposée) : core.user.read, core.user.write,
core.module.read, core.module.write, core.role.write, core.backup.read,
core.audit.read.

Idempotente (get_or_create), même patron que la migration 0006 (seed
des rôles prédéfinis) : ne supprime, ne renomme ni n'écrase jamais une
permission existante, y compris en cas de ré-exécution.
"""

from django.db import migrations

CORE_PERMISSIONS = [
    ("user", "read"),
    ("user", "write"),
    ("module", "read"),
    ("module", "write"),
    ("role", "write"),
    ("backup", "read"),
    ("audit", "read"),
]


def seed_core_permissions(apps, schema_editor):
    Permission = apps.get_model("core", "Permission")
    for resource, action in CORE_PERMISSIONS:
        Permission.objects.get_or_create(module_id="core", resource=resource, action=action)


def noop_reverse(apps, schema_editor):
    """Ne supprime jamais les permissions à la baisse : elles peuvent
    déjà être assignées à des rôles réels au moment d'un reverse."""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_seed_predefined_roles"),
    ]

    operations = [
        migrations.RunPython(seed_core_permissions, noop_reverse),
    ]
