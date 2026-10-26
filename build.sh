#!/usr/bin/env bash
# Google TTS For NVDA - Add-on Builder (WSL/Linux)
#
# Linux/WSL counterpart to build.bat. Mirrors the same 6 steps and the same
# [n/6] / [ERROR] output so the two builds can be compared line for line.
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
echo "[1/6] Cleaning build artifacts..."
_clean_pycache
if [ -f "googleTtsForNvda/googleTtsForNvda.nvda-addon" ]; then
    rm -f "googleTtsForNvda/googleTtsForNvda.nvda-addon"
    echo "      Removed stale .nvda-addon from source tree."
fi
if [ -d "googleTtsForNvda/doc" ]; then
    find googleTtsForNvda/doc \( -name "*.mo" -o -name "*.po" -o -name "*.md" \) -type f -delete 2>/dev/null || true
fi
echo "      Done."
echo

# --------------- Merge conflict marker check ---------------
echo "[2/6] Checking for unresolved merge conflict markers..."
if ! python3 -B -c "import pathlib, sys; exts = {'.py', '.js', '.html', '.ini', '.json', '.bat', '.sh', '.md', '.po', '.pot', '.toml'}; files = [p for d in ('googleTtsForNvda', 'tests') if pathlib.Path(d).is_dir() for p in sorted(pathlib.Path(d).rglob('*')) if p.is_file() and p.suffix in exts] + [p for p in sorted(pathlib.Path('.').iterdir()) if p.is_file() and p.suffix in exts]; hits = [(p, i, s) for p in files for i, s in enumerate(p.read_text(encoding='utf-8', errors='replace').splitlines(), 1) if s in ('<<<<<<<', '=======', '>>>>>>>') or s.startswith(('<<<<<<< ', '>>>>>>> '))]; [print(f'      [ERROR] {p}:{i}: {s.strip()}') for p, i, s in hits]; sys.exit(1 if hits else 0)"; then
    echo "[ERROR] Unresolved merge conflict markers found."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Passed."
echo

# --------------- Python syntax check ---------------
echo "[3/6] Checking Python syntax..."
if ! python3 -B -c "import pathlib; [compile(p.read_bytes(), str(p), 'exec') for p in sorted(pathlib.Path('googleTtsForNvda').rglob('*.py'))]"; then
    echo "[ERROR] Python syntax check failed."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Passed."
echo

# --------------- JavaScript syntax check ---------------
echo "[4/6] Checking JavaScript syntax..."
if ! node --check googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js; then
    echo "[ERROR] JavaScript syntax check failed."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi
echo "      Passed."
echo

# --------------- Verify no .zvoice in source ---------------
echo "[5/6] Verifying no .zvoice files in source tree..."
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

# --------------- Package the add-on ---------------
OUTPUT="dist/googleTtsForNvda-$VERSION.nvda-addon"
echo "[6/6] Packaging add-on to $OUTPUT ..."

mkdir -p dist

# Remove old build with same version if present
rm -f "$OUTPUT"

# Package the contents of googleTtsForNvda/ at the archive root (manifest.ini
# must land at the top level) using Python's standard zipfile with DEFLATE
# level 9 compression, excluding dotfiles/dotdirs.
if ! python3 -B -c "import pathlib, sys, zipfile; root = pathlib.Path('googleTtsForNvda'); zf = zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED, compresslevel=9); [zf.write(p, p.relative_to(root).as_posix()) for p in sorted(root.rglob('*')) if p.is_file() and not any(part.startswith('.') for part in p.relative_to(root).parts)]; zf.close()" "$OUTPUT"; then
    echo "[ERROR] Packaging failed."
    EXIT_CODE=1
    exit "$EXIT_CODE"
fi

SIZE="$(wc -c < "$OUTPUT" 2>/dev/null | tr -d ' ')"
echo "      Created: $OUTPUT"
echo "      Size:    ${SIZE:-unknown} bytes"
echo

echo "============================================"
echo "  Build complete: $OUTPUT"
echo "============================================"
exit 0
