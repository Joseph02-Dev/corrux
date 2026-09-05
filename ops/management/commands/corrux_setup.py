"""Assistant d'installation initiale — `manage.py corrux_setup` (§4).

Flux interactif par défaut (prompts), avec des options en ligne de
commande permettant un « scénario scripté » (contrat de test explicite
du ticket) sans simulation d'entrée standard — chaque option CLI
correspond à une question du wizard ; si toutes sont fournies, aucun
prompt n'est affiché (mode non-interactif complet, utilisé par les
tests d'intégration).

Chaque étape valide avant de passer à la suivante (§4 UX) — délègue
entièrement à ops/setup_service.py pour la logique, ce fichier ne fait
que collecter les réponses et confirmer les opérations destructives.
"""

import getpass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.backup.database import DatabaseConnectionParams
from core.certs.service import CertificatePaths
from ops.setup_service import (
    CorruxSetupConfig,
    SetupError,
    detect_candidate_volumes,
    run_corrux_setup,
)


class Command(BaseCommand):
    help = (
        "Assistant d'installation initiale CORRUX (identité, compte admin, "
        "HTTPS, sauvegarde, modules)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--admin-username")
        parser.add_argument("--admin-password")
        parser.add_argument("--admin-full-name")
        parser.add_argument("--certificate-common-name")
        parser.add_argument("--ca-key-path")
        parser.add_argument("--ca-cert-path")
        parser.add_argument("--server-key-path")
        parser.add_argument("--server-cert-path")
        parser.add_argument("--backup-device-path")
        parser.add_argument("--backup-mount-point")
        parser.add_argument("--backup-fstab-path")
        parser.add_argument(
            "--format-backup-volume", action="store_true", default=None,
            help="Formate le volume en ext4 avant montage (opération destructive).",
        )
        parser.add_argument(
            "--skip-format-confirmation", action="store_true",
            help="Ignore la confirmation interactive de formatage (mode scripté uniquement).",
        )
        parser.add_argument("--documentation-manifest-path")
        parser.add_argument("--rh-manifest-path")
        parser.add_argument("--backup-storage-root")
        parser.add_argument("--backup-gpg-recipient-key-path")
        parser.add_argument("--db-name")
        parser.add_argument("--db-user")
        parser.add_argument("--db-password")
        parser.add_argument("--db-host")
        parser.add_argument("--db-port")

    def handle(self, *args, **options):
        self.stdout.write("=== CORRUX — Installation initiale ===\n")

        admin_username = options["admin_username"] or input(
            "Identifiant du compte administrateur : "
        )
        admin_password = options["admin_password"] or getpass.getpass(
            "Mot de passe du compte administrateur : "
        )
        admin_full_name = options["admin_full_name"] or input(
            "Nom complet de l'administrateur : "
        )
        certificate_common_name = options["certificate_common_name"] or input(
            "Nom de la machine (utilisé pour le certificat HTTPS) : "
        )

        candidates = detect_candidate_volumes()
        backup_device_path = options["backup_device_path"]
        if backup_device_path is None:
            if not candidates:
                raise CommandError(
                    "Aucun volume candidat détecté — un support de sauvegarde "
                    "physiquement distinct du disque système est requis (§11.1). "
                    "L'installation ne peut pas se terminer sans support désigné."
                )
            self.stdout.write("Volumes candidats détectés :")
            for candidate in candidates:
                self.stdout.write(
                    f"  {candidate.device_path} ({candidate.size}, "
                    f"fstype={candidate.fstype or 'aucun'})"
                )
            backup_device_path = input(
                "Périphérique à utiliser pour le support de sauvegarde : "
            )

        format_backup_volume = options["format_backup_volume"]
        if format_backup_volume is None:
            answer = input(
                f"Formater {backup_device_path} en ext4 ? Cette opération est "
                f"DESTRUCTIVE et efface toutes les données présentes. (oui/non) : "
            )
            format_backup_volume = answer.strip().lower() == "oui"
        elif format_backup_volume and not options["skip_format_confirmation"]:
            answer = input(
                f"Confirmer le formatage DESTRUCTIF de {backup_device_path} (oui/non) : "
            )
            if answer.strip().lower() != "oui":
                raise CommandError("Formatage non confirmé — installation interrompue.")

        backup_mount_point = Path(
            options["backup_mount_point"]
            or input("Point de montage du support de sauvegarde : ")
        )
        backup_fstab_path = Path(options["backup_fstab_path"] or "/etc/fstab")
        documentation_manifest_path = Path(
            options["documentation_manifest_path"] or "modules/documentation/manifest.yaml"
        )
        rh_manifest_path = Path(options["rh_manifest_path"] or "modules/rh/manifest.yaml")
        backup_storage_root = Path(
            options["backup_storage_root"] or "/var/lib/corrux/storage"
        )
        backup_gpg_recipient_key_path = Path(
            options["backup_gpg_recipient_key_path"] or "/etc/corrux/backup-gpg-public.key"
        )

        config = CorruxSetupConfig(
            admin_username=admin_username,
            admin_password=admin_password,
            admin_full_name=admin_full_name,
            certificate_common_name=certificate_common_name,
            certificate_paths=CertificatePaths(
                ca_key=Path(options["ca_key_path"] or "/etc/corrux/tls/ca.key"),
                ca_cert=Path(options["ca_cert_path"] or "/etc/corrux/tls/ca.crt"),
                server_key=Path(options["server_key_path"] or "/etc/corrux/tls/server.key"),
                server_cert=Path(options["server_cert_path"] or "/etc/corrux/tls/server.crt"),
            ),
            backup_device_path=backup_device_path,
            backup_mount_point=backup_mount_point,
            backup_fstab_path=backup_fstab_path,
            format_backup_volume=format_backup_volume,
            documentation_manifest_path=documentation_manifest_path,
            rh_manifest_path=rh_manifest_path,
            backup_storage_root=backup_storage_root,
            backup_gpg_recipient_key_path=backup_gpg_recipient_key_path,
            backup_db_params=DatabaseConnectionParams(
                name=options["db_name"], user=options["db_user"],
                password=options["db_password"], host=options["db_host"],
                port=options["db_port"],
            ),
        )

        try:
            result = run_corrux_setup(config)
        except SetupError as exc:
            raise CommandError(f"Installation échouée : {exc}") from exc

        self.stdout.write(self.style.SUCCESS("\n=== Installation terminée avec succès ==="))
        self.stdout.write(f"Compte administrateur : {result.admin.username}")
        self.stdout.write(
            f"Permissions attribuées à l'Administrateur : "
            f"{result.permissions_granted_to_administrator}"
        )
        self.stdout.write(f"Module Documentation : {result.documentation_module.state}")
        self.stdout.write(f"Module RH : {result.rh_module.state}")
        self.stdout.write(f"Sauvegarde de vérification : {result.backup_run.status}")
