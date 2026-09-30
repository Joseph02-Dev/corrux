#!/usr/bin/env bash
# CORRUX — construit le site de publication (DEPLOY-001) :
#
#   <sortie>/install.sh                 script `curl | sh` (URL et empreinte figées)
#   <sortie>/corrux-archive-keyring.gpg clé publique du dépôt (format keyring apt)
#   <sortie>/corrux-archive-keyring.asc la même, format ASCII (vérification manuelle)
#   <sortie>/*.deb, Packages(.gz), Release, InRelease, Release.gpg
#                                       dépôt apt « plat » signé
#
# Le dépôt est « plat » (`deb <url> ./`) : même format que le canal de
# mise à jour en ligne existant (ops/update_service.py), un seul dépôt
# pour toutes les distributions supportées (Python embarqué).
#
# Usage :
#   CORRUX_SIGNING_KEY_ID=<empreinte> CORRUX_REPO_URL=<url publique> \
#     packaging/build_apt_site.sh <version> <répertoire_de_sortie>
#
# La clé privée de signature doit déjà être présente dans le trousseau
# GnuPG courant (GNUPGHOME) : ce script ne la génère ni ne la lit
# jamais depuis un fichier. En CI, elle vient d'un secret GitHub
# (cf. .github/workflows/release.yml).
set -euo pipefail

VERSION="${1:?Usage: build_apt_site.sh <version> <répertoire_de_sortie>}"
OUT_DIR="${2:?Usage: build_apt_site.sh <version> <répertoire_de_sortie>}"
: "${CORRUX_SIGNING_KEY_ID:?CORRUX_SIGNING_KEY_ID requis (empreinte de la clé de signature)}"
: "${CORRUX_REPO_URL:?CORRUX_REPO_URL requis (URL publique du dépôt)}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
GPG=(gpg --batch --yes --pinentry-mode loopback)
if [[ -n "${CORRUX_SIGNING_PASSPHRASE:-}" ]]; then
    GPG+=(--passphrase-fd 3)
fi

for tool in dpkg-deb dpkg-scanpackages apt-ftparchive gpg; do
    command -v "$tool" >/dev/null || { echo "Outil manquant : $tool" >&2; exit 1; }
done

# Version Debian valide (ex. 1.2.0, 1.2.0~rc1) : jamais un tag brut.
if [[ ! "$VERSION" =~ ^[0-9][0-9A-Za-z.+~-]*$ ]]; then
    echo "Version invalide : ${VERSION}" >&2
    exit 1
fi

FINGERPRINT="$(gpg --batch --with-colons --list-secret-keys "$CORRUX_SIGNING_KEY_ID" \
    | awk -F: '$1 == "fpr" { print $10; exit }')"
if [[ -z "$FINGERPRINT" ]]; then
    echo "Clé privée introuvable dans le trousseau : ${CORRUX_SIGNING_KEY_ID}" >&2
    exit 1
fi

sign() {
    if [[ -n "${CORRUX_SIGNING_PASSPHRASE:-}" ]]; then
        "${GPG[@]}" --local-user "$FINGERPRINT" "$@" 3<<<"$CORRUX_SIGNING_PASSPHRASE"
    else
        "${GPG[@]}" --local-user "$FINGERPRINT" "$@"
    fi
}

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "[apt-site] Wheels Python embarqués..."
cd "$PROJECT_ROOT"
"$PYTHON" -m packaging.wheelhouse requirements.txt "${WORK_DIR}/wheelhouse"

echo "[apt-site] Paquets .deb ${VERSION}..."
"$PYTHON" - "$VERSION" "$OUT_DIR" "${WORK_DIR}/wheelhouse" <<'PYEOF'
import sys
from pathlib import Path

from packaging.build_packages import (
    ALL_PACKAGE_SPEC_BUILDERS,
    build_corrux_core_spec,
    build_deb_package,
)

version, out_dir, wheelhouse = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
for builder in ALL_PACKAGE_SPEC_BUILDERS:
    if builder is build_corrux_core_spec:
        spec = builder(version, wheelhouse)
    else:
        spec = builder(version)
    print(f"  -> {build_deb_package(spec, Path('.').resolve(), out_dir).name}")
PYEOF

echo "[apt-site] Index du dépôt..."
cd "$OUT_DIR"
dpkg-scanpackages --multiversion . /dev/null >Packages 2>/dev/null
gzip -9kf Packages
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=CORRUX \
    -o APT::FTPArchive::Release::Label=CORRUX \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Architectures="amd64 all" \
    -o APT::FTPArchive::Release::Description="CORRUX ${VERSION}" \
    release . >"${WORK_DIR}/Release"
mv "${WORK_DIR}/Release" Release

echo "[apt-site] Signature (${FINGERPRINT})..."
sign --clearsign --output InRelease Release
sign --armor --detach-sign --output Release.gpg Release
gpg --batch --yes --export "$FINGERPRINT" >corrux-archive-keyring.gpg
gpg --batch --yes --armor --export "$FINGERPRINT" >corrux-archive-keyring.asc

echo "[apt-site] install.sh..."
sed -e "s|@@CORRUX_REPO_URL@@|${CORRUX_REPO_URL%/}|" \
    -e "s|@@CORRUX_KEY_FINGERPRINT@@|${FINGERPRINT}|" \
    "${PROJECT_ROOT}/install/install.sh" >install.sh
chmod 0755 install.sh
sha256sum ./*.deb install.sh >SHA256SUMS

echo "[apt-site] Site prêt : ${OUT_DIR}"
