#!/usr/bin/env bash
# CORRUX — test d'installation automatisée de bout en bout (BUILD-005).
#
# Lance l'ISO CORRUX dans une VM, laisse l'installation preseedée se
# dérouler seule, et vérifie ensuite sur le disque installé que les
# paquets CORRUX sont bien présents. Le test réussit UNIQUEMENT si
# l'installation se termine d'elle-même ET que la vérification passe.
#
# POURQUOI KVM EST REQUIS : sans accélération matérielle, une
# installation Debian complète prend plusieurs heures en émulation
# logicielle pure — infaisable en pratique et en CI. Avec KVM, ce test
# tourne en quelques minutes. Le script refuse donc de démarrer si
# /dev/kvm est absent, plutôt que de partir dans un run interminable.
#
# Usage :
#   iso/test_boot/run_install_test.sh <chemin_iso> [répertoire_de_travail]
#
# Prérequis : qemu-system-x86_64, qemu-img, /dev/kvm accessible,
#             guestfish (paquet libguestfs-tools) pour la vérification.
#
# Code de sortie : 0 = succès, non-zéro = échec (avec message explicite).

set -euo pipefail

ISO_PATH="${1:?Usage: run_install_test.sh <chemin_iso> [répertoire_de_travail]}"
WORK_DIR="${2:-$(mktemp -d)}"

DISK="${WORK_DIR}/test-disk.qcow2"
SERIAL_LOG="${WORK_DIR}/serial.log"
DISK_SIZE="${CORRUX_TEST_DISK_SIZE:-16G}"
VM_RAM="${CORRUX_TEST_VM_RAM:-2048}"
VM_CPUS="${CORRUX_TEST_VM_CPUS:-2}"
# Garde-fou : au-delà, on considère l'installation bloquée plutôt que
# d'attendre indéfiniment. Généreux par rapport au temps réel attendu
# sous KVM (quelques minutes), pour ne pas produire de faux échec sur
# une machine lente.
TIMEOUT_SECONDS="${CORRUX_TEST_TIMEOUT:-3600}"

fail() { echo "[test_boot] ÉCHEC : $*" >&2; exit 1; }

# --- Prérequis -------------------------------------------------------

[ -f "${ISO_PATH}" ] || fail "ISO introuvable : ${ISO_PATH}"
command -v qemu-system-x86_64 >/dev/null || fail "qemu-system-x86_64 absent."
command -v qemu-img >/dev/null || fail "qemu-img absent."

if [ ! -e /dev/kvm ]; then
    fail "/dev/kvm absent : ce test exige une accélération matérielle.
Sans KVM, une installation Debian complète prend plusieurs heures en
émulation logicielle, ce qui rend ce test inexploitable. Exécuter ce
script sur une machine (ou un runner CI) disposant de KVM."
fi

mkdir -p "${WORK_DIR}"

# --- Installation ----------------------------------------------------

echo "[test_boot] Préparation du disque de test (${DISK_SIZE})..."
rm -f "${DISK}"
qemu-img create -f qcow2 "${DISK}" "${DISK_SIZE}" >/dev/null

echo "[test_boot] Lancement de l'installation automatisée (timeout : ${TIMEOUT_SECONDS}s)..."
echo "[test_boot] L'installation est terminée quand QEMU s'arrête de lui-même (-no-reboot)."

set +e
timeout "${TIMEOUT_SECONDS}" qemu-system-x86_64 \
    -m "${VM_RAM}" -smp "${VM_CPUS}" -accel kvm \
    -drive file="${DISK}",format=qcow2,if=virtio \
    -cdrom "${ISO_PATH}" \
    -boot d \
    -netdev user,id=net0 -device virtio-net,netdev=net0 \
    -serial "file:${SERIAL_LOG}" \
    -display none \
    -no-reboot
QEMU_RC=$?
set -e

if [ "${QEMU_RC}" -eq 124 ]; then
    fail "l'installation n'est pas arrivée à son terme dans le délai imparti
(${TIMEOUT_SECONDS}s) — installation probablement bloquée sur une
question interactive. Console série : ${SERIAL_LOG}"
fi

echo "[test_boot] L'installateur s'est arrêté de lui-même (code ${QEMU_RC})."

# --- Vérification du système installé --------------------------------
#
# On ne se fie PAS à l'affichage de l'installateur : on inspecte le
# disque réellement produit. Leçon de BUILD-005 — le marqueur <ERR> de
# l'interface texte s'est révélé être un faux positif.

command -v guestfish >/dev/null || fail "guestfish absent (paquet libguestfs-tools) : vérification impossible."

echo "[test_boot] Vérification des paquets CORRUX sur le disque installé..."
INSTALLED=$(guestfish --ro -a "${DISK}" -i sh "dpkg-query -W -f=\${Package}\\\\n corrux-core corrux-module-documentation corrux-module-rh" 2>/dev/null || true)

for pkg in corrux-core corrux-module-documentation corrux-module-rh; do
    echo "${INSTALLED}" | grep -qx "${pkg}" \
        || fail "paquet « ${pkg} » absent du système installé.
Paquets détectés : ${INSTALLED:-aucun}"
    echo "[test_boot]   ✓ ${pkg}"
done

echo "[test_boot] Vérification des services CORRUX..."
guestfish --ro -a "${DISK}" -i exists /etc/systemd/system/multi-user.target.wants/corrux-core.service >/dev/null 2>&1 \
    || echo "[test_boot]   (avertissement : corrux-core.service non activé — à confirmer, corrux-setup s'exécute au premier démarrage)"

echo "[test_boot] SUCCÈS : installation automatisée complète et paquets CORRUX présents."
echo "[test_boot] Disque de test : ${DISK}"
