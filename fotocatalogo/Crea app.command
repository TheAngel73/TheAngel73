#!/bin/bash
# Doppio clic: costruisce FotoCatalogo.app e la installa in Applicazioni.
# Rilancialo ogni volta che aggiorni il codice.
cd "$(dirname "$0")"
./mac/crea_app.sh
echo
read -r -p "Premi Invio per chiudere questa finestra."
