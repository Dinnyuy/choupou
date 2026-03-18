#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-wasteai.service}"
SERVICE_TEMPLATE="$ROOT_DIR/systemd/wasteai.service.template"
SERVICE_DEST="/etc/systemd/system/$SERVICE_NAME"

RUN_AS_USER="${RUN_AS_USER:-$(id -un)}"
RUN_AS_GROUP="${RUN_AS_GROUP:-$(id -gn)}"
USER_HOME="${USER_HOME:-$(getent passwd "$RUN_AS_USER" | cut -d: -f6)}"

if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
  echo "[ERREUR] Template introuvable: $SERVICE_TEMPLATE"
  exit 1
fi

if [[ -z "$USER_HOME" ]]; then
  echo "[ERREUR] Impossible de resoudre HOME pour l'utilisateur $RUN_AS_USER"
  exit 1
fi

SUDO_CMD=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO_CMD="sudo"
  else
    echo "[ERREUR] Ce script doit etre execute en root ou avec sudo disponible."
    exit 1
  fi
fi

TMP_RENDERED="$(mktemp)"
cleanup() {
  rm -f "$TMP_RENDERED"
}
trap cleanup EXIT

sed \
  -e "s|__PROJECT_DIR__|$ROOT_DIR|g" \
  -e "s|__RUN_AS_USER__|$RUN_AS_USER|g" \
  -e "s|__RUN_AS_GROUP__|$RUN_AS_GROUP|g" \
  -e "s|__USER_HOME__|$USER_HOME|g" \
  "$SERVICE_TEMPLATE" > "$TMP_RENDERED"

echo "[INFO] Installation du service $SERVICE_NAME"
$SUDO_CMD install -D -m 0644 "$TMP_RENDERED" "$SERVICE_DEST"

echo "[INFO] Rechargement de systemd"
$SUDO_CMD systemctl daemon-reload

echo "[INFO] Activation au demarrage"
$SUDO_CMD systemctl enable "$SERVICE_NAME"

echo "[INFO] Redemarrage du service"
$SUDO_CMD systemctl restart "$SERVICE_NAME"

echo "[INFO] Etat du service"
$SUDO_CMD systemctl --no-pager --full status "$SERVICE_NAME" || true

echo
echo "[OK] Service installe: $SERVICE_NAME"
echo "Verifier les logs:"
echo "  sudo journalctl -u $SERVICE_NAME -f"
