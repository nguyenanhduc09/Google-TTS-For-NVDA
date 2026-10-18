"""Download the Bun runtime and place ``bun.exe`` inside the add-on package.

One implementation shared by ``build.bat``, ``build.sh`` and the GitHub Actions
build workflow, so the three cannot drift.

The add-on drives Google's WASM TTS engine through a bundled Bun executable
instead of a headless Chromium browser (see
``googleTtsForNvda/synthDrivers/googleTtsForNvda/bun_bridge.py``), so the runtime
has to ship inside the ``.nvda-addon``. Bun publishes per-platform archives as
release assets; the Windows x64 one contains a single ``bun.exe`` nested in a
top-level folder::

    bun-windows-x64/
    bun-windows-x64/bun.exe

Because that folder name has historically tracked the platform triple rather than
being a stable contract, the executable is located by a recursive search for the
expected file name instead of a hardcoded path.

Usage::

    python fetch_bun_runtime.py
    python fetch_bun_runtime.py --dest path/to/runtime --url https://.../bun-windows-x64.zip
    python fetch_bun_runtime.py --print-dest        # for build scripts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from contextlib import suppress
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ADDON_ROOT = REPO_ROOT / "googleTtsForNvda"
DRIVER_DIR = ADDON_ROOT / "synthDrivers" / "googleTtsForNvda"

DEFAULT_URL = "https://github.com/oven-sh/bun/releases/latest/download/bun-windows-x64.zip"
# NVDA is Windows-only, and the add-on ships a single runtime binary.
EXECUTABLE_NAME = "bun.exe"
# Guard against a truncated download or an HTML error page being packaged.
MINIMUM_EXECUTABLE_BYTES = 20 * 1024 * 1024
PE_MAGIC = b"MZ"
VERSION_FILE_NAME = "bun_version.json"
USER_AGENT = "GoogleTtsForNvda-Build/1.0"
CHUNK_BYTES = 1024 * 1024


def default_destination() -> Path:
    """Where the add-on expects the runtime, matching ``bun_bridge.RUNTIME_DIR``."""
    return DRIVER_DIR / "runtime"


def is_pe_executable(path: Path) -> bool:
    """Whether ``path`` looks like a Windows PE image rather than an error page."""
    try:
        with path.open("rb") as stream:
            return stream.read(2) == PE_MAGIC
    except OSError:
        return False


def find_bun_executable(root: Path, executableName: str = EXECUTABLE_NAME) -> Path | None:
    """Recursively locate the runtime executable inside an extracted archive.

    Prefers the shallowest match, and among equally shallow matches the one whose
    name matches exactly, so a stray ``bun`` shell script cannot win over
    ``bun.exe``.
    """
    if not root.is_dir():
        return None
    matches = [path for path in root.rglob("*") if path.is_file() and path.name == executableName]
    if not matches:
        return None
    return min(matches, key=lambda path: (len(path.parts), str(path)))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, *, timeout: int = 300) -> Path:
    """Download ``url`` to ``destination``, streaming to a temporary file first."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporaryName = tempfile.mkstemp(dir=str(destination.parent), suffix=".download")
    os.close(handle)
    temporary = Path(temporaryName)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream, CHUNK_BYTES)
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    return destination


def extract_archive(archive: Path, targetDir: Path) -> Path:
    """Unzip ``archive`` into ``targetDir``, rejecting entries that escape it."""
    targetDir.mkdir(parents=True, exist_ok=True)
    resolvedTarget = targetDir.resolve()
    with zipfile.ZipFile(archive) as zipFile:
        for member in zipFile.infolist():
            candidate = (resolvedTarget / member.filename).resolve()
            if resolvedTarget != candidate and resolvedTarget not in candidate.parents:
                raise RuntimeError(f"Refusing to extract a path-traversal member: {member.filename}")
        zipFile.extractall(resolvedTarget)
    return resolvedTarget


def probe_version(executable: Path) -> str:
    """Best-effort ``bun --version``; only works when the host can run the binary."""
    try:
        import subprocess

        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip().splitlines()[0].strip() if completed.stdout else ""


def install(
    *,
    url: str = DEFAULT_URL,
    destination: Path | None = None,
    cacheDir: Path | None = None,
    force: bool = False,
    quiet: bool = False,
) -> Path:
    """Download, extract, verify and place ``bun.exe``. Returns the installed path."""

    def say(message: str) -> None:
        if not quiet:
            print(message)

    destDir = Path(destination) if destination is not None else default_destination()
    installed = destDir / EXECUTABLE_NAME
    if installed.is_file() and not force:
        if installed.stat().st_size >= MINIMUM_EXECUTABLE_BYTES and is_pe_executable(installed):
            say(f"      Already present: {installed} ({installed.stat().st_size:,} bytes)")
            return installed
        say(f"      Replacing invalid runtime at {installed}")

    cache = Path(cacheDir) if cacheDir is not None else Path(tempfile.gettempdir()) / "googleTtsForNvda-bun-cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / url.rstrip("/").rsplit("/", 1)[-1]

    if archive.is_file() and archive.stat().st_size > 0 and not force:
        say(f"      Using cached archive: {archive}")
    else:
        say(f"      Downloading {url}")
        download(url, archive)
        say(f"      Downloaded {archive.stat().st_size:,} bytes")

    with tempfile.TemporaryDirectory(prefix="gtts-bun-") as stagingName:
        staging = Path(stagingName)
        extract_archive(archive, staging)
        found = find_bun_executable(staging)
        if found is None:
            names = sorted({path.name for path in staging.rglob("*") if path.is_file()})[:20]
            raise RuntimeError(f"{EXECUTABLE_NAME} was not found inside {archive.name}. Archive contained: {names}")
        size = found.stat().st_size
        if size < MINIMUM_EXECUTABLE_BYTES:
            raise RuntimeError(f"{found.name} is only {size:,} bytes; expected at least {MINIMUM_EXECUTABLE_BYTES:,}.")
        if not is_pe_executable(found):
            raise RuntimeError(f"{found.name} is not a Windows PE executable.")
        destDir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(found, installed)
        with suppress(OSError):
            os.chmod(installed, 0o755)
        say(f"      Installed {installed} ({installed.stat().st_size:,} bytes)")

    metadata = {
        "url": url,
        "archiveSha256": sha256(archive),
        "executable": EXECUTABLE_NAME,
        "executableSha256": sha256(installed),
        "executableBytes": installed.stat().st_size,
        "version": probe_version(installed),
    }
    (destDir / VERSION_FILE_NAME).write_text(json.dumps(metadata, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    say(f"      Bun version: {metadata['version'] or 'unknown (host cannot execute a Windows binary)'}")
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="Bun release archive URL (default: %(default)s).")
    parser.add_argument(
        "--dest",
        default=None,
        help=f"Destination directory (default: {default_destination().relative_to(REPO_ROOT)}).",
    )
    parser.add_argument("--cache-dir", default=None, help="Directory to cache the downloaded archive in.")
    parser.add_argument("--force", action="store_true", help="Re-download and re-extract even if already installed.")
    parser.add_argument("--quiet", action="store_true", help="Only report errors.")
    parser.add_argument(
        "--print-dest",
        action="store_true",
        help="Print the destination directory and exit without downloading.",
    )
    args = parser.parse_args(argv)

    if args.print_dest:
        print(default_destination() if args.dest is None else Path(args.dest))
        return 0

    try:
        install(
            url=args.url,
            destination=Path(args.dest) if args.dest else None,
            cacheDir=Path(args.cache_dir) if args.cache_dir else None,
            force=args.force,
            quiet=args.quiet,
        )
    except (urllib.error.URLError, RuntimeError, OSError, zipfile.BadZipFile) as error:
        print(f"[ERROR] Could not install the Bun runtime: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
