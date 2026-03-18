#!/usr/bin/env bash
set -euo pipefail

APP_URL="${WASTEAI_BROWSER_URL:-http://127.0.0.1:5000/login}"
BROWSER_BIN="${WASTEAI_BROWSER_BIN:-/usr/bin/chromium}"
WAIT_SECONDS="${WASTEAI_BROWSER_WAIT_SECONDS:-90}"
BROWSER_MODE="${WASTEAI_BROWSER_MODE:-window}"

if [[ ! -x "$BROWSER_BIN" ]]; then
  echo "[ERREUR] Navigateur introuvable: $BROWSER_BIN" >&2
  exit 1
fi

if pgrep -f "$BROWSER_BIN.*$APP_URL" >/dev/null 2>&1; then
  exit 0
fi

deadline=$((SECONDS + WAIT_SECONDS))
until curl -fsI "$APP_URL" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "[ERREUR] L'application n'a pas repondu avant le delai imparti: $APP_URL" >&2
    exit 1
  fi
  sleep 2
done

browser_args=(
  "--disable-session-crashed-bubble"
  "--disable-infobars"
  "--no-first-run"
  "--noerrdialogs"
)

if [[ "$BROWSER_MODE" == "app" ]]; then
  browser_args+=("--app=$APP_URL" "--start-maximized")
elif [[ "$BROWSER_MODE" == "window" ]]; then
  browser_args+=("--new-window" "--start-maximized" "$APP_URL")
else
  browser_args+=("--kiosk" "$APP_URL")
fi

exec "$BROWSER_BIN" "${browser_args[@]}"
