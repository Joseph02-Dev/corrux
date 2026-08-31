# CORRUX — corrux-core

Distribution Linux SI local pour PME/TPE. Voir `vision-produit-v1.md` et
`architecture-technique-v1.md` (documents de référence du projet Claude)
pour le produit et l'architecture.

## Démarrage local (développement)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis renseigner DJANGO_SECRET_KEY, DB_*
python manage.py create_schemas   # crée les schémas core/documentation/rh
python manage.py migrate
python manage.py runserver
```
