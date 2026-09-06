#!/usr/bin/env bash
# CORRUX — assemblage de l'ISO serveur bootable (BUILD-003, prototype).
#
# Principe : ne modifie JAMAIS la chaîne de boot officielle Debian
# (isolinux/grub-efi/shim tels que fournis par l'image netinst) —
# on ajoute uniquement : un preseed, un mini-dépôt local, la clé
# publique de confiance CORRUX. Ceci préserve Secure Boot (cf.
# BUILD-002, décision C2 confirmée par le produit).
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

# --- 1. Récupération (mise en cache) de l'image Debian 13 netinst officielle ---

BASE_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd"
ISO_LISTING=$(curl -s --max-time 20 "${BASE_URL}/")
ISO_NAME=$(echo "${ISO_LISTING}" | grep -o "debian-${DEBIAN_VERSION_SERIES}\.[0-9]*\.[0-9]*-amd64-netinst\.iso" | head -1)
if [ -z "${ISO_NAME}" ]; then
    echo "[build_iso] Impossible de déterminer le nom de l'ISO Debian ${DEBIAN_VERSION_SERIES} officielle." >&2
    exit 1
fi
BASE_ISO="${CACHE_DIR}/${ISO_NAME}"

if [ ! -f "${BASE_ISO}" ]; then
    echo "[build_iso] Téléchargement de ${ISO_NAME}..."
    curl -sL --max-time 600 -o "${BASE_ISO}.part" "${BASE_URL}/${ISO_NAME}"
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
sed "s|__CORRUX_TECH_PASSWORD_HASH__|${CORRUX_TECH_PASSWORD_HASH}|" \
    "${PROJECT_ROOT}/iso/preseed/corrux.preseed" \
    > "${EXTRACT_DIR}/corrux.preseed"

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

if [ -f "${EXTRACT_DIR}/isolinux/txt.cfg" ]; then
    sed -i \
        "s|append |append ${D_I_BOOT_PARAMS} |" \
        "${EXTRACT_DIR}/isolinux/txt.cfg"
fi
if [ -f "${EXTRACT_DIR}/boot/grub/grub.cfg" ]; then
    sed -i \
        "s|linux    /install.amd/vmlinuz|linux    /install.amd/vmlinuz ${D_I_BOOT_PARAMS}|" \
        "${EXTRACT_DIR}/boot/grub/grub.cfg"
fi

# --- 6. Régénération de md5sum.txt (contrôle d'intégrité debian-installer) ---

echo "[build_iso] Régénération de md5sum.txt..."
(
    cd "${EXTRACT_DIR}"
    find . -type f ! -name "md5sum.txt" -exec md5sum {} \; > md5sum.txt
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
