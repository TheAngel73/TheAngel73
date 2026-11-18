#!/bin/bash
# Costruisce FotoCatalogo.app e (se vuoi) la copia in Applicazioni.
# Uso: ./crea_app.sh            (chiede se installare)
#      ./crea_app.sh --installa (installa senza chiedere)
set -euo pipefail
cd "$(dirname "$0")"
SRC="$(cd .. && pwd)"
BUILD="$SRC/build"
APP="$BUILD/FotoCatalogo.app"

if ! command -v swiftc >/dev/null 2>&1 || ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "Servono gli strumenti per sviluppatori di Apple (gratuiti)."
  echo "Si apre ora la finestra di installazione: al termine rilancia questo script."
  xcode-select --install || true
  exit 1
fi

echo "› Compilo l'app…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app/static"
swiftc -O -swift-version 5 -o "$APP/Contents/MacOS/FotoCatalogo" FotoCatalogo.swift \
  -framework Cocoa -framework WebKit

echo "› Copio il motore e l'interfaccia…"
cp "$SRC"/*.py "$APP/Contents/Resources/app/"
cp "$SRC"/static/* "$APP/Contents/Resources/app/static/"

echo "› Creo l'icona…"
ICONSET="$BUILD/AppIcon.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s icona.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) icona.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET"

VERSION="$(date +%Y.%m.%d)"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>FotoCatalogo</string>
  <key>CFBundleDisplayName</key><string>FotoCatalogo</string>
  <key>CFBundleIdentifier</key><string>local.fotocatalogo</string>
  <key>CFBundleExecutable</key><string>FotoCatalogo</string>
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
  <key>NSDocumentsFolderUsageDescription</key><string>FotoCatalogo salva i cataloghi nella cartella Documenti/FotoCatalogo.</string>
  <key>NSNetworkVolumesUsageDescription</key><string>FotoCatalogo legge le foto dal NAS per creare le miniature. I file non vengono mai modificati.</string>
  <key>NSRemovableVolumesUsageDescription</key><string>FotoCatalogo legge le foto dai dischi esterni per creare le miniature.</string>
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
  osascript -e 'tell application "FotoCatalogo" to quit' >/dev/null 2>&1 || true
  sleep 1
  rm -rf "/Applications/FotoCatalogo.app"
  ditto "$APP" "/Applications/FotoCatalogo.app"
  echo "✓ Installata in Applicazioni. La apro…"
  open "/Applications/FotoCatalogo.app"
else
  open -R "$APP"
fi
