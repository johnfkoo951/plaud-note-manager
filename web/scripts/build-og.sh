#!/usr/bin/env bash
# Build OG images (1200×630 PNG) from HTML templates.
# Usage: ./scripts/build-og.sh  (run from project root)
#
# Templates live in /assets/og/templates/*.html and reference
# /assets/og/templates/logo-round.png (copied in on first run).
# Output: /assets/og/og-{name}.png

set -euo pipefail

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TPL_DIR="$ROOT/assets/og/templates"
OUT_DIR="$ROOT/assets/og"

[ -x "$CHROME" ] || { echo "❌ Chrome not found at $CHROME"; exit 1; }
[ -d "$TPL_DIR" ] || { echo "❌ Template dir not found: $TPL_DIR"; exit 1; }

# Ensure logo is present alongside templates (local ref, no network dependency)
cp -n "$ROOT/assets/logos/cmds-logo-round.png"       "$TPL_DIR/logo-round.png" 2>/dev/null || true
cp -n "$ROOT/assets/logos/cmds-logo-typo-black.png"  "$TPL_DIR/logo-typo-black.png" 2>/dev/null || true
cp -n "$ROOT/assets/logos/cmds-logo-typo-white.png"  "$TPL_DIR/logo-typo-white.png" 2>/dev/null || true

cd "$TPL_DIR"

for tpl in og-*.html; do
    name="${tpl%.html}"        # og-landing
    out="$OUT_DIR/$name.png"
    echo "→ $tpl  →  $out"
    "$CHROME" --headless=new --disable-gpu --no-sandbox \
        --window-size=1200,630 --hide-scrollbars \
        --virtual-time-budget=3000 \
        --screenshot="$out" \
        "file://$TPL_DIR/$tpl" 2>&1 | tail -1
done

echo ""
echo "✅ OG images rebuilt:"
ls -la "$OUT_DIR"/*.png
