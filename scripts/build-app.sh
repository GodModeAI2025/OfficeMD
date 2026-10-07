#!/bin/bash
# Baut dist/OfficeMD.app: SwiftUI-Oberfläche, eingebettetes Python 3.12 mit OfficeMD und den
# KI-SDKs, Knowledge Distiller. Signiert jedes Binary und das Bundle.
#
#   scripts/build-app.sh                 # Identität automatisch: Developer ID, sonst Apple Development
#   SIGN_IDENTITY=- scripts/build-app.sh # ad hoc signieren
#   PYTHON_VERSION=3.12 scripts/build-app.sh
#   NOTARY_PROFILE=officemd scripts/build-app.sh   # zusätzlich notarisieren und Ticket anheften
#
# Notarisierung braucht einmalig ein Profil mit App-spezifischem Passwort:
#   xcrun notarytool store-credentials officemd --apple-id <apple-id> --team-id <team-id>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/OfficeMD.app"
CONTENTS="$APP/Contents"
RES="$CONTENTS/Resources"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
BUNDLE_ID="${BUNDLE_ID:-com.godmodeai2025.officemd}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"

step() { printf '\n==> %s\n' "$1"; }

if [ -z "${SIGN_IDENTITY:-}" ]; then
  SIGN_IDENTITY="$(security find-identity -v -p codesigning | sed -n 's/.*) \([0-9A-F]\{40\}\) "Developer ID Application.*/\1/p' | head -1)"
  [ -n "$SIGN_IDENTITY" ] || SIGN_IDENTITY="$(security find-identity -v -p codesigning | sed -n 's/.*) \([0-9A-F]\{40\}\) "Apple Development.*/\1/p' | head -1)"
  [ -n "$SIGN_IDENTITY" ] || SIGN_IDENTITY="-"
fi
IDENTITY_NAME="$(security find-identity -v -p codesigning | grep "$SIGN_IDENTITY" | sed 's/.*"\(.*\)"/\1/' | head -1)"
[ "$SIGN_IDENTITY" = "-" ] && IDENTITY_NAME="ad hoc"

step "Submodule und Swift-Build"
git -C "$ROOT" submodule update --init --quiet
(cd "$ROOT/app" && swift build -c release --arch arm64 --quiet)
BIN="$(cd "$ROOT/app" && swift build -c release --arch arm64 --show-bin-path)/OfficeMDApp"

step "Bundle-Struktur"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$RES/bin"
cp "$BIN" "$CONTENTS/MacOS/OfficeMD"
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>OfficeMD</string>
  <key>CFBundleDisplayName</key><string>OfficeMD</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>OfficeMD</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT-Lizenz</string>
</dict>
</plist>
PLIST

step "Icon"
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
swift "$ROOT/scripts/make-icon.swift" "$ICONSET/base.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ICONSET/base.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$ICONSET/base.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
rm "$ICONSET/base.png"
iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"

step "Python $PYTHON_VERSION einbetten"
UV_PYTHON_PREFERENCE=only-managed uv python install "$PYTHON_VERSION" --quiet
# Echte Installation, nicht die .venv des Projekts: ohne Projektbezug suchen, Symlinks auflösen.
PY_SRC="$(cd / && UV_PYTHON_PREFERENCE=only-managed uv python find --no-project "$PYTHON_VERSION")"
PY_REAL="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$PY_SRC")"
PY_HOME="$(cd "$(dirname "$PY_REAL")/.." && pwd)"
case "$PY_HOME" in *"/.venv"*) echo "Python-Installation nicht gefunden ($PY_HOME)" >&2; exit 1;; esac
ditto "$PY_HOME" "$RES/python"
PYLIB="$(ls -d "$RES"/python/lib/python3.*)"
rm -rf "$PYLIB/test" "$PYLIB/idlelib" "$PYLIB/tkinter" "$PYLIB/turtledemo" "$PYLIB/ensurepip" \
       "$PYLIB/lib-dynload/_tkinter"*.so "$RES"/python/lib/tcl* "$RES"/python/lib/tk* "$RES"/python/lib/itcl* \
       "$RES"/python/lib/thread* "$RES/python/include" "$RES/python/share" "$PYLIB/EXTERNALLY-MANAGED"
rm -f "$RES"/python/bin/idle* "$RES"/python/bin/2to3* "$RES"/python/bin/pydoc*
PY="$RES/python/bin/python3"

step "OfficeMD und SDKs installieren"
uv pip install --quiet --python "$PY" --no-cache "$ROOT[ai]"
"$PY" -m compileall -q "$PYLIB/site-packages" >/dev/null || true

step "Knowledge Distiller"
mkdir -p "$RES/knowledge-distiller"
for item in scripts schema profiles viewer SKILL.md SPEC.md LICENSE README.md; do
  cp -R "$ROOT/vendor/knowledge-distiller/$item" "$RES/knowledge-distiller/"
done
find "$RES/knowledge-distiller" -name __pycache__ -prune -exec rm -rf {} +

cat > "$RES/bin/officemd" <<'WRAP'
#!/bin/sh
# officemd aus dem App-Bundle: eingebettetes Python, eingebetteter Distiller, keine .pyc.
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export OFFICEMD_DISTILLER="$HERE/knowledge-distiller"
exec "$HERE/python/bin/python3" -E -s -B -m officemd.cli "$@"
WRAP
chmod +x "$RES/bin/officemd"

step "Symlinks prüfen"
find "$APP" -type l | while read -r link; do
  target="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$link")"
  case "$target" in "$APP"/*) ;; *) echo "Symlink zeigt aus dem Bundle: $link -> $target" >&2; exit 1;; esac
done

step "Signieren ($IDENTITY_NAME)"
ENTITLEMENTS="$ROOT/scripts/officemd.entitlements"
RUNTIME=()
case "$IDENTITY_NAME" in
  "Developer ID Application"*) RUNTIME=(--options runtime --timestamp) ;;  # notarisierbar
  "ad hoc") ;;
  *) RUNTIME=(--options runtime --timestamp=none) ;;
esac
# Erst alle Mach-O-Dateien im Python, dann das Bundle.
find "$RES/python" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0 |
  while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q "Mach-O"; then
      codesign --force --sign "$SIGN_IDENTITY" ${RUNTIME[@]+"${RUNTIME[@]}"} --entitlements "$ENTITLEMENTS" "$f" 2>/dev/null
    fi
  done
codesign --force --sign "$SIGN_IDENTITY" ${RUNTIME[@]+"${RUNTIME[@]}"} --entitlements "$ENTITLEMENTS" "$APP"

step "Prüfen"
codesign --verify --deep --strict "$APP"
"$RES/bin/officemd" --version
"$RES/bin/officemd" config show | tail -3
SELFTEST="$("$RES/bin/officemd" selftest)"
echo "$SELFTEST"
echo "$SELFTEST" | grep -q FEHLER && { echo "Selbsttest im Bundle fehlgeschlagen" >&2; exit 1; }

(cd "$DIST" && rm -f OfficeMD.zip && ditto -c -k --keepParent OfficeMD.app OfficeMD.zip)

if [ -n "${NOTARY_PROFILE:-}" ]; then
  step "Notarisieren (Profil $NOTARY_PROFILE)"
  xcrun notarytool submit "$DIST/OfficeMD.zip" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  (cd "$DIST" && rm -f OfficeMD.zip && ditto -c -k --keepParent OfficeMD.app OfficeMD.zip)
  spctl -a -vv "$APP"
else
  echo
  echo "Nicht notarisiert: Auf anderen Macs blockiert Gatekeeper die App. Mit NOTARY_PROFILE=… notarisieren."
fi
echo
echo "Fertig: $APP ($(du -sh "$APP" | cut -f1)), signiert mit: $IDENTITY_NAME"
echo "Zip:    $DIST/OfficeMD.zip"
