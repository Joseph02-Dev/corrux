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

1. Télécharge (et met en cache) l'image Debian 13 netinst officielle,
   vérifie son intégrité contre `SHA256SUMS` officiel.
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
d'installation automatisée jusqu'à son terme. L'environnement de
développement utilisé ne dispose d'aucune accélération matérielle de
virtualisation (`/dev/kvm` absent), et son mécanisme d'exécution de
commandes impose une limite de durée par appel qui s'est révélée
inférieure au temps nécessaire à une installation Debian complète en
émulation logicielle pure (1 vCPU). Comme `debian-installer` ne
reprend jamais une installation interrompue, chaque essai tronqué
recommençait entièrement — un test de bout en bout réel nécessite un
environnement avec accélération matérielle (KVM réel ou machine
physique), hors périmètre de ce sandbox.

## Sécurité

- Aucun secret en dur dans le dépôt : mot de passe technicien fourni
  hashé via variable d'environnement au moment du build, clé publique
  de release fournie en paramètre (jamais générée à la volée par ce
  script pour un usage réel).
- Le compte technicien créé par le preseed n'est **pas** root (accès
  sudo uniquement) — cohérent avec le principe « pas d'exécution en
  root » (architecture-technique-v1.md §16).
