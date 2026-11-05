#!/bin/bash
# Double-click to start Deck. Keep the window open while you use the panel.
cd "$(dirname "$0")"
printf '\033]0;Deck\007'
exec python3 server.py
