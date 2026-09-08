#!/usr/bin/env bash

set -euo pipefail

API="http://127.0.0.1:8581"
AUTH_DIR="/var/lib/homebridge/roborockPauseSchedules/controller"
TOKEN_FILE="$AUTH_DIR/homebridge-access-token"
CREDENTIALS_FILE="$AUTH_DIR/homebridge-api-credentials.json"

cleanup() {
    rm -f \
        "${credentials_temp:-}" \
        "${token_temp:-}" \
        "${username_temp:-}" \
        "${password_temp:-}"
}
trap cleanup EXIT

echo "===== BEGIN HOMEBRIDGE AUTOMATIC AUTH SETUP ====="
echo
echo "Use a dedicated Homebridge automation account, not your primary admin account."

credentials_temp=$(mktemp)
token_temp=$(mktemp)
username_temp=$(mktemp)
password_temp=$(mktemp)
chmod 600 "$credentials_temp" "$token_temp" "$username_temp" "$password_temp"

read -r -p "Homebridge automation username: " HB_USER
read -r -s -p "Homebridge automation password: " HB_PASS
echo

if [ -z "$HB_USER" ] || [ -z "$HB_PASS" ]; then
    echo "ERROR: Username and password must not be empty." >&2
    exit 1
fi

printf '%s' "$HB_USER" >"$username_temp"
printf '%s' "$HB_PASS" >"$password_temp"
unset HB_USER HB_PASS

python3 - \
    "$API" \
    "$credentials_temp" \
    "$token_temp" \
    "$username_temp" \
    "$password_temp" <<'PY'
import json
import pathlib
import sys
import urllib.error
import urllib.request

api = sys.argv[1]
credentials_path, token_path, username_path, password_path = map(
    pathlib.Path, sys.argv[2:6]
)
username = username_path.read_text(encoding="utf-8")
password = password_path.read_text(encoding="utf-8")

if not username or not password:
    raise SystemExit("ERROR: Username and password must not be empty")

credentials = {"username": username, "password": password}
request = urllib.request.Request(
    api + "/api/auth/login",
    data=json.dumps(credentials).encode("utf-8"),
    headers={"Content-Type": "application/json", "Accept": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    raise SystemExit(f"ERROR: Homebridge login failed with HTTP {exc.code}") from exc
except (OSError, ValueError, urllib.error.URLError) as exc:
    raise SystemExit(f"ERROR: Homebridge login failed: {exc}") from exc

token = payload.get("access_token") if isinstance(payload, dict) else None
if not isinstance(token, str) or not token:
    raise SystemExit("ERROR: Homebridge login did not return an access token")
credentials_path.write_text(json.dumps(credentials), encoding="utf-8")
token_path.write_text(token, encoding="utf-8")
PY

sudo install -d -o homebridge -g homebridge -m 755 "$AUTH_DIR"
sudo install -o homebridge -g homebridge -m 600 "$credentials_temp" "$CREDENTIALS_FILE"
sudo install -o homebridge -g homebridge -m 600 "$token_temp" "$TOKEN_FILE"

echo
echo "Automatic authentication configured."
echo "Credentials: $CREDENTIALS_FILE"
echo "Token cache: $TOKEN_FILE"
echo "Owner: homebridge:homebridge"
echo "Permissions: 600"
echo "No secret was displayed."
echo
echo "===== END HOMEBRIDGE AUTOMATIC AUTH SETUP ====="
