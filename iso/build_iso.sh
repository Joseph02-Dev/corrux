#!/usr/bin/env bash
# CORRUX — assemblage de l'ISO serveur bootable (BUILD-003, prototype ;
# base DVD-1 depuis BUILD-005).
#
# Principe : ne modifie JAMAIS la chaîne de boot officielle Debian
# (isolinux/grub-efi/shim tels que fournis par l'image DVD) — on
# ajoute uniquement : un preseed, un mini-dépôt local, la clé publique
# de confiance CORRUX. Ceci préserve Secure Boot (cf. BUILD-002,
# décision C2 confirmée par le produit).
#
# Base DVD-1 (pas netinst) — décision BUILD-005 : une image netinst ne
# contient qu'un socle minimal et dépend du réseau pour le système de
# base (noyau, initramfs-tools, etc.), ce qui contredit directement
# l'exigence « aucune dépendance à un miroir Internet pendant
# l'installation » (architecture-technique-v1.md §4, mis à jour en
# conséquence). Constaté empiriquement : cascade d'échecs <ERR> sur
# busybox/initramfs-tools/linux-base/etc. avec le mirroir réseau
# désactivé et une base netinst. L'image DVD-1 embarque l'ensemble des
# paquets nécessaires à une installation standard, sans réseau.
#
# Usage :
#   CORRUX_TECH_PASSWORD_HASH='$6$...'  \
#   iso/build_iso.sh <version> <chemin_clé_publique_gpg> <répertoire_de_travail>
#
# Prérequis : xorriso, dpkg-scanpackages (paquet dpkg-dev), curl.
# Aucun secret en dur : le hash du mot de passe technicien est fourni
# par variable d'environnement (déjà chiffré, jamais en clair), la clé
# publique de release est un fichier fourni explicitement (jamais
# générée ni devinée par ce script).
#
# Espace disque : l'image DVD-1 (~4 Go) est volumineuse. Pour limiter
# le pic d'usage disque pendant le build (source + extraction +
# ISO finale simultanément), le cache de la source est supprimé après
# extraction — un nouveau build retélécharge la source (réseau rapide
# généralement, coût accepté au profit de l'espace disque).

set -euo pipefail

VERSION="${1:?Usage: build_iso.sh <version> <clé_publique_gpg> <répertoire_de_travail>}"
PUBKEY_PATH="${2:?Usage: build_iso.sh <version> <clé_publique_gpg> <répertoire_de_travail>}"
WORK_DIR="${3:?Usage: build_iso.sh <version> <clé_publique_gpg> <répertoire_de_travail>}"

: "${CORRUX_TECH_PASSWORD_HASH:?Variable CORRUX_TECH_PASSWORD_HASH requise (hash crypt SHA-512 du mot de passe technicien, jamais en clair).}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEBIAN_VERSION_SERIES="13"
CACHE_DIR="${WORK_DIR}/cache"
EXTRACT_DIR="${WORK_DIR}/extracted"
REPO_DIR="${WORK_DIR}/local-repo"
OUT_ISO="${WORK_DIR}/corrux-server-${VERSION}.iso"

mkdir -p "${CACHE_DIR}" "${EXTRACT_DIR}" "${REPO_DIR}"

# --- 1. Récupération de l'image Debian 13 DVD-1 officielle ---

BASE_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-dvd"
ISO_LISTING=$(curl -s --max-time 20 "${BASE_URL}/")
ISO_NAME=$(echo "${ISO_LISTING}" | grep -o "debian-${DEBIAN_VERSION_SERIES}\.[0-9]*\.[0-9]*-amd64-DVD-1\.iso" | head -1)
if [ -z "${ISO_NAME}" ]; then
    echo "[build_iso] Impossible de déterminer le nom de l'ISO DVD-1 Debian ${DEBIAN_VERSION_SERIES} officielle." >&2
    exit 1
fi
BASE_ISO="${CACHE_DIR}/${ISO_NAME}"

if [ ! -f "${BASE_ISO}" ]; then
    echo "[build_iso] Téléchargement de ${ISO_NAME} (~4 Go, peut prendre plusieurs minutes)..."
    # --fail : une réponse HTTP d'erreur (404, 500...) doit faire
    # échouer curl, sinon la page d'erreur serait écrite dans le .part
    # puis renommée en .iso (le checksum le rattraperait, mais avec un
    # message trompeur). Relevé en revue de code.
    if ! curl -fsSL --max-time 900 -o "${BASE_ISO}.part" "${BASE_URL}/${ISO_NAME}"; then
        rm -f "${BASE_ISO}.part"
        echo "[build_iso] ÉCHEC : téléchargement de ${ISO_NAME} impossible." >&2
        exit 1
    fi
    mv "${BASE_ISO}.part" "${BASE_ISO}"
else
    echo "[build_iso] ISO officielle déjà en cache : ${BASE_ISO}"
fi

# Vérification d'intégrité contre la somme officielle Debian (défense
# en profondeur — indépendante de la signature GPG CORRUX ajoutée
# plus loin, qui protège le contenu CORRUX, pas l'image Debian elle-même).
echo "[build_iso] Vérification de l'intégrité de l'image Debian officielle..."
SUMS=$(curl -s --max-time 20 "${BASE_URL}/SHA256SUMS")
EXPECTED_SHA=$(echo "${SUMS}" | grep " ${ISO_NAME}\$" | awk '{print $1}')
ACTUAL_SHA=$(sha256sum "${BASE_ISO}" | awk '{print $1}')
if [ -z "${EXPECTED_SHA}" ] || [ "${EXPECTED_SHA}" != "${ACTUAL_SHA}" ]; then
    echo "[build_iso] ÉCHEC : checksum de l'image Debian officielle non vérifié (attendu=${EXPECTED_SHA:-absent}, obtenu=${ACTUAL_SHA})." >&2
    exit 1
fi
echo "[build_iso] Intégrité de l'image Debian officielle confirmée."

# --- 2. Extraction ---

echo "[build_iso] Extraction de l'image de base..."
rm -rf "${EXTRACT_DIR:?}"/*
xorriso -osirrox on -indev "${BASE_ISO}" -extract / "${EXTRACT_DIR}" >/dev/null

# Libère l'espace du cache source (~4 Go) : le réassemblage doit
# coexister avec l'arborescence extraite ET l'ISO finale. Sur une
# machine disposant d'espace, CORRUX_KEEP_CACHE=1 conserve la source
# pour éviter un retéléchargement au build suivant (point relevé en
# revue de code) — voir note d'espace disque en tête de fichier.
if [ "${CORRUX_KEEP_CACHE:-0}" = "1" ]; then
    echo "[build_iso] Cache source conservé (CORRUX_KEEP_CACHE=1) : ${BASE_ISO}"
else
    rm -f "${BASE_ISO}"
fi

# --- 3. Constitution du dépôt local CORRUX ---

echo "[build_iso] Constitution du dépôt local CORRUX..."
"${PROJECT_ROOT}/iso/build_repo.sh" "${VERSION}" "${REPO_DIR}"
mkdir -p "${EXTRACT_DIR}/corrux-repo"
cp -a "${REPO_DIR}/." "${EXTRACT_DIR}/corrux-repo/"

# --- 4. Clé publique de confiance CORRUX ---

if [ ! -f "${PUBKEY_PATH}" ]; then
    echo "[build_iso] ÉCHEC : clé publique introuvable : ${PUBKEY_PATH}" >&2
    exit 1
fi
cp "${PUBKEY_PATH}" "${EXTRACT_DIR}/corrux-release-public-key.asc"

# --- 5. Preseed (mot de passe technicien injecté depuis l'environnement) ---

echo "[build_iso] Injection du preseed..."
# Remplacement LITTÉRAL du jeton. On n'utilise volontairement pas
# `sed s|...|${HASH}|` : sed interprète `&` (= le motif trouvé) et les
# séquences `\1`, ce qui corromprait silencieusement un hash contenant
# ces caractères — mot de passe technicien inutilisable, sans aucune
# erreur affichée. Bug trouvé en revue de code (BUILD-005).
# awk avec index/substr fait un remplacement strictement littéral.
CORRUX_TECH_PASSWORD_HASH="${CORRUX_TECH_PASSWORD_HASH}" \
awk '
    BEGIN { token = "__CORRUX_TECH_PASSWORD_HASH__"; hash = ENVIRON["CORRUX_TECH_PASSWORD_HASH"] }
    {
        line = $0
        out = ""
        while ((pos = index(line, token)) > 0) {
            out = out substr(line, 1, pos - 1) hash
            line = substr(line, pos + length(token))
            replaced = 1
        }
        print out line
    }
    END { if (!replaced) { print "[build_iso] ÉCHEC : jeton de mot de passe introuvable dans le preseed." > "/dev/stderr"; exit 1 } }
' "${PROJECT_ROOT}/iso/preseed/corrux.preseed" > "${EXTRACT_DIR}/corrux.preseed"

# Ajout des paramètres de boot preseed sur les entrées par défaut
# (isolinux BIOS + grub UEFI), sans toucher au reste de la chaîne de
# boot officielle Debian.
#
# debian-installer/language, /country et /locale sont ajoutés
# EXPLICITEMENT sur la ligne de commande noyau (pas seulement dans le
# fichier preseed) : ce sont les deux/trois seules questions posées
# avant que le fichier preseed (chargé depuis /cdrom) ne puisse être
# lu — sans cela, l'installeur reste bloqué sur l'écran interactif
# « Select a language », jamais atteint par une préconfiguration
# fournie uniquement via `file=`. Bug réel constaté et corrigé lors du
# test BUILD-004 (doc officielle Debian : apbs04, « The commands
# debian-installer/language and debian-installer/country [...] can
# only be preseeded using the kernel boot parameters »).
D_I_BOOT_PARAMS="auto=true priority=critical debian-installer/language=en debian-installer/country=US debian-installer/locale=en_US.UTF-8 file=/cdrom/corrux.preseed"

# Chaque patch est vérifié : si Debian change l'emplacement ou le
# format de ces fichiers, l'ISO se construirait sans preseed et
# l'installation redeviendrait interactive — sans aucun signal au
# moment du build. On échoue ici plutôt que de livrer une ISO
# silencieusement inutilisable (point relevé en revue de code).
BOOT_ENTRIES_PATCHED=0

if [ -f "${EXTRACT_DIR}/isolinux/txt.cfg" ]; then
    sed -i "s|append |append ${D_I_BOOT_PARAMS} |" "${EXTRACT_DIR}/isolinux/txt.cfg"
    if grep -q "file=/cdrom/corrux.preseed" "${EXTRACT_DIR}/isolinux/txt.cfg"; then
        BOOT_ENTRIES_PATCHED=$((BOOT_ENTRIES_PATCHED + 1))
        echo "[build_iso]   ✓ entrée de boot BIOS (isolinux) patchée"
    fi
fi

if [ -f "${EXTRACT_DIR}/boot/grub/grub.cfg" ]; then
    sed -i "s|linux    /install.amd/vmlinuz|linux    /install.amd/vmlinuz ${D_I_BOOT_PARAMS}|" \
        "${EXTRACT_DIR}/boot/grub/grub.cfg"
    if grep -q "file=/cdrom/corrux.preseed" "${EXTRACT_DIR}/boot/grub/grub.cfg"; then
        BOOT_ENTRIES_PATCHED=$((BOOT_ENTRIES_PATCHED + 1))
        echo "[build_iso]   ✓ entrée de boot UEFI (grub) patchée"
    fi
fi

if [ "${BOOT_ENTRIES_PATCHED}" -eq 0 ]; then
    echo "[build_iso] ÉCHEC : aucune entrée de boot n'a pu être patchée avec le preseed.
L'ISO produite démarrerait en mode interactif. Vérifier que l'image de
base contient bien isolinux/txt.cfg et/ou boot/grub/grub.cfg." >&2
    exit 1
fi

# --- 6. Régénération de md5sum.txt (contrôle d'intégrité debian-installer) ---

echo "[build_iso] Régénération de md5sum.txt..."
# `-exec ... +` groupe les fichiers en un nombre minimal d'appels, au
# lieu d'un process md5sum par fichier. Sur une image DVD (~15 000
# fichiers) l'écart est considérable : mesuré ~180x plus rapide sur un
# échantillon de 2 000 fichiers, pour un résultat identique (point
# relevé en revue de code).
(
    cd "${EXTRACT_DIR}"
    find . -type f ! -name "md5sum.txt" -exec md5sum {} + > md5sum.txt
)

# --- 7. Réassemblage hybride BIOS + UEFI ---
#
# isohdpfx.bin n'est PAS fourni par l'image Debian extraite : c'est un
# fichier du paquet système "isolinux" de la machine de build
# (prérequis documenté dans iso/README.md), utilisé ici pour rendre
# l'ISO démarrable en mode disque USB hybride (MBR).

ISOHDPFX_CANDIDATES=(
    "/usr/lib/ISOLINUX/isohdpfx.bin"
    "/usr/lib/syslinux/isohdpfx.bin"
    "/usr/share/syslinux/isohdpfx.bin"
)
ISOHDPFX=""
for candidate in "${ISOHDPFX_CANDIDATES[@]}"; do
    if [ -f "${candidate}" ]; then
        ISOHDPFX="${candidate}"
        break
    fi
done
if [ -z "${ISOHDPFX}" ]; then
    echo "[build_iso] ÉCHEC : isohdpfx.bin introuvable. Installer le paquet 'isolinux' sur la machine de build." >&2
    exit 1
fi

echo "[build_iso] Réassemblage de l'ISO (hybride BIOS+UEFI)..."
mkdir -p "$(dirname "${OUT_ISO}")"
xorriso -as mkisofs \
    -o "${OUT_ISO}" \
    -isohybrid-mbr "${ISOHDPFX}" \
    -c isolinux/boot.cat \
    -b isolinux/isolinux.bin \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot \
    -e boot/grub/efi.img \
    -no-emul-boot -isohybrid-gpt-basdat \
    -V "CORRUX Server ${VERSION}" \
    "${EXTRACT_DIR}"

sha256sum "${OUT_ISO}" | awk '{print $1}' > "${OUT_ISO}.sha256"
echo "[build_iso] ISO produite : ${OUT_ISO}"
echo "[build_iso] Checksum    : $(cat "${OUT_ISO}.sha256")"
