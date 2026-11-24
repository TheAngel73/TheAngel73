#!/bin/bash
# Doppio clic su questo file per avviare Provinia.
cd "$(dirname "$0")"
if curl -s -o /dev/null http://localhost:8765/api/jobs; then
  # già in esecuzione: apri solo la finestra
  open "http://localhost:8765/"
  exit 0
fi
exec python3 server.py
