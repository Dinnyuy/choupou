#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[ERREUR] python3 introuvable. Installez Python 3.10+."
  exit 1
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ]]; then
  echo "[ERREUR] Python >= 3.10 requis (trouve: $PY_VERSION)"
  exit 1
fi

ARCH="$(uname -m)"
USE_SYSTEM_SITE_PACKAGES=0
if [[ "$ARCH" == "aarch64" || "$ARCH" == arm* || "$PY_MINOR" -ge 13 ]]; then
  USE_SYSTEM_SITE_PACKAGES=1
fi

if [[ ! -d ".venv" ]]; then
  echo "[INFO] Creation du virtualenv .venv"
  if [[ "$USE_SYSTEM_SITE_PACKAGES" -eq 1 ]]; then
    "$PYTHON_BIN" -m venv --system-site-packages .venv
  else
    "$PYTHON_BIN" -m venv .venv
  fi
elif [[ "$USE_SYSTEM_SITE_PACKAGES" -eq 1 ]]; then
  echo "[INFO] Mise a niveau du virtualenv .venv avec acces aux paquets systeme"
  "$PYTHON_BIN" -m venv --upgrade --system-site-packages .venv
fi

source .venv/bin/activate

echo "[INFO] Upgrade pip/setuptools/wheel"
python -m pip install --upgrade pip setuptools wheel

REQ_FILE="requirements/requirements-base.txt"
if [[ "$ARCH" == "aarch64" || "$ARCH" == arm* ]]; then
  REQ_FILE="requirements/requirements-rpi.txt"
fi

echo "[INFO] Installation dependances: $REQ_FILE"
python -m pip install -r "$REQ_FILE"

echo
echo "[OK] Environnement pret."
echo "Architecture detectee : $ARCH"
echo "Python               : $(python --version)"
echo
echo "Commandes suivantes:"
echo "1) VS Code: selectionnez l'interpreteur .venv/bin/python"
echo "2) Diagnostic: python scripts/diagnose_env.py"
echo "3) Smoke test: python scripts/smoke_test.py"
echo "4) Lancer app: ./run.sh"
echo
echo "Option PT (plus lourd): python -m pip install -r requirements/requirements-dev.txt"
