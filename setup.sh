#!/bin/bash
# Deck setup. Run once on the Mac:  ./setup.sh
# Safe to run again. It keeps your PIN and certificate and only refreshes what changed.
set -e
cd "$(dirname "$0")"
DECK="$(pwd)"
STATE="$HOME/.deck"
mkdir -p "$STATE/models" certs bin

step() { printf "\n\033[1m%s\033[0m\n" "$1"; }

step "1. Python"
if ! python3 -c "import ssl" 2>/dev/null; then
  echo "Python 3 is not ready. Run:  xcode-select --install   then run this script again."
  exit 1
fi
echo "OK: $(python3 --version)"

if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Created config.json from config.example.json. Edit it to change your buttons."
fi

step "2. PIN for the iPad"
CURRENT=$(python3 -c 'import json;print(json.load(open("config.json")).get("pin",""))')
if [ ${#CURRENT} -ge 4 ]; then
  echo "A PIN is already set. To change it, edit \"pin\" in config.json."
else
  while true; do
    read -r -s -p "Choose a PIN, 4 to 12 digits: " PIN; echo
    if [[ "$PIN" =~ ^[0-9]{4,12}$ ]]; then break; fi
    echo "Digits only, 4 to 12 of them."
  done
  PIN="$PIN" python3 - <<'PY'
import os, re
p = "config.json"
s = open(p, encoding="utf-8").read()
s = re.sub(r'"pin":\s*"[^"]*"', '"pin": "%s"' % os.environ["PIN"], s, count=1)
open(p, "w", encoding="utf-8").write(s)
PY
  chmod 600 config.json
  echo "PIN saved."
fi

step "3. Certificate (lets the iPad microphone work)"
HOST="$(scutil --get LocalHostName).local"
if [ ! -f certs/ca.key ]; then
  cat > certs/ca.cnf <<CNF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = Deck Local CA
O = Deck
[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
CNF
  openssl genrsa -out certs/ca.key 2048 2>/dev/null
  openssl req -x509 -new -nodes -key certs/ca.key -sha256 -days 3650 \
    -config certs/ca.cnf -extensions v3_ca -out certs/ca.crt
  chmod 600 certs/ca.key
  echo "Created a private certificate authority for this Mac only."
fi
SAN="DNS:$HOST,DNS:localhost,IP:127.0.0.1"
for IF in en0 en1 en2 bridge100; do
  IP=$(ipconfig getifaddr "$IF" 2>/dev/null || true)
  [ -n "$IP" ] && SAN="$SAN,IP:$IP"
done
cat > certs/server.ext <<EXT
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = $SAN
EXT
openssl genrsa -out certs/server.key 2048 2>/dev/null
openssl req -new -key certs/server.key -subj "/CN=$HOST" -out certs/server.csr
openssl x509 -req -in certs/server.csr -CA certs/ca.crt -CAkey certs/ca.key -CAcreateserial \
  -out certs/server.crt -days 800 -sha256 -extfile certs/server.ext 2>/dev/null
chmod 600 certs/server.key
echo "Certificate issued for $HOST"

step "4. Calendar helper"
if command -v swiftc >/dev/null 2>&1; then
  swiftc -O tools/agenda.swift -o bin/agenda \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker tools/Info.plist
  echo "Built. macOS will now ask for calendar access. Click Allow."
  bin/agenda list --days 1 >/dev/null || echo "Calendar access was not granted. The panel will say so; allow it later in System Settings."
else
  echo "Skipped: swiftc missing. Run  xcode-select --install  then run this script again."
fi

step "5. Voice transcription (optional, free, runs on this Mac)"
MODEL="$STATE/models/ggml-base.en.bin"
if [ -f "$MODEL" ] && (command -v whisper-cli >/dev/null 2>&1 || [ -x /opt/homebrew/bin/whisper-cli ]); then
  echo "Already set up."
else
  read -r -p "Set it up now? Downloads about 150 MB. [y/N] " ANS
  if [[ "$ANS" =~ ^[Yy] ]]; then
    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew is needed for this step. Install it from https://brew.sh then run this script again."
    else
      brew install whisper-cpp
      curl -L --fail -o "$MODEL" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
      echo "Transcription ready."
    fi
  else
    echo "Skipped. Voice notes will save as audio until you run this again."
  fi
fi

step "6. Start script"
chmod +x deck.command background.sh
echo "To try it: double-click deck.command (Deck runs while that window is open)."
echo "To keep it running: bash background.sh on (starts now and at every login, no window)."

step "7. Permissions"
echo "Deck presses keys for you (Claude, Teams, lock screen). macOS needs to allow that."
echo "Opening Accessibility settings: switch on Terminal."
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" || true

step "Done"
echo "Start Deck, then on the iPad open:  http://$HOST:8080"
