#!/usr/bin/env bash
# CORRUX — constitution du mini-dépôt APT local embarqué dans l'ISO.
#
# Réutilise directement packaging/build_packages.py (TECH-043, déjà
# écrit et testé) : ce script ne réimplémente aucune logique de
# construction de paquet, il appelle le code existant puis produit
# l'index APT (Packages/Packages.gz) attendu par le preseed
# (apt-setup/local1/repository, cf. iso/preseed/corrux.preseed).
#
# Usage : iso/build_repo.sh <version> <répertoire_de_sortie>
# Exemple : iso/build_repo.sh 1.0.0 iso/build/local-repo

set -euo pipefail

VERSION="${1:?Usage: build_repo.sh <version> <répertoire_de_sortie>}"
OUT_DIR="${2:?Usage: build_repo.sh <version> <répertoire_de_sortie>}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "${OUT_DIR}"

echo "[build_repo] Construction des paquets .deb CORRUX (version ${VERSION})..."
python3 - "$VERSION" "$OUT_DIR" <<'PYEOF'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from packaging.build_packages import (
    build_corrux_core_spec,
    build_corrux_module_documentation_spec,
    build_corrux_module_rh_spec,
    build_deb_package,
)

version, out_dir = sys.argv[1], Path(sys.argv[2])
project_root = Path(".").resolve()

for builder in (
    build_corrux_core_spec,
    build_corrux_module_documentation_spec,
    build_corrux_module_rh_spec,
):
    spec = builder(version)
    path = build_deb_package(spec, project_root, out_dir)
    print(f"  -> {path}")
PYEOF

echo "[build_repo] Génération de l'index APT (dpkg-scanpackages)..."
cd "${OUT_DIR}"
dpkg-scanpackages --multiversion . /dev/null > Packages
gzip -kf Packages

# Release minimal — suffisant pour un dépôt "trusted=yes" local
# consommé uniquement via le preseed pendant l'installation, jamais
# publié (cf. §19.3 architecture : le canal en ligne réel utilise la
# clé de signature CORRUX, distincte de ce mécanisme d'installation
# initiale).
cat > Release <<EOF
Origin: CORRUX
Label: CORRUX local install repository
Suite: corrux
Codename: corrux
Version: ${VERSION}
Architectures: all amd64
Components: main
Description: Dépôt local CORRUX embarqué dans l'ISO d'installation
EOF

echo "[build_repo] Dépôt local prêt dans : ${OUT_DIR}"
