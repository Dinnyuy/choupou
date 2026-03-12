#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "[INFO] .venv absent -> lancement de ./bootstrap.sh"
  ./bootstrap.sh
fi

source .venv/bin/activate

if [[ ! -f ".env" && -f ".env.example" ]]; then
  echo "[INFO] Creation de .env depuis .env.example"
  cp .env.example .env
fi

echo "[INFO] Verification prealable du projet"
python scripts/preflight.py

if [[ -f ".env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
fi

echo "[INFO] Demarrage Flask sur ${FLASK_HOST:-0.0.0.0}:${FLASK_PORT:-5000}"
python app.py
