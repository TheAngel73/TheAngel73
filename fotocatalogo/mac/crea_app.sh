#!/bin/bash
# Costruisce Provinia.app e (se vuoi) la copia in Applicazioni.
# Uso: ./crea_app.sh                  (chiede se installare)
#      ./crea_app.sh --installa       (installa senza chiedere)
#      ./crea_app.sh --senza-domande  (crea e basta: per la costruzione automatica su GitHub)
#      ./crea_app.sh --python DIR     (mette dentro l'app il Python della cartella DIR, così
#                                      funziona anche sui Mac senza strumenti per sviluppatori)
# L'app è solo per Mac con processore Apple (M1 e successivi).
set -euo pipefail
cd "$(dirname "$0")"
SRC="$(cd .. && pwd)"
BUILD="$SRC/build"
APP="$BUILD/Provinia.app"
INSTALL=""; ASK=1; PYDIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --installa) INSTALL="--installa";;
    --senza-domande) ASK=0;;
    --python) PYDIR="$2"; shift;;
  esac
  shift
done
TARGET="arm64-apple-macos12.3"

if ! command -v swiftc >/dev/null 2>&1 || ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "Servono gli strumenti per sviluppatori di Apple (gratuiti)."
  echo "Si apre ora la finestra di installazione: al termine rilancia questo script."
  xcode-select --install || true
  exit 1
fi

echo "› Compilo l'app…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app/static"
swiftc -O -swift-version 5 -target "$TARGET" -o "$APP/Contents/MacOS/Provinia" Provinia.swift \
  -framework Cocoa -framework WebKit
mkdir -p "$APP/Contents/Resources/bin"
swiftc -O -swift-version 5 -target "$TARGET" -o "$APP/Contents/Resources/bin/fcimg" fcimg.swift

echo "› Copio il motore e l'interfaccia…"
cp "$SRC"/*.py "$APP/Contents/Resources/app/"
cp -R "$SRC/dati" "$APP/Contents/Resources/app/"   # località per i nomi dei luoghi
cp "$SRC"/static/* "$APP/Contents/Resources/app/static/"   # anche il font Inter (inter.woff2)

if [ -n "$PYDIR" ]; then
  echo "› Python dentro l'app…"
  ditto "$PYDIR" "$APP/Contents/Resources/python"
  # parti di Python che Provinia non usa (test, editor, interfacce Tk)
  for d in test idlelib turtledemo tkinter ensurepip lib2to3; do
    rm -rf "$APP/Contents/Resources/python/lib/python3."*/"$d"
  done
  "$APP/Contents/Resources/python/bin/python3" -c "import sqlite3, zlib, json; print('  Python', __import__('sys').version.split()[0])"
fi

echo "› Icona…"
cp Provinia.icns "$APP/Contents/Resources/AppIcon.icns"
cp "$SRC/Provinia about.png" "$APP/Contents/Resources/about.png"   # avvio e «Informazioni su»

# numero di versione dal file VERSIONE (es. 1.0); la build è la data di costruzione
VERSION="$(tr -d ' \n' < "$SRC/VERSIONE" 2>/dev/null)"; [ -n "$VERSION" ] || VERSION="$(date +%Y.%m.%d)"
BUILD="$(date +%Y.%m.%d)"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Provinia</string>
  <key>CFBundleDisplayName</key><string>Provinia</string>
  <key>CFBundleIdentifier</key><string>local.fotocatalogo</string>
  <key>CFBundleExecutable</key><string>Provinia</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD</string>
  <key>CFBundleDevelopmentRegion</key><string>it</string>
  <key>LSMinimumSystemVersion</key><string>12.3</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.photography</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Catalogo foto personale</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Provinia salva i cataloghi nella cartella Documenti/Provinia.</string>
  <key>NSNetworkVolumesUsageDescription</key><string>Provinia legge le foto dal NAS per mostrarle e per le operazioni sui file che chiedi tu.</string>
  <key>NSRemovableVolumesUsageDescription</key><string>Provinia legge le foto dai dischi esterni.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST

echo "› Firma locale…"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "  (firma non riuscita: l'app funziona lo stesso su questo Mac)"

echo "✓ App pronta: $APP"

[ "$ASK" = 1 ] || exit 0
if [ "$INSTALL" != "--installa" ]; then
  read -r -p "Copiarla nella cartella Applicazioni? [S/n] " ans
  case "${ans:-s}" in [sSyY]*) INSTALL="--installa";; esac
fi
if [ "$INSTALL" = "--installa" ]; then
  osascript -e 'tell application id "local.fotocatalogo" to quit' >/dev/null 2>&1 || true
  sleep 1
  # la versione con il vecchio nome viene sostituita da Provinia
  if [ -d "/Applications/FotoCatalogo.app" ]; then
    rm -rf "/Applications/FotoCatalogo.app"
    echo "  (rimossa la vecchia FotoCatalogo.app: ora si chiama Provinia)"
  fi
  rm -rf "/Applications/Provinia.app"
  ditto "$APP" "/Applications/Provinia.app"
  echo "✓ Installata in Applicazioni. La apro…"
  open "/Applications/Provinia.app"
else
  open -R "$APP"
fi
