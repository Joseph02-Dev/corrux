# Installation initiale

Réalisée une seule fois, par un technicien IT, lors de la mise en service
d'un PC/serveur neuf (Debian 13 « Trixie »). Ne nécessite aucun accès
Internet après l'installation des paquets CORRUX.

## Prérequis

- Machine x86_64 sous Debian 13, avec les paquets CORRUX déjà installés
  (`corrux-core`, `corrux-module-documentation`, `corrux-module-rh` —
  livrés en `.deb`, cf. `packaging/`).
- **Deux volumes de stockage physiquement distincts** : le disque système
  (où sont déjà installés les paquets), et un second support pour les
  sauvegardes — disque secondaire interne, disque externe USB laissé
  branché en permanence, ou NAS local. **Jamais le disque système.**
- Une base PostgreSQL déjà créée et accessible (nom, utilisateur, mot de
  passe, hôte, port).
- Une clé publique GPG de destinataire pour le chiffrement des sauvegardes
  (fournie séparément par l'éditeur CORRUX, ou générée localement pour un
  déploiement autonome).

## Étape 1 — Identifier le volume de sauvegarde

Repérer le périphérique du support de sauvegarde (jamais celui du disque
système) :

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE
```

Le disque système est celui indiqué par :

```bash
findmnt -no SOURCE /
```

Noter le chemin du périphérique de sauvegarde (ex. `/dev/sdb1`) — il sera
demandé à l'étape 3.

## Étape 2 — Se placer dans l'installation CORRUX

```bash
cd /opt/corrux
source .venv/bin/activate
export DJANGO_SETTINGS_MODULE=corrux_core.settings
export DB_NAME=<nom_base> DB_USER=<utilisateur> DB_PASSWORD=<mot_de_passe>
export DB_HOST=<hote> DB_PORT=5432
```

## Étape 3 — Exécuter l'assistant

```bash
python manage.py corrux_setup
```

L'assistant pose les questions suivantes, dans l'ordre — chaque étape
valide avant de passer à la suivante ; un échec affiche un message
explicite et interrompt l'installation sans laisser d'état partiel :

1. **Identifiant** et **mot de passe** du compte administrateur initial,
   et son **nom complet**.
2. **Nom de la machine** (utilisé comme nom commun du certificat HTTPS —
   ex. le nom d'hôte ou l'adresse IP LAN de la machine).
3. **Périphérique du support de sauvegarde** (celui identifié à l'étape 1).
4. **Confirmation de formatage** — répondre `oui` **efface toutes les
   données déjà présentes sur ce support** ; répondre `non` si le support
   est déjà formaté et ne doit pas être réinitialisé.
5. **Point de montage** du support de sauvegarde (ex. `/mnt/corrux-backup`).

À l'issue, l'assistant affiche :

```
=== Installation terminée avec succès ===
Compte administrateur : <identifiant>
Permissions attribuées à l'Administrateur : <nombre>
Module Documentation : activated
Module RH : activated
Sauvegarde de vérification : success
```

Si l'un de ces éléments manque ou affiche un état différent,
l'installation n'est pas terminée — consulter le message d'erreur affiché
et recommencer l'étape 3 (aucun état partiel n'est jamais laissé en base).

## Étape 4 — Créer les fichiers d'environnement des services systemd

Les 3 unités systemd de CORRUX (`corrux-core.service`,
`corrux-backup.service`, `corrux-cert-check.service`) lisent leur
configuration depuis un fichier d'environnement dédié —
**`corrux-setup` ne les écrit pas lui-même** (limite constatée lors de la
rédaction de cette procédure ; les valeurs ci-dessous reprennent
exactement celles fournies à l'assistant à l'étape 3). Les créer avant de
démarrer les services :

```bash
mkdir -p /etc/corrux
chmod 700 /etc/corrux

cat > /etc/corrux/core.env << 'EOF'
DJANGO_SETTINGS_MODULE=corrux_core.settings
DB_NAME=<nom_base>
DB_USER=<utilisateur>
DB_PASSWORD=<mot_de_passe>
DB_HOST=<hote>
DB_PORT=5432
EOF

cat > /etc/corrux/backup.env << 'EOF'
DJANGO_SETTINGS_MODULE=corrux_core.settings
DB_NAME=<nom_base>
DB_USER=<utilisateur>
DB_PASSWORD=<mot_de_passe>
DB_HOST=<hote>
DB_PORT=5432
CORRUX_BACKUP_DESTINATION=/mnt/corrux-backup
CORRUX_BACKUP_GPG_RECIPIENT_KEY_PATH=/etc/corrux/backup-gpg-public.key
EOF

cat > /etc/corrux/certs.env << 'EOF'
CORRUX_CERT_SERVER_CERT_PATH=/etc/corrux/tls/server.crt
EOF

chmod 600 /etc/corrux/*.env
```

## Étape 5 — Activer la configuration Nginx

Le certificat HTTPS n'existe qu'une fois l'étape 3 terminée avec succès —
c'est pourquoi cette étape vient après, jamais avant. Le paquet
`corrux-core` dépose la configuration Nginx dans
`/etc/nginx/sites-available/corrux.conf`, mais ne l'active jamais lui-même
(convention Debian standard : un site n'est servi qu'une fois activé par un
lien symbolique dans `sites-enabled/`) :

```bash
ln -s /etc/nginx/sites-available/corrux.conf /etc/nginx/sites-enabled/corrux.conf
nginx -t   # vérifie la configuration avant de recharger
systemctl reload nginx
```

## Étape 6 — Démarrer le service applicatif principal

`corrux-core.service` exécute l'application elle-même (via gunicorn, en
écoute sur `127.0.0.1:8000`, jamais exposé directement — seul Nginx est
accessible depuis le réseau). Il doit être démarré et activé au boot :

```bash
systemctl enable --now corrux-core
systemctl status corrux-core   # doit afficher "active (running)"
```

## Étape 7 — Activer la sauvegarde et la vérification de certificat planifiées

```bash
systemctl enable --now corrux-backup.timer
systemctl enable --now corrux-cert-check.timer
```

## Étape 8 — Vérifier l'accès HTTPS

Depuis un poste du réseau local, ouvrir `https://<nom-ou-ip-de-la-machine>/`
dans un navigateur. Le certificat CORRUX étant signé par une autorité de
certification interne (pas une autorité publique), le navigateur affichera
un avertissement de sécurité tant que le certificat racine n'a pas été
installé sur le poste client :

```bash
# Chemin par défaut du certificat racine, à distribuer aux postes clients :
/etc/corrux/tls/ca.crt
```

L'installation du certificat racine sur les postes clients est une
procédure propre à chaque système d'exploitation client (hors périmètre
de ce document) — une fois installée, la connexion HTTPS ne déclenche
plus d'avertissement.

## Mode non interactif (installation scriptée)

Toutes les questions ci-dessus peuvent être fournies en options de ligne
de commande, pour une installation entièrement automatisée (aucun prompt
affiché si toutes les options nécessaires sont présentes) :

```bash
python manage.py corrux_setup \
  --admin-username admin --admin-password '<mot de passe>' \
  --admin-full-name "Administrateur CORRUX" \
  --certificate-common-name corrux.exemple.local \
  --backup-device-path /dev/sdb1 \
  --backup-mount-point /mnt/corrux-backup \
  --format-backup-volume --skip-format-confirmation \
  --backup-gpg-recipient-key-path /etc/corrux/backup-gpg-public.key \
  --db-name <nom_base> --db-user <utilisateur> --db-password '<mot de passe>' \
  --db-host <hote> --db-port 5432
```

`--format-backup-volume` **efface les données du support désigné** — à
n'utiliser que sur un support neuf ou dont l'effacement est voulu.
`--skip-format-confirmation` supprime la confirmation interactive
correspondante — à réserver aux installations scriptées où cette
confirmation a déjà été obtenue par un autre moyen.
