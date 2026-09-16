#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Plaud Note Manager"
EXECUTABLE_NAME="PlaudNoteApp"
IDENTIFIER="com.cmdspace.PlaudNoteManager"
# Single source of truth: pyproject.toml [project] version.
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT_DIR/pyproject.toml" | head -1)"
VERSION="${VERSION:-0.0.0}"
# Build number = total git commit count (monotonic, lets the user tell which
# upgrade they are running). Falls back to a timestamp outside a git checkout.
BUILD="$(git -C "$ROOT_DIR" rev-list --count HEAD 2>/dev/null || date +%y%m%d%H%M)"
GIT_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all -- . ':!dist' 2>/dev/null)" ]]; then
  GIT_SHA="${GIT_SHA}-dirty"
fi
ICON_SOURCE="${ICON_SOURCE:-$ROOT_DIR/app/Resources/AppIcon.png}"
BUILD_CONFIG="${BUILD_CONFIG:-release}"
DIST_DIR="$ROOT_DIR/dist"
DIST_BUNDLE="$DIST_DIR/$APP_NAME.app"
APPLICATIONS_DIR="${APPLICATIONS_DIR:-/Applications}"

# Keep completed/previous bundles recoverable. All construction happens in a
# unique directory; a failed build or copy never removes the working app.
mkdir -p "$DIST_DIR"
BUILD_STAGE_DIR="$(mktemp -d "$DIST_DIR/.plaud-build.XXXXXX")"
APP_BUNDLE="$BUILD_STAGE_DIR/$APP_NAME.app"

replace_bundle() {
  local staged_bundle="$1"
  local destination="$2"
  local backup="$3"
  local had_previous=0

  if [[ -e "$destination" || -L "$destination" ]]; then
    mv "$destination" "$backup"
    had_previous=1
  fi
  if ! mv "$staged_bundle" "$destination"; then
    echo "Could not install $destination; restoring the previous bundle." >&2
    if [[ "$had_previous" == 1 ]]; then
      mv "$backup" "$destination" || {
        echo "Previous bundle is preserved at: $backup" >&2
        return 1
      }
    fi
    return 1
  fi
  if [[ "$had_previous" == 1 ]]; then
    echo "Previous bundle preserved: $backup"
  fi
}

if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "missing icon source: $ICON_SOURCE" >&2
  exit 1
fi

echo "Building $EXECUTABLE_NAME ($BUILD_CONFIG)..."
swift build --package-path "$ROOT_DIR/app" -c "$BUILD_CONFIG"

BIN_DIR="$(swift build --package-path "$ROOT_DIR/app" -c "$BUILD_CONFIG" --show-bin-path)"
BUILD_BINARY="$BIN_DIR/$EXECUTABLE_NAME"
if [[ ! -x "$BUILD_BINARY" ]]; then
  echo "build binary not found: $BUILD_BINARY" >&2
  exit 1
fi

mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

cp "$BUILD_BINARY" "$APP_BUNDLE/Contents/MacOS/$EXECUTABLE_NAME"

ICONSET="$BUILD_STAGE_DIR/AppIcon.iconset"
mkdir -p "$ICONSET"

sips -z 16 16 "$ICON_SOURCE" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$ICON_SOURCE" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$ICON_SOURCE" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$ICON_SOURCE" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns"

BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SOURCE_FINGERPRINT="unknown"
if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # Hash runtime sources and templates, including uncommitted additions. Never
  # enumerate data/, credentials, virtualenvs or compiler output.
  SOURCE_FINGERPRINT="$(
    cd "$ROOT_DIR"
    git ls-files -z --cached --others --exclude-standard -- \
      app/Package.swift app/Package.resolved app/Sources app/Resources \
      core cli templates pyproject.toml uv.lock scripts/package-macos-app.sh \
      | while IFS= read -r -d '' source_file; do
          if [[ -f "$source_file" ]]; then
            shasum -a 256 "$source_file"
          fi
        done \
      | shasum -a 256 | awk '{print $1}'
  )"
fi

cat >"$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleExecutable</key>
  <string>$EXECUTABLE_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>$IDENTIFIER</string>
  <key>CFBundleVersion</key>
  <string>$BUILD</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>PlaudGitSHA</key>
  <string>$GIT_SHA</string>
  <key>PlaudBuildTimestamp</key>
  <string>$BUILD_TIMESTAMP</string>
  <key>PlaudSourceFingerprint</key>
  <string>$SOURCE_FINGERPRINT</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSQuitAlwaysKeepsWindows</key>
  <false/>
</dict>
</plist>
PLIST

codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null
plutil -lint "$APP_BUNDLE/Contents/Info.plist" >/dev/null
codesign --verify --deep --strict "$APP_BUNDLE"

replace_bundle "$APP_BUNDLE" "$DIST_BUNDLE" "$BUILD_STAGE_DIR/Previous $APP_NAME.app"
APP_BUNDLE="$DIST_BUNDLE"

DEST="$APPLICATIONS_DIR/$APP_NAME.app"
echo "Installing to $DEST..."
INSTALL_STAGE_DIR="$(mktemp -d "$APPLICATIONS_DIR/.plaud-install.XXXXXX")"
INSTALL_CANDIDATE="$INSTALL_STAGE_DIR/$APP_NAME.app"
cp -R "$APP_BUNDLE" "$INSTALL_CANDIDATE"
plutil -lint "$INSTALL_CANDIDATE/Contents/Info.plist" >/dev/null
codesign --verify --deep --strict "$INSTALL_CANDIDATE"
replace_bundle "$INSTALL_CANDIDATE" "$DEST" "$INSTALL_STAGE_DIR/Previous $APP_NAME.app"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

echo "Installed: $DEST"
echo "Built: $BUILD_TIMESTAMP; source fingerprint: $SOURCE_FINGERPRINT"
