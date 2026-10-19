#!/bin/bash
set -e
cd "$(dirname "$0")"

python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    sys.exit("Python 3.9 ou supérieur est requis.")
PY

if [ ! -d .venv ]; then
  echo "Création de l'environnement virtuel…"
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --quiet --disable-pip-version-check -r requirements.txt

echo "Démarrage sur http://127.0.0.1:8888"
(sleep 1 && open "http://127.0.0.1:8888") &

exec python app.py
