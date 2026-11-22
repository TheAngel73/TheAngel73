#!/bin/bash
# Costruisce Provinia.app e (se vuoi) la copia in Applicazioni.
# Uso: ./crea_app.sh            (chiede se installare)
#      ./crea_app.sh --installa (installa senza chiedere)
set -euo pipefail
cd "$(dirname "$0")"
SRC="$(cd .. && pwd)"
BUILD="$SRC/build"
APP="$BUILD/Provinia.app"

if ! command -v swiftc >/dev/null 2>&1 || ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "Servono gli strumenti per sviluppatori di Apple (gratuiti)."
  echo "Si apre ora la finestra di installazione: al termine rilancia questo script."
  xcode-select --install || true
  exit 1
fi

echo "› Compilo l'app…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app/static"
swiftc -O -swift-version 5 -o "$APP/Contents/MacOS/Provinia" Provinia.swift \
  -framework Cocoa -framework WebKit
mkdir -p "$APP/Contents/Resources/bin"
swiftc -O -swift-version 5 -o "$APP/Contents/Resources/bin/fcimg" fcimg.swift

echo "› Copio il motore e l'interfaccia…"
cp "$SRC"/*.py "$APP/Contents/Resources/app/"
cp -R "$SRC/dati" "$APP/Contents/Resources/app/"   # località per i nomi dei luoghi
cp "$SRC"/static/* "$APP/Contents/Resources/app/static/"   # anche il font Inter (inter.woff2)

echo "› Icona…"
cp Provinia.icns "$APP/Contents/Resources/AppIcon.icns"
cp "$SRC/Provinia about.png" "$APP/Contents/Resources/about.png"   # avvio e «Informazioni su»

VERSION="$(date +%Y.%m.%d)"
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
  <key>CFBundleVersion</key><string>$VERSION</string>
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

INSTALL="${1:-}"
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
