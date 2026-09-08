#!/usr/bin/env bash

API="http://127.0.0.1:8581"
TOKEN_FILE="/var/lib/homebridge/roborockPauseSchedules/homebridge-access-token"

echo "===== BEGIN HOMEBRIDGE TOKEN SETUP ====="
echo
read -r -p "Homebridge username: " HB_USER
read -r -s -p "Homebridge password: " HB_PASS
echo
echo
echo "--- Authenticating ---"

RESPONSE=$(curl -sS \
  --connect-timeout 5 \
  --max-time 30 \
  -H "Content-Type: application/json" \
  -X POST \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"username":sys.argv[1],"password":sys.argv[2]}))' "$HB_USER" "$HB_PASS")" \
  "$API/api/auth/login")
curl_status=$?
unset HB_PASS

if [ "$curl_status" -ne 0 ]; then
    echo "ERROR: Authentication request failed (curl status $curl_status)."
    return 1 2>/dev/null || exit 1
fi

TOKEN=$(printf "%s" "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("access_token", ""))' 2>/dev/null)
parse_status=$?
unset RESPONSE

if [ "$parse_status" -ne 0 ] || [ -z "$TOKEN" ]; then
    echo "ERROR: Authentication did not return a usable access token."
    return 1 2>/dev/null || exit 1
fi

temporary_file=$(mktemp)
printf "%s" "$TOKEN" >"$temporary_file"
unset TOKEN
chmod 600 "$temporary_file"

sudo install -o homebridge -g homebridge -m 600 "$temporary_file" "$TOKEN_FILE"
install_status=$?
rm -f "$temporary_file"

if [ "$install_status" -ne 0 ]; then
    echo "ERROR: Unable to install the token file (status $install_status)."
    return 1 2>/dev/null || exit 1
fi

echo "Token saved securely."
echo "Token file: $TOKEN_FILE"
echo "Owner: homebridge:homebridge"
echo "Permissions: 600"
echo
echo "The token value was NOT displayed."
echo
echo "===== END HOMEBRIDGE TOKEN SETUP ====="
