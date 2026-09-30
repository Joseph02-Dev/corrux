#!/bin/sh
# CORRUX — installation sur un système Linux existant.
#
#   curl -fsSL https://joseph02-dev.github.io/corrux/install.sh | sudo sh
#
# Systèmes supportés (amd64) : Debian 12/13, Ubuntu 22.04/24.04 LTS,
# Proxmox VE 8/9. Le script ne fait qu'ajouter le dépôt apt signé de
# CORRUX puis installer le paquet `corrux` : tout le reste (compte
# système, environnement Python, base PostgreSQL) est fait par les
# paquets eux-mêmes, exactement comme avec `apt install corrux`.
#
# Variables optionnelles :
#   CORRUX_REPO_URL     dépôt apt (défaut : dépôt officiel publié)
#   CORRUX_NONINTERACTIVE=1  ne pas lancer corrux-setup à la fin
#
# Les valeurs @@...@@ sont substituées à la publication
# (packaging/build_apt_site.sh).
set -eu

REPO_URL="${CORRUX_REPO_URL:-@@CORRUX_REPO_URL@@}"
KEY_FINGERPRINT="@@CORRUX_KEY_FINGERPRINT@@"
KEYRING=/usr/share/keyrings/corrux-archive-keyring.gpg
SOURCES_LIST=/etc/apt/sources.list.d/corrux.list

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }

# Tout le script est dans une fonction appelée à la dernière ligne : un
# téléchargement interrompu (`curl | sh`) n'exécute jamais un script
# partiel.
main() {
    [ "$(id -u)" -eq 0 ] || fail "à exécuter en root : curl -fsSL <url>/install.sh | sudo sh"
    case "$REPO_URL" in
        @@*) fail "script non publié : définir CORRUX_REPO_URL (URL du dépôt apt)." ;;
    esac

    check_system
    install_prerequisites
    add_repository

    info "Installation de CORRUX..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y corrux

    info "CORRUX est installé."
    if [ "${CORRUX_NONINTERACTIVE:-0}" != "1" ] && [ -r /dev/tty ]; then
        info "Lancement de l'assistant de mise en service (corrux-setup)..."
        corrux-setup </dev/tty
    else
        info "Étape suivante : sudo corrux-setup"
    fi
}

check_system() {
    [ -r /etc/os-release ] || fail "/etc/os-release introuvable : système non supporté."
    # shellcheck disable=SC1091
    . /etc/os-release
    os="${ID:-}"
    version="${VERSION_ID:-}"
    label="${PRETTY_NAME:-$os $version}"
    if command -v pveversion >/dev/null 2>&1; then
        label="Proxmox VE ($(pveversion | cut -d/ -f2)) sur ${label}"
    fi

    case "${os}:${version}" in
        debian:12|debian:13|ubuntu:22.04|ubuntu:24.04) ;;
        *) fail "${label} n'est pas supporté (Debian 12/13, Ubuntu 22.04/24.04, Proxmox VE 8/9)." ;;
    esac

    arch="$(dpkg --print-architecture)"
    [ "$arch" = "amd64" ] || fail "architecture ${arch} non supportée (amd64 uniquement)."

    info "Système détecté : ${label}"
}

install_prerequisites() {
    info "Mise à jour de l'index des paquets..."
    # Toléré : une source tierce en erreur (ex. dépôt « enterprise » de
    # Proxmox sans abonnement, HTTP 401) ne doit pas bloquer
    # l'installation ; l'installation échouera d'elle-même si les
    # dépôts de la distribution sont réellement inaccessibles.
    apt-get update -q || info "Avertissement : certaines sources apt sont en erreur (voir ci-dessus)."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q ca-certificates curl gnupg
}

add_repository() {
    info "Ajout de la clé de signature du dépôt CORRUX..."
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL "${REPO_URL}/corrux-archive-keyring.gpg" -o "${tmp}/keyring.gpg"

    # Empreinte figée à la publication : une clé substituée sur le
    # serveur est refusée même si le transport HTTPS est compromis.
    case "$KEY_FINGERPRINT" in
        @@*) ;;
        *)
            actual="$(gpg --show-keys --with-colons "${tmp}/keyring.gpg" 2>/dev/null \
                | awk -F: '$1 == "fpr" { print $10; exit }')"
            [ "$actual" = "$KEY_FINGERPRINT" ] \
                || fail "empreinte de la clé du dépôt inattendue (${actual:-illisible})."
            ;;
    esac
    install -m 0644 "${tmp}/keyring.gpg" "$KEYRING"

    info "Ajout du dépôt apt : ${REPO_URL}"
    echo "deb [arch=amd64 signed-by=${KEYRING}] ${REPO_URL} ./" >"$SOURCES_LIST"
    # Mise à jour du seul dépôt CORRUX : ici une erreur (signature
    # invalide, dépôt injoignable) doit arrêter l'installation.
    apt-get update -q -o Dir::Etc::sourcelist="$SOURCES_LIST" \
        -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
}

main "$@"
