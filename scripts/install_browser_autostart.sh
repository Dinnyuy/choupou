#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT_DIR/autostart/wasteai-browser.desktop.template"
AUTOSTART_DIR="${AUTOSTART_DIR:-$HOME/.config/autostart}"
DEST_FILE="$AUTOSTART_DIR/wasteai-browser.desktop"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "[ERREUR] Template introuvable: $TEMPLATE"
  exit 1
fi

mkdir -p "$AUTOSTART_DIR"

TMP_RENDERED="$(mktemp)"
cleanup() {
  rm -f "$TMP_RENDERED"
}
trap cleanup EXIT

sed \
  -e "s|__PROJECT_DIR__|$ROOT_DIR|g" \
  "$TEMPLATE" > "$TMP_RENDERED"

install -m 0644 "$TMP_RENDERED" "$DEST_FILE"

echo "[OK] Autostart navigateur installe: $DEST_FILE"
echo "Au prochain login graphique, WasteAI s'ouvrira automatiquement."
