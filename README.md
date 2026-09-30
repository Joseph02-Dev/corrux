# CORRUX — corrux-core

Distribution Linux SI local pour PME/TPE. Voir `vision-produit-v1.md` et
`architecture-technique-v1.md` (documents de référence du projet Claude)
pour le produit et l'architecture.

## Installation (production)

Sur Debian 12/13, Ubuntu 22.04/24.04/26.04 LTS ou Proxmox VE 8/9 (amd64) :

```bash
curl -fsSL https://joseph02-dev.github.io/corrux/install.sh | sudo sh
```

ou par apt — cf. [docs/installation.md](docs/installation.md). Publication
d'une version : [docs/publication.md](docs/publication.md). L'ISO
bootable (`iso/`) reste disponible en option pour une machine vierge.

## Démarrage local (développement)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis renseigner DJANGO_SECRET_KEY, DB_*
python manage.py create_schemas   # crée les schémas core/documentation/rh
python manage.py migrate
python manage.py runserver
```

Pour le développement, installer aussi les dépendances de test/lint :

```bash
pip install -r requirements-dev.txt
```

## Tests

```bash
pytest
```

## Lint

```bash
ruff check .
```
