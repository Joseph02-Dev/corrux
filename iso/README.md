# Build ISO CORRUX (BUILD-003 — prototype)

Construit une ISO serveur Debian 13 bootable (BIOS + UEFI) embarquant
les paquets CORRUX et un preseed d'installation automatisée. Ne
modifie jamais la chaîne de boot officielle Debian (isolinux/grub-efi
tels que fournis) — Secure Boot n'est pas cassé.

## Prérequis (machine de build)

- `xorriso`
- `dpkg-dev` (fournit `dpkg-scanpackages`)
- `isolinux` (fournit `isohdpfx.bin`, nécessaire à l'hybridation MBR)
- Accès réseau sortant vers `cdimage.debian.org` (téléchargement, mis
  en cache localement — un seul téléchargement par version Debian)
- Python 3 avec les dépendances du dépôt CORRUX installées

## Utilisation

```bash
export CORRUX_TECH_PASSWORD_HASH='$6$...'   # hash crypt SHA-512, jamais en clair
                                              # génération : openssl passwd -6 '<mot de passe>'

iso/build_iso.sh <version> <chemin_clé_publique_gpg_release> <répertoire_de_travail>
```

Exemple :

```bash
iso/build_iso.sh 1.0.0 /chemin/vers/corrux-release-public-key.asc /var/tmp/corrux-iso-build
```

Produit `<répertoire_de_travail>/corrux-server-<version>.iso` et son
`.sha256`.

## Ce que fait le script

1. Télécharge (et met en cache) l'image Debian 13 **DVD-1** officielle
   (~4 Go), vérifie son intégrité contre `SHA256SUMS` officiel.
2. Extrait l'image.
3. Reconstruit les 3 paquets `.deb` CORRUX à partir du code du dépôt
   (réutilise `packaging/build_packages.py`, TECH-043 — aucune
   réimplémentation) et constitue un mini-dépôt APT local
   (`iso/build_repo.sh`).
4. Embarque la clé publique de release CORRUX fournie en paramètre
   (jamais générée par ce script).
5. Injecte le preseed (`iso/preseed/corrux.preseed`), avec le hash du
   mot de passe technicien substitué depuis la variable
   d'environnement `CORRUX_TECH_PASSWORD_HASH` — jamais de valeur en
   clair, jamais de valeur par défaut.
6. Régénère `md5sum.txt` (contrôle d'intégrité debian-installer).
7. Réassemble une ISO hybride BIOS+UEFI (`xorriso`), calcule son
   checksum SHA-256.

## Ce que ce prototype NE couvre PAS (hors périmètre BUILD-003)

- **Test de boot réel** (VM/BIOS/UEFI/matériel) — nécessite un outil
  de virtualisation, ticket distinct **BUILD-004**.
- Signature GPG de l'ISO elle-même (la signature existante, TECH-013,
  couvre le bundle de mise à jour `.deb`, pas l'image ISO).
- Détermination du compte administrateur CORRUX applicatif : reste
  entièrement à la charge de `corrux-setup`, exécuté au premier
  démarrage — ce mécanisme ne configure que l'OS.

## BUILD-005 — Décision : image DVD-1 au lieu de netinst

Constat empirique (test de boot réel, QEMU) : avec le mirroir réseau
Debian désactivé (mode offline garanti) et une base **netinst**,
l'installation échoue en cascade sur les paquets du système de base
(`initramfs-tools`, `linux-base`, `busybox`, `zstd`, `apparmor`...) —
une image netinst ne contient qu'un socle minimal et dépend
structurellement du réseau pour le reste. C'est incompatible avec
l'exigence produit « aucune dépendance à un miroir Internet pendant
l'installation ».

**Décision** : base **DVD-1** (`iso-dvd`, pas `iso-cd`/netinst) — elle
embarque l'ensemble des paquets nécessaires à une installation
standard sans réseau. Le mécanisme de preseed, le dépôt local CORRUX
et l'assemblage restent inchangés ; seule la source de l'image de
base change.

**⚠ Mise à jour externe requise** : `architecture-technique-v1.md` §4
(document de référence du projet, hors du dépôt Git) mentionne encore
« image Debian netinst standard ». Cette décision de BUILD-005 la
remplace par « image Debian DVD-1 » — à répercuter dans le document
de référence, cette mise à jour n'étant pas dans le périmètre
d'écriture de ce dépôt.

## BUILD-004 — Test de boot réel (état honnête)

Un test de boot a été mené sous QEMU (émulation logicielle `tcg`, sans
KVM) dans l'environnement de développement. Deux bugs réels ont été
trouvés et corrigés grâce à ce test :

1. **Langue/pays/locale non préseedables via `file=` seul** : ce sont
   les premières questions posées par `debian-installer`, avant même
   que le fichier preseed chargé depuis `/cdrom` ne puisse être lu.
   Sans les paramètres `debian-installer/language=`, `/country=`,
   `/locale=` explicitement sur la ligne de commande noyau,
   l'installation reste bloquée sur un écran interactif. Corrigé dans
   `build_iso.sh` (paramètres ajoutés à l'injection des entrées de
   boot isolinux/grub).
2. **Boucle infinie de synchronisation NTP** (« Setting up the
   clock ») en l'absence de sortie réseau fiable vers un serveur de
   temps. Corrigé par `d-i clock-setup/ntp boolean false` dans le
   preseed — cohérent avec le principe produit « CORRUX fonctionne
   sans accès Internet garanti ».

Après ces deux corrections, l'installation progresse correctement
au-delà (réseau, horloge, chargement des composants LVM) dans
l'environnement de test.

**Ce qui n'a PAS pu être prouvé dans ce sandbox** : un cycle complet
d'installation automatisée jusqu'à son terme.

### BUILD-005 — correctifs supplémentaires et limite atteinte

Deux correctifs de plus ont été identifiés en poursuivant le test :

3. **`apt-setup/cdrom/set-first true`** — enregistrement explicite du
   support comme source apt : en `priority=critical`, l'ajout
   automatique n'est pas garanti.
4. **`cdrom-detect/eject false`** — blocage réel constaté :
   l'installation restait **totalement figée** (deux segments de test
   consécutifs sans la moindre nouvelle ligne de log ni activité
   écran) juste après l'écriture de la liste de sources apt.
   `apt-setup` tente d'éjecter puis d'attendre la réinsertion du
   disque pour vérifier le jeu de cédéroms — un cycle qui ne peut
   jamais aboutir sous une VM sans tiroir physique.

Le partitionnement est également passé de LVM à standard (`regular`) :
LVM n'apporte rien sur un serveur mono-disque et n'est exigé nulle
part dans l'architecture.

**Note de méthode importante** : le marqueur `<ERR>` visible dans
l'interface texte de l'installeur s'est révélé être un **faux
positif**. L'analyse du syslog réel (via `log_host=`) montre que les
paquets concernés (`busybox`, `apparmor`, `linux-image-amd64`...) se
téléchargent et se configurent **avec succès**. Ne pas se fier au
marqueur `<ERR>` de l'écran pour diagnostiquer : utiliser le syslog.

**Limite de l'environnement, assumée** : l'environnement de
développement utilisé ne dispose d'aucune accélération matérielle de
virtualisation (`/dev/kvm` absent) et impose une limite de durée
d'exécution par commande inférieure au temps nécessaire à une
installation Debian complète en émulation logicielle pure (1 vCPU).
Chaque itération d'hypothèse coûtait ~35 minutes, ce qui ne converge
pas. **La validation d'un cycle complet doit être faite dans un
environnement avec KVM** (voir `iso/test_boot/`), pas par des tests
manuels segmentés.

## Sécurité

- Aucun secret en dur dans le dépôt : mot de passe technicien fourni
  hashé via variable d'environnement au moment du build, clé publique
  de release fournie en paramètre (jamais générée à la volée par ce
  script pour un usage réel).
- Le compte technicien créé par le preseed n'est **pas** root (accès
  sudo uniquement) — cohérent avec le principe « pas d'exécution en
  root » (architecture-technique-v1.md §16).

### ⚠ Le hash du mot de passe technicien est lisible dans l'ISO

C'est **inhérent au mécanisme preseed de Debian**, pas un défaut de ce
script : `debian-installer` doit pouvoir lire le hash en clair au
moment de créer le compte. Conséquence pratique à connaître :

> **Quiconque possède une copie de l'ISO peut en extraire le hash et
> tenter de le casser hors ligne**, sans limite de tentatives ni
> verrouillage.

Mesures recommandées :

1. **Mot de passe technicien fort** (long et aléatoire) — un mot de
   passe faible sera cassé rapidement une fois le hash extrait.
   `openssl passwd -6` utilise SHA-512 crypt, résistant mais pas
   magique face à un mot de passe court.
2. **Traiter l'ISO comme un support sensible** : ne pas la publier ni
   la laisser sur un partage ouvert.
3. **Changer le mot de passe technicien après installation**
   (`passwd corrux-tech` sur le serveur), ce qui rend le hash de l'ISO
   caduc.
4. **Une ISO par client** plutôt qu'une ISO générique réutilisée : un
   hash compromis n'expose alors qu'une seule installation.

Le compte administrateur CORRUX applicatif n'est **pas** concerné : il
est créé interactivement par `corrux-setup` au premier démarrage, son
mot de passe n'existe nulle part dans l'ISO.

