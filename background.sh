#!/bin/bash
# Run Deck in the background, started at login, with no Terminal window.
#
#   bash ~/deck/background.sh on       start now and at every login
#   bash ~/deck/background.sh off      stop, and do not start at login
#   bash ~/deck/background.sh restart  after you change server.py
#   bash ~/deck/background.sh status   is it running?
#
# Messages from Deck go to ~/deck/deck.log

cd "$(dirname "$0")"
DECK="$(pwd)"
LABEL="com.thedeck.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
PY="$(xcrun -f python3 2>/dev/null || echo /usr/bin/python3)"

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DECK/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DECK</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$DECK/deck.log</string>
  <key>StandardErrorPath</key><string>$DECK/deck.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
</dict>
</plist>
PL
}

running() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

case "${1:-}" in
  on)
    if lsof -nP -iTCP:8443 -sTCP:LISTEN >/dev/null 2>&1 && ! running; then
      echo "Deck is already running in a Terminal window. Close that window first, then run this again."
      exit 1
    fi
    write_plist
    running && launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
    launchctl bootstrap "$DOMAIN" "$PLIST" && sleep 2
    if running; then
      echo "Deck is running in the background and will start at every login."
      echo
      echo "Next: tap a tile on the iPad (Lock is a good test). macOS will ask to allow"
      echo "\"python3\" or \"Python\". Allow it. If no prompt appears, open System Settings,"
      echo "Privacy and Security, Accessibility, press +, and add:"
      echo "  $PY"
      echo "Then open the Calendar card on the iPad and allow calendar access the same way."
    else
      echo "It did not start. The last lines of deck.log:"; tail -15 "$DECK/deck.log"
    fi
    ;;
  off)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
    rm -f "$PLIST"
    echo "Deck stopped and will not start at login. deck.command still works."
    ;;
  restart)
    launchctl kickstart -k "$DOMAIN/$LABEL" && echo "Restarted."
    ;;
  status)
    if running; then echo "Running."; else echo "Not running."; fi
    tail -5 "$DECK/deck.log" 2>/dev/null
    ;;
  *)
    echo "Use: bash ~/deck/background.sh on | off | restart | status"
    ;;
esac
