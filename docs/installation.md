# Installation initiale

Réalisée une seule fois, par un technicien IT, sur une machine **déjà
installée** avec l'un des systèmes suivants (amd64) :

| Système | Versions |
|---|---|
| Debian | 12 « Bookworm », 13 « Trixie » |
| Ubuntu Server | 22.04 LTS, 24.04 LTS, 26.04 LTS |
| Proxmox VE | 8 (Debian 12), 9 (Debian 13) — directement sur l'hôte |

CORRUX s'installe comme n'importe quel logiciel Debian, depuis son dépôt
apt signé. Aucun accès Internet n'est nécessaire une fois installé.

## Prérequis

- Un accès root (`sudo`).
- **Deux volumes de stockage physiquement distincts** : le disque système,
  et un second support pour les sauvegardes — disque secondaire interne,
  disque externe USB laissé branché en permanence, ou NAS local.
  **Jamais le disque système.**
- Une **clé publique GPG** de chiffrement des sauvegardes (fichier
  `.asc`), fournie par l'éditeur CORRUX ou générée pour un déploiement
  autonome (cf. [sauvegarde-restauration.md](sauvegarde-restauration.md)).
  Seule la clé publique est copiée sur le serveur ; la clé privée reste
  hors de la machine.
- Les ports 80 et 443 libres (Nginx). Sur Proxmox VE, l'interface de
  Proxmox (port 8006) n'est pas affectée.

## Étape 1 — Installer CORRUX

### Méthode A — script d'installation (recommandée)

```bash
curl -fsSL https://joseph02-dev.github.io/corrux/install.sh | sudo sh
```

Le script vérifie le système, ajoute la clé de signature du dépôt (son
empreinte est vérifiée) et le dépôt apt, puis installe le paquet
`corrux`. Il lance ensuite l'assistant de mise en service (étape 2).

Pour relire le script avant de l'exécuter :

```bash
curl -fsSLO https://joseph02-dev.github.io/corrux/install.sh
less install.sh
sudo sh install.sh
```

### Méthode B — apt, manuellement

```bash
sudo apt-get install -y ca-certificates curl
curl -fsSL https://joseph02-dev.github.io/corrux/corrux-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/corrux-archive-keyring.gpg >/dev/null
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/corrux-archive-keyring.gpg] https://joseph02-dev.github.io/corrux ./" \
  | sudo tee /etc/apt/sources.list.d/corrux.list
sudo apt-get update
sudo apt-get install -y corrux
```

### Ce que fait l'installation du paquet

Automatiquement, sans question :

- installe PostgreSQL, Nginx et les outils système nécessaires (paquets
  officiels de la distribution) ;
- crée le compte système `corrux` ;
- construit l'environnement Python de CORRUX dans `/opt/corrux/.venv`
  à partir des composants livrés dans le paquet (hors ligne) ;
- génère `/etc/corrux/core.env` (clé secrète et mot de passe de base de
  données aléatoires, lisible uniquement par root et le compte `corrux`) ;
- crée la base PostgreSQL locale `corrux` et applique les migrations.

Aucun service CORRUX n'est encore démarré à ce stade.

## Étape 2 — Mettre CORRUX en service

Repérer d'abord le périphérique du support de sauvegarde (jamais celui du
disque système, donné par `findmnt -no SOURCE /`) :

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE
```

Puis lancer l'assistant (déjà lancé par la méthode A) :

```bash
sudo corrux-setup
```

L'assistant demande, dans l'ordre :

1. le **point de montage** du support de sauvegarde (défaut
   `/srv/corrux-backup`) ;
2. le **chemin de la clé publique GPG** de chiffrement des sauvegardes
   (copiée dans `/etc/corrux/backup-gpg-public.key`) ;
3. l'**identifiant**, le **mot de passe** et le **nom complet** du compte
   administrateur ;
4. le **nom de la machine** sous lequel les postes joindront CORRUX (nom
   DNS ou adresse IP du LAN) — utilisé pour le certificat HTTPS ;
5. le **périphérique** du support de sauvegarde ;
6. la **confirmation de formatage** — répondre `oui` **efface toutes les
   données du support** ; `non` s'il est déjà formaté.

Chaque étape est validée avant la suivante ; un échec interrompt
l'assistant sans laisser de compte ni de module à moitié créé. À la fin,
`corrux-setup` écrit la configuration des tâches planifiées, active le
site Nginx et démarre CORRUX :

```
=== Installation terminée avec succès ===
Compte administrateur : <identifiant>
Module Documentation : activated
Module RH : activated
Sauvegarde de vérification : success
...
CORRUX est en service : https://<nom-de-la-machine>/
```

## Étape 3 — Vérifier

```bash
systemctl status corrux-core          # active (running)
systemctl list-timers 'corrux-*'      # sauvegarde et contrôle du certificat planifiés
```

Depuis un poste du réseau local, ouvrir `https://<nom-de-la-machine>/`.
Le certificat étant signé par l'autorité de certification interne de
CORRUX, installer une fois sur chaque poste son certificat racine
`/etc/corrux/tls/ca.crt` (magasin « Autorités de certification racines de
confiance ») pour supprimer l'avertissement du navigateur.

## Mode non interactif (installation scriptée)

```bash
curl -fsSL https://joseph02-dev.github.io/corrux/install.sh | sudo CORRUX_NONINTERACTIVE=1 sh
sudo install -m 0644 cle-publique-sauvegarde.asc /etc/corrux/backup-gpg-public.key
sudo corrux-setup \
  --backup-mount-point /srv/corrux-backup \
  --admin-username admin --admin-password '<mot de passe>' \
  --admin-full-name "Administrateur CORRUX" \
  --certificate-common-name corrux.exemple.local \
  --backup-device-path /dev/sdb1 \
  --format-backup-volume --skip-format-confirmation
```

`--format-backup-volume` **efface les données du support désigné**.
`--skip-format-confirmation` supprime la confirmation correspondante — à
réserver aux installations où elle a été obtenue par un autre moyen.

## Commandes d'administration

```bash
sudo corrux-manage <commande>        # ex. check, showmigrations, run_backup
sudo dpkg-reconfigure corrux-core    # reconstruit l'environnement Python (après une
                                     # montée de version de la distribution)
```

## Désinstallation

```bash
sudo apt-get remove corrux corrux-core corrux-module-documentation corrux-module-rh
sudo apt-get purge  corrux-core      # supprime aussi /etc/corrux (secrets, certificats)
```

La base PostgreSQL `corrux`, le stockage documentaire
(`/var/lib/corrux/storage`) et les sauvegardes ne sont **jamais**
supprimés automatiquement : ce sont les données du client.

## Base de données distante (optionnel)

Par défaut la base est locale. Pour une base PostgreSQL existante sur un
autre serveur, modifier `DB_HOST`, `DB_NAME`, `DB_USER` et `DB_PASSWORD`
dans `/etc/corrux/core.env` **avant** `corrux-setup` ; la base et le rôle
doivent alors être créés par l'administrateur de ce serveur.
