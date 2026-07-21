#!/usr/bin/env bash
# Google TTS For NVDA - Add-on Builder (WSL/Linux)
#
# Linux/WSL counterpart to build.bat. Mirrors the same 8 steps and the same
# [n/8] / [ERROR] output so the two builds can be compared line for line.
# WSL/Linux can build, check, and package the add-on, but NVDA and the
# Chromium browser runtime only run on Windows -- this script cannot test
# either. Keep build.bat and build.sh in sync when the build steps change.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

EXIT_CODE=0

_clean_pycache() {
    find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null
    return 0
}

# Always clean generated __pycache__ before exiting, on both success and
# failure paths (including Ctrl+C), the same way build.bat's
# cleanup_and_exit label does before every "goto cleanup_and_exit".
trap '_clean_pycache' EXIT

echo "============================================"
echo "  Google TTS For NVDA - Add-on Builder (WSL/Linux)"
echo "============================================"
echo

# --------------- Read version from manifest.ini ---------------
VERSION="$(grep -m1 '^version' googleTtsForNvda/manifest.ini | cut -d'=' -f2 | xargs)"

if [ -z "${VERSION:-}" ]; then
    echo "[ERROR] Could not read version from manifest.ini."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "Version: $VERSION"
echo

# --------------- Clean build artifacts ---------------
echo "[1/8] Cleaning build artifacts..."
_clean_pycache
if [ -f "googleTtsForNvda/googleTtsForNvda.nvda-addon" ]; then
    rm -f "googleTtsForNvda/googleTtsForNvda.nvda-addon"
    echo "      Removed stale .nvda-addon from source tree."
fi
echo "      Done."
echo

# --------------- Merge conflict marker check ---------------
echo "[2/8] Checking for unresolved merge conflict markers..."
mapfile -t CONFLICT_FILES < <(find googleTtsForNvda -type f \( \
    -name '*.py' -o -name '*.js' -o -name '*.html' -o -name '*.ini' -o \
    -name '*.json' -o -name '*.bat' -o -name '*.md' -o -name '*.po' -o -name '*.pot' \))
for extra in build.bat build.sh AGENTS.md readme.md TRANSLATING.md build_i18n.py; do
    [ -f "$extra" ] && CONFLICT_FILES+=("$extra")
done

CONFLICT_MATCHES=""
if [ "${#CONFLICT_FILES[@]}" -gt 0 ]; then
    CONFLICT_MATCHES="$(grep -nE '^(<<<<<<<|=======|>>>>>>>)' "${CONFLICT_FILES[@]}" 2>/dev/null || true)"
fi
if [ -n "$CONFLICT_MATCHES" ]; then
    echo "$CONFLICT_MATCHES" | sed 's/^/      [ERROR] /'
    echo "[ERROR] Unresolved merge conflict markers found."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Passed."
echo

# --------------- Build translations ---------------
echo "[3/8] Building translations..."
I18N_ARGS=(--all-languages)
# On WSL, NVDA itself lives on the Windows side. If the Windows NVDA locale
# folder is reachable through the /mnt/c mount, use it to validate language
# codes; otherwise build_i18n.py just prints [WARN] and skips that check.
WIN_NVDA_LOCALE_DIR="/mnt/c/Program Files/NVDA/locale"
if [ -d "$WIN_NVDA_LOCALE_DIR" ]; then
    I18N_ARGS+=(--nvda-locale-dir "$WIN_NVDA_LOCALE_DIR")
fi
if ! python3 build_i18n.py "${I18N_ARGS[@]}"; then
    echo "[ERROR] Translation build failed."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Passed."
echo

# --------------- Python syntax check ---------------
echo "[4/8] Checking Python syntax..."
if ! python3 -m compileall -q googleTtsForNvda; then
    echo "[ERROR] Python syntax check failed."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Passed."
echo

# --------------- JavaScript syntax check ---------------
echo "[5/8] Checking JavaScript syntax..."
if ! node --check googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js; then
    echo "[ERROR] JavaScript syntax check failed."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Passed."
echo

# --------------- Verify no .zvoice in source ---------------
echo "[6/8] Verifying no .zvoice files in source tree..."
mapfile -t ZVOICE_FILES < <(find googleTtsForNvda -name '*.zvoice' -type f)
if [ "${#ZVOICE_FILES[@]}" -gt 0 ]; then
    for f in "${ZVOICE_FILES[@]}"; do
        echo "      [ERROR] Found .zvoice file: $f"
    done
    echo "[ERROR] Voice data files must not be in the source tree."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Clean - no .zvoice files found."
echo

# --------------- Clean __pycache__ created by compileall ---------------
echo "[7/8] Cleaning __pycache__ created by syntax check..."
_clean_pycache
echo "      Done."
echo

# --------------- Package the add-on ---------------
OUTPUT="dist/googleTtsForNvda-$VERSION.nvda-addon"
echo "[8/8] Packaging add-on to $OUTPUT ..."

mkdir -p dist

# Remove old build with same version if present
rm -f "$OUTPUT"

if ! command -v zip >/dev/null 2>&1; then
    echo "[ERROR] 'zip' command not found. Install it (e.g. 'sudo apt install zip') and retry."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi

# Zip the contents of googleTtsForNvda/ at the archive root (manifest.ini
# must land at the top level, matching what
# `Compress-Archive -Path 'googleTtsForNvda\*'` produces on Windows).
# Exclude dotfiles/dotdirs the same way Windows wildcard expansion would.
if ! (cd googleTtsForNvda && zip -rXq "../$OUTPUT" . -x ".*" -x "*/.*"); then
    echo "[ERROR] Packaging failed."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi

SIZE="$(du -b "$OUTPUT" 2>/dev/null | cut -f1)"
echo "      Created: $OUTPUT"
echo "      Size:    ${SIZE:-unknown} bytes"
echo

echo "============================================"
echo "  Build complete: $OUTPUT"
echo "============================================"
exit 0
