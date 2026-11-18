"""
Google TTS Voice Metadata Extractor and Catalog Generator
=========================================================
Reads voice package metadata from google tts voices.json, updates existing entries,
fetches speaker configurations for new voice packages, formats speaker names,
and writes merged, deduplicated entries to voices.json.

The target catalog is the newest bundled ``WasmTtsEngine/<version>/voices.json``.
The engine version is discovered from the tree and must match the production
``catalog.py:ENGINE_VERSION``. An unreadable, unbundled, or disagreeing engine
version aborts the run instead of writing a catalog that the add-on would not
serve.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import io
import json
import re
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover - online developer tool; tests import this module without requests.
    requests = None  # type: ignore[assignment]

# Paths
BASE_DIR = Path(__file__).resolve().parent

# Find google tts voices.json
GOOGLE_TTS_JSON_CANDIDATES = [
    BASE_DIR / "Google-TTS-For-NVDA" / "google tts voices.json",
    BASE_DIR / "google tts voices.json",
]

ENGINE_ROOT_CANDIDATES = [
    BASE_DIR / "Google-TTS-For-NVDA" / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "WasmTtsEngine",
    BASE_DIR / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "WasmTtsEngine",
]

CATALOG_MODULE_CANDIDATES = [
    BASE_DIR / "Google-TTS-For-NVDA" / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "catalog.py",
    BASE_DIR / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "catalog.py",
]

ENGINE_VERSION_PATTERN = re.compile(r"^\d{8}(?:\.\d+)*$")

# Concurrency & Request Settings
MAX_WORKERS = 15
REQUEST_TIMEOUT = 20
MAX_RETRIES = 2
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_TEXTPROTO_BYTES = 10 * 1024 * 1024

# Thread lock for console output
output_lock = threading.Lock()


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Build a numeric-aware sort key so 20260820.1 sorts after 20260625.1."""
    key: list[tuple[int, int | str]] = []
    for token in re.findall(r"\d+|[A-Za-z]+", version):
        if token.isdigit():
            key.append((0, int(token)))
        else:
            key.append((1, token.lower()))
    return tuple(key)


def _resolved_engine_roots(engineRoots: Iterable[Path] | None) -> list[Path]:
    return list(engineRoots) if engineRoots is not None else list(ENGINE_ROOT_CANDIDATES)


def newest_bundled_engine_dir(engineRoots: Iterable[Path] | None = None) -> Path | None:
    """Return the newest bundled engine version directory discovered under the engine roots."""
    candidates: list[tuple[tuple[tuple[int, int | str], ...], str, Path]] = []
    for engineRoot in _resolved_engine_roots(engineRoots):
        if not engineRoot.is_dir():
            continue
        for directory in engineRoot.iterdir():
            if directory.is_dir() and ENGINE_VERSION_PATTERN.match(directory.name):
                candidates.append((_version_sort_key(directory.name), directory.name, directory))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
    return candidates[-1][2]


def latest_engine_voices_json(engineRoot: Path | None = None) -> Path | None:
    """Return the voices.json of the newest bundled WasmTtsEngine version, if any."""
    if engineRoot is None:
        for candidate in ENGINE_ROOT_CANDIDATES:
            if candidate.is_dir():
                engineRoot = candidate
                break
    if engineRoot is None or not engineRoot.is_dir():
        return None
    candidates: list[tuple[tuple[tuple[int, int | str], ...], str, Path]] = []
    for directory in engineRoot.iterdir():
        if not directory.is_dir():
            continue
        voicesJson = directory / "voices.json"
        if voicesJson.is_file():
            candidates.append((_version_sort_key(directory.name), directory.name, voicesJson))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
    return candidates[-1][2]


def catalog_engine_version() -> str:
    """Read ENGINE_VERSION from the production catalog module so the tree can be compared with the add-on."""
    for candidate in CATALOG_MODULE_CANDIDATES:
        try:
            text = candidate.read_text(encoding="utf-8-sig")
            tree = ast.parse(text, filename=str(candidate))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "ENGINE_VERSION" for target in targets):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
            break
    return ""


def check_engine_version_alignment(bundledVersion: str, configuredVersion: str) -> str | None:
    """Explain why the newest bundled engine and the production catalog module disagree, if they do."""
    if not configuredVersion:
        locations = ", ".join(str(candidate) for candidate in CATALOG_MODULE_CANDIDATES)
        return f"Could not read ENGINE_VERSION from the production catalog module ({locations})."
    if not bundledVersion:
        return "No bundled WasmTtsEngine version directory was found."
    if bundledVersion == configuredVersion:
        return None
    if _version_sort_key(bundledVersion) > _version_sort_key(configuredVersion):
        return (
            f"The newest bundled engine version ({bundledVersion}) is newer than catalog.py "
            f"ENGINE_VERSION ({configuredVersion}). Bump ENGINE_VERSION to {bundledVersion} "
            "before regenerating."
        )
    return (
        f"catalog.py ENGINE_VERSION ({configuredVersion}) has no bundled engine directory: "
        f"the newest bundled version is {bundledVersion}. Add the {configuredVersion} engine "
        "bundle or fix ENGINE_VERSION."
    )


def select_engine_voices_json(
    engineRoots: Iterable[Path] | None = None,
    explicitVoicesJson: Path | None = None,
    *,
    configuredVersion: str | None = None,
) -> Path:
    """Resolve the newest bundled engine catalog, aborting unless the engine version is verified."""
    if explicitVoicesJson is not None:
        if not explicitVoicesJson.is_file():
            print(
                f"[ABORT] Error: the explicit engine catalog was not found at {explicitVoicesJson}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Target engine catalog (explicit): {explicitVoicesJson}")
        print(
            "[WARN] Engine version verification was skipped for an explicit --voices-json target.",
            file=sys.stderr,
        )
        return explicitVoicesJson

    engineDir = newest_bundled_engine_dir(engineRoots)
    if engineDir is None:
        roots = ", ".join(str(candidate) for candidate in _resolved_engine_roots(engineRoots))
        print(
            f"[ABORT] Error: no WasmTtsEngine <version> directory was found under: {roots}",
            file=sys.stderr,
        )
        sys.exit(1)

    voicesJsonPath = engineDir / "voices.json"
    if not voicesJsonPath.is_file():
        print(
            f"[ABORT] Error: the newest bundled engine ({engineDir.name}) has no voices.json. "
            "Add the upstream catalog to the engine bundle before regenerating.",
            file=sys.stderr,
        )
        sys.exit(1)

    expectedVersion = catalog_engine_version() if configuredVersion is None else configuredVersion
    problem = check_engine_version_alignment(engineDir.name, expectedVersion)
    if problem is not None:
        print(f"[ABORT] Error: {problem}", file=sys.stderr)
        sys.exit(1)

    print(f"Target engine catalog: {voicesJsonPath}")
    return voicesJsonPath


# Mapping of locale codes to native language names
NATIVE_LANGUAGE_NAMES: dict[str, str] = {
    "en-au": "Australian English",
    "en-us": "US English",
    "en-gb": "UK English",
    "en-in": "Indian English",
    "as-in": "অসমীয়া",
    "pl-pl": "Polski",
    "bn-bd": "বাংলা",
    "bn-in": "বাংলা (ভারত)",
    "bg-bg": "български",
    "pt-br": "português do Brasil",
    "pt-pt": "português de Portugal",
    "ca-es": "català",
    "iw-il": "עברית",
    "he-il": "עברית",
    "nl-be": "Vlaams",
    "nl-nl": "Nederlands",
    "ko-kr": "한국어",
    "it-it": "italiano",
    "jv-id": "Basa Jawa",
    "kn-in": "ಕನ್ನಡ",
    "km-kh": "ខ្មែរ",
    "kok-in": "कोंकणी",
    "lv-lv": "latviešu",
    "lt-lt": "lietuvių",
    "ml-in": "മലയാളം",
    "mr-in": "मराठी",
    "ms-my": "Bahasa Melayu",
    "nb-no": "Norsk Bokmål",
    "ru-ru": "русский",
    "ja-jp": "日本語",
    "fr-ca": "français canadien",
    "fr-fr": "français",
    "pa-in": "ਪੰਜਾਬੀ",
    "yue-hk": "粵語",
    "ro-ro": "română",
    "sr-rs": "српски",
    "sd-in": "سنڌي",
    "sl-si": "slovenščina",
    "sw-ke": "Kiswahili",
    "cs-cz": "čeština",
    "ta-in": "தமிழ்",
    "te-in": "తెలుగు",
    "th-th": "ไทย",
    "tr-tr": "Türkçe",
    "sv-se": "Svenska",
    "zh-cn": "中文 (中国)",
    "zh-tw": "中文 (台灣)",
    "cmn-cn": "中文 (中国)",
    "cmn-tw": "中文 (台灣)",
    "es-us": "español de Estados Unidos",
    "es-es": "español",
    "uk-ua": "українська",
    "ur-pk": "اردو",
    "ur-in": "اردو (ভারত)",
    "vi-vn": "Tiếng Việt",
    "cy-gb": "Cymraeg",
    "da-dk": "Dansk",
    "de-de": "Deutsch",
    "ar-xa": "العربية",
    "sq-al": "shqip",
    "en-ng": "Nigerian English",
    "brx-in": "बड़ो",
    "bs-ba": "bosanski",
    "hr-hr": "hrvatski",
    "doi-in": "डोगरी",
    "et-ee": "eesti",
    "gu-in": "ગુજરાતી",
    "hi-in": "हिन्दी",
    "hu-hu": "Magyar",
    "el-gr": "Ελληνικά",
    "is-is": "íslenska",
    "id-id": "Bahasa Indonesia",
    "ks-in": "कॉशुर",
    "mai-in": "मैथिली",
    "mni-in": "মৈতৈলোন্",
    "ne-np": "नेपाली",
    "or-in": "ଓଡ଼ିଆ",
    "fil-ph": "Filipino",
    "sa-in": "संस्कृतम्",
    "fi-fi": "Suomi",
    "sat-in": "ᱥᱟᱱᱛᱟᱲᱤ",
    "si-lk": "සිංහල",
    "sk-sk": "Slovenčina",
    "su-id": "Basa Sunda",
}


def get_native_language_name(locale: str) -> str:
    locale_lower = locale.lower()
    return NATIVE_LANGUAGE_NAMES.get(locale_lower, locale.upper())


def format_speaker_name(voice_id: str, native_name: str, idx: int, num_speakers: int) -> str:
    if voice_id.endswith("-multi"):
        return f"Chrome OS {native_name}" if num_speakers == 1 else f"Chrome OS {native_name} {idx}"
    elif voice_id.endswith("-multi-seanet"):
        return f"Google {native_name} (Natural)" if num_speakers == 1 else f"Google {native_name} {idx} (Natural)"
    elif voice_id.endswith("-multi-locomel"):
        return f"Google {native_name} (Locomel)" if num_speakers == 1 else f"Google {native_name} {idx} (Locomel)"
    elif voice_id.endswith("-multi-lemonbalm"):
        return f"Google {native_name} (Lemonbalm)" if num_speakers == 1 else f"Google {native_name} {idx} (Lemonbalm)"
    elif voice_id.endswith("-blueginger-lemonbalm"):
        return f"Google {native_name} (Blueginger)" if num_speakers == 1 else f"Google {native_name} {idx} (Blueginger)"
    elif voice_id.endswith("-news-lemonbalm"):
        return f"Google {native_name} News" if num_speakers == 1 else f"Google {native_name} News {idx}"
    elif voice_id.endswith("-news-darwinnrio-lemonbalm"):
        return (
            f"Google {native_name} News Darwinnrio"
            if num_speakers == 1
            else f"Google {native_name} News Darwinnrio {idx}"
        )
    elif voice_id.endswith("-afh"):
        return f"Chrome OS {native_name} (afh)" if num_speakers == 1 else f"Chrome OS {native_name} {idx} (afh)"
    elif voice_id.endswith("-fis"):
        return f"Chrome OS {native_name} (fis)" if num_speakers == 1 else f"Chrome OS {native_name} {idx} (fis)"
    elif voice_id.endswith("-afh-seanet"):
        return (
            f"Google {native_name} (afh Natural)" if num_speakers == 1 else f"Google {native_name} {idx} (afh Natural)"
        )
    elif voice_id.endswith("-fis-seanet"):
        return (
            f"Google {native_name} (fis Natural)" if num_speakers == 1 else f"Google {native_name} {idx} (fis Natural)"
        )
    else:
        suffix = voice_id.split("-x-")[-1]
        return f"Google {native_name} ({suffix})" if num_speakers == 1 else f"Google {native_name} {idx} ({suffix})"


_SHARED_MULTI_DEPENDENCY_RE = re.compile(r"-(?:lemonbalm|locomel)(?:-|$)")


def dependent_voice_id_for_package(voice_id: str) -> str:
    """Return the required base package ID for delta voice packages, or empty string."""
    normalized = voice_id.strip()
    if normalized.endswith("-seanet"):
        return re.sub(r"-seanet$", "", normalized)
    locale_prefix, sep, _ = normalized.partition("-x-")
    if sep and locale_prefix and _SHARED_MULTI_DEPENDENCY_RE.search(normalized):
        return f"{locale_prefix}-x-multi"
    return ""


def _extract_speakers_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    idx = 0
    length = len(text)
    pattern = re.compile(r"\bspeakers\s*\{")
    while idx < length:
        match = pattern.search(text, idx)
        if match is None:
            break
        start = match.end()
        depth = 1
        pos = start
        in_string = False
        escaped = False
        while pos < length and depth > 0:
            ch = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "#":
                newline = text.find("\n", pos)
                if newline == -1:
                    pos = length
                    break
                pos = newline
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start:pos])
                    break
            pos += 1
        idx = pos + 1
    return blocks


def extract_speakers_from_textproto(content_bytes: bytes) -> list[tuple[str, str]]:
    try:
        text = content_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return []

    speakers: list[tuple[str, str]] = []
    seen_speakers: set[str] = set()

    for block in _extract_speakers_blocks(text):
        spk_match = re.search(r'\bspeaker:\s*"((?:\\.|[^"\\])*)"', block)
        gen_match = re.search(r'\bgender:\s*"((?:\\.|[^"\\])*)"', block)
        if spk_match:
            spk_code = spk_match.group(1).strip()
            gen_str = (gen_match.group(1).strip() if gen_match else "") or "female"
            if spk_code and spk_code not in seen_speakers:
                seen_speakers.add(spk_code)
                speakers.append((spk_code, gen_str))

    return speakers


def _read_bounded_response_bytes(response: Any, max_bytes: int) -> bytes:
    headers = getattr(response, "headers", None)
    content_length = headers.get("Content-Length") if isinstance(headers, dict) or hasattr(headers, "get") else None
    if content_length is not None:
        try:
            parsed_length = int(content_length)
        except (TypeError, ValueError):
            parsed_length = None
        if parsed_length is not None and parsed_length > max_bytes:
            raise ValueError(f"Package size {parsed_length} bytes exceeds limit ({max_bytes} bytes)")

    iter_content = getattr(response, "iter_content", None)
    if callable(iter_content):
        chunks = bytearray()
        for chunk in iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise ValueError(f"Downloaded package exceeds limit ({max_bytes} bytes)")
        return bytes(chunks)

    payload = response.content
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > max_bytes:
        raise ValueError(f"Downloaded package exceeds limit ({max_bytes} bytes)")
    return bytes(payload)


def fetch_new_package_speakers(
    session: requests.Session, pkg_info: dict[str, Any], all_packages_map: dict[str, dict[str, Any]]
) -> list[dict[str, str]] | None:
    voice_id = pkg_info["id"]
    url = pkg_info["url"]
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        with output_lock:
            print(f"[ERROR] Refusing non-HTTPS URL for {voice_id}: {url!r}", file=sys.stderr, flush=True)
        return None
    locale = voice_id.split("-x-")[0]
    native_name = get_native_language_name(locale)

    for attempt in range(MAX_RETRIES + 1):
        try:
            with output_lock:
                print(f"[FETCHING] {voice_id} ...", flush=True)

            response = session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            try:
                final_url = getattr(response, "url", None)
                if isinstance(final_url, str) and final_url and not final_url.lower().startswith("https://"):
                    raise ValueError(f"Redirected to non-HTTPS URL for {voice_id}: {final_url!r}")
                if response.status_code != 200:
                    raise requests.RequestException(f"HTTP {response.status_code}")

                payload = _read_bounded_response_bytes(response, MAX_PACKAGE_BYTES)
            finally:
                close_fn = getattr(response, "close", None)
                if callable(close_fn):
                    close_fn()

            raw_speakers: list[tuple[str, str]] = []

            with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
                try:
                    info = zf.getinfo("backend_pipeline.textproto")
                except KeyError:
                    info = None
                if info is not None:
                    if info.file_size > MAX_TEXTPROTO_BYTES:
                        raise ValueError(f"backend_pipeline.textproto exceeds size limit ({info.file_size} bytes)")
                    with zf.open(info, "r") as member:
                        textproto_bytes = member.read(MAX_TEXTPROTO_BYTES + 1)
                    if len(textproto_bytes) > MAX_TEXTPROTO_BYTES:
                        raise ValueError("backend_pipeline.textproto exceeded size limit while reading")
                    raw_speakers = extract_speakers_from_textproto(textproto_bytes)

            # If no textproto found (e.g. locomel binary compiled protobuf), inherit speaker codes from base multi package
            if not raw_speakers:
                base_multi_id = f"{locale}-x-multi"
                if base_multi_id in all_packages_map and "speakers" in all_packages_map[base_multi_id]:
                    raw_speakers = [(s["speaker"], s["gender"]) for s in all_packages_map[base_multi_id]["speakers"]]

            if not raw_speakers:
                raise ValueError("No valid speakers found or inferred")

            formatted_speakers = []
            num_speakers = len(raw_speakers)

            for idx, (spk_code, gender) in enumerate(raw_speakers, start=1):
                display_name = format_speaker_name(voice_id, native_name, idx, num_speakers)
                formatted_speakers.append({"speaker": spk_code, "name": display_name, "gender": gender})

            with output_lock:
                print(f"[SUCCESS] {voice_id} ({len(formatted_speakers)} speakers)", flush=True)
            return formatted_speakers

        except Exception as e:
            if attempt == MAX_RETRIES:
                with output_lock:
                    print(f"[ERROR] Failed to process {url}: {e}", file=sys.stderr, flush=True)
            else:
                time.sleep(1 * (attempt + 1))

    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate the bundled engine voices.json from google tts voices.json."
    )
    parser.add_argument(
        "--voices-json",
        type=Path,
        default=None,
        help="Explicit engine voices.json to regenerate; skips the engine version verification.",
    )
    return parser.parse_args(argv)


def _fetch_speaker_wave(
    session: requests.Session | Callable[[], requests.Session],
    executor: concurrent.futures.ThreadPoolExecutor,
    packages: list[dict[str, Any]],
    speaker_snapshot: dict[str, dict[str, Any]],
    merged_map: dict[str, dict[str, Any]],
    fallback_speakers: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    if not packages:
        return
    session_factory = session if callable(session) else (lambda: session)
    thread_local = threading.local()
    created_sessions: list[requests.Session] = []
    sessions_lock = threading.Lock()

    def _run_pkg(pkg_copy: dict[str, Any]) -> list[dict[str, Any]] | None:
        worker_session = getattr(thread_local, "session", None)
        if worker_session is None:
            worker_session = session_factory()
            thread_local.session = worker_session
            if callable(session):
                with sessions_lock:
                    created_sessions.append(worker_session)
        return fetch_new_package_speakers(worker_session, pkg_copy, speaker_snapshot)

    try:
        future_to_pkg = {executor.submit(_run_pkg, dict(pkg)): pkg for pkg in packages}
        for future in concurrent.futures.as_completed(future_to_pkg):
            pkg = future_to_pkg[future]
            speakers = future.result()
            if speakers:
                merged_map[pkg["id"]]["speakers"] = speakers
            elif fallback_speakers and pkg["id"] in fallback_speakers:
                print(f"[WARN] Keeping previous speaker metadata for {pkg['id']} after fetch failure.")
                merged_map[pkg["id"]]["speakers"] = fallback_speakers[pkg["id"]]
            else:
                print(f"[WARN] Removing {pkg['id']} due to missing speakers.")
                merged_map.pop(pkg["id"], None)
    finally:
        for created in created_sessions:
            close_fn = getattr(created, "close", None)
            if callable(close_fn):
                close_fn()


def _atomic_write_json(target_path: Path, payload: object) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temp_path.replace(target_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> None:
    args = _parse_args()
    print("=== Google TTS Voice Catalog Generator ===")

    if requests is None:
        print(
            "[ABORT] Error: the 'requests' package is required for this online catalog generator.",
            file=sys.stderr,
        )
        sys.exit(1)

    source_json_path = None
    for cand in GOOGLE_TTS_JSON_CANDIDATES:
        if cand.exists():
            source_json_path = cand
            break

    if not source_json_path:
        print("[ABORT] Error: google tts voices.json not found in expected locations.", file=sys.stderr)
        sys.exit(1)

    print(f"Reading source catalog from: {source_json_path}")
    with open(source_json_path, encoding="utf-8-sig") as f:
        source_data = json.load(f)

    source_packs = source_data.get("packs", [])
    print(f"Found {len(source_packs)} total packs in google tts voices.json.")

    voices_json_path = select_engine_voices_json(explicitVoicesJson=args.voices_json)

    # Load existing voices.json if present
    existing_entries: dict[str, dict[str, Any]] = {}
    if voices_json_path.exists():
        try:
            with open(voices_json_path, encoding="utf-8-sig") as f:
                for item in json.load(f):
                    if isinstance(item, dict) and "id" in item:
                        existing_entries[item["id"]] = item
            print(f"Loaded {len(existing_entries)} existing voice entries from {voices_json_path.name}.")
        except Exception as e:
            print(f"[WARN] Could not parse existing voices.json: {e}", file=sys.stderr)

    # Prepare merged map
    merged_map: dict[str, dict[str, Any]] = {}

    # Step 1: Process items from source_packs
    new_packages_to_fetch: list[dict[str, Any]] = []

    for pack in source_packs:
        file_id = pack.get("name", "")
        if not file_id:
            continue
        # Derive voice_id by stripping revision suffix e.g. -r83
        voice_id = re.sub(r"-r\d+$", "", file_id)
        compressed_size = pack.get("compressed_size", 0)
        sha256_checksum = pack.get("sha256_checksum", "")

        urls = pack.get("download_urls", [])
        if not urls:
            continue
        # Prefer dl.google.com url if present
        url = next(
            (u for u in urls if (urllib.parse.urlparse(u).hostname or "").lower() == "dl.google.com"),
            urls[0],
        )

        entry: dict[str, Any] = {
            "id": voice_id,
            "fileId": file_id,
            "url": url,
            "sha256Checksum": sha256_checksum,
            "compressedSize": compressed_size,
            "remote": True,
        }

        dependent_voice_id = dependent_voice_id_for_package(voice_id)
        if dependent_voice_id:
            entry["dependentVoiceId"] = dependent_voice_id

        existing_item = existing_entries.get(voice_id)
        if existing_item is not None and "speakers" in existing_item and existing_item.get("fileId") == file_id:
            # Preserve existing speakers array when package revision is unchanged
            entry["speakers"] = existing_item["speakers"]
            merged_map[voice_id] = entry
        else:
            merged_map[voice_id] = entry
            new_packages_to_fetch.append(entry)

    print(f"\n{len(merged_map) - len(new_packages_to_fetch)} packages already have speaker metadata.")
    print(f"Fetching speaker metadata for {len(new_packages_to_fetch)} new packages concurrently...\n")

    if new_packages_to_fetch:
        fallback_speakers = {
            vid: list(item["speakers"])
            for vid, item in existing_entries.items()
            if isinstance(item.get("speakers"), list) and item["speakers"]
        }
        base_multi_packages = [pkg for pkg in new_packages_to_fetch if pkg["id"].endswith("-x-multi")]
        dependent_packages = [pkg for pkg in new_packages_to_fetch if not pkg["id"].endswith("-x-multi")]
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            wave1_snapshot = {
                vid: {"speakers": list(item["speakers"])} for vid, item in merged_map.items() if "speakers" in item
            }
            _fetch_speaker_wave(
                requests.Session,
                executor,
                base_multi_packages,
                wave1_snapshot,
                merged_map,
                fallback_speakers,
            )
            wave2_snapshot = {
                vid: {"speakers": list(item["speakers"])} for vid, item in merged_map.items() if "speakers" in item
            }
            _fetch_speaker_wave(
                requests.Session,
                executor,
                dependent_packages,
                wave2_snapshot,
                merged_map,
                fallback_speakers,
            )

    for entry in merged_map.values():
        dependent_voice_id = entry.get("dependentVoiceId")
        if dependent_voice_id and dependent_voice_id not in merged_map:
            entry.pop("dependentVoiceId", None)

    # Sort by language code (locale) and then by package ID
    sorted_entries = sorted(merged_map.values(), key=lambda x: (x["id"].split("-x-")[0].lower(), x["id"].lower()))

    # Save updated content atomically to voices.json
    _atomic_write_json(voices_json_path, sorted_entries)

    print("\n=== Catalog Update Complete ===")
    print(f"Total entries in voices.json: {len(sorted_entries)}")
    print(f"Saved directly to: {voices_json_path}")


if __name__ == "__main__":
    main()
