"""Generate the Bun entry points from Google's bundled WASM TTS engine sources.

The browser path loads three classic ``<script>`` tags (see ``web/index.html``),
which puts every top-level ``var`` on ``window``. Bun has no such thing, so this
module produces two generated files that recreate that arrangement:

``engine_boot.js``
    Main-thread entry: ``bindings_main.js`` followed by ``offscreen_compiled.js``,
    with the engine instance published onto ``globalThis`` afterwards.

``pthread_worker.js``
    Entry executed by each Emscripten pthread worker: ``bindings_main.js`` plus an
    explicit self-boot. This file exists because Bun's ``--preload`` and
    ``bunfig.toml`` preload only run on the main thread, so the
    ``parentPort`` -> ``globalThis.onmessage`` bridge that
    ``bunRuntime/shim.js`` installs cannot be injected by a flag.

Both files are wrapped in a function scope with ``__dirname``/``__filename`` bound
to the engine directory, because Emscripten's ``ENVIRONMENT_IS_NODE`` path
resolves ``bindings_main.wasm`` from ``__dirname``.

Nothing inside ``WasmTtsEngine/`` is modified, which also keeps
``tests/test_dependency_isolation.py::test_bundled_engine_files_are_fully_declared``
valid: that test asserts ``catalog.REQUIRED_ENGINE_FILES`` lists every file under
the engine directory, so generated output must live elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

try:
    from logHandler import log
except Exception:  # pragma: no cover - used by the standalone tests.
    import logging

    log = logging.getLogger("googleTtsForNvda")

from . import voice_store
from .catalog import ENGINE_DIR

BASE_DIR = Path(__file__).resolve().parent
BUN_RUNTIME_DIR = BASE_DIR / "bunRuntime"
SHIM_PATH = BUN_RUNTIME_DIR / "shim.js"
WORKER_PATH = BUN_RUNTIME_DIR / "worker.js"
MAIN_ENTRY_NAME = "engine_boot.js"
PTHREAD_ENTRY_NAME = "pthread_worker.js"
MANIFEST_NAME = "bootstrap_manifest.json"

_BINDINGS_NAME = "bindings_main.js"
_OFFSCREEN_NAME = "offscreen_compiled.js"

# offscreen_compiled.js instantiates the engine as a top-level minified global
# immediately before wiring up chrome.runtime.onMessage, e.g.
#   var Yh=new nh,Zh=null;
#   chrome.runtime.onMessage.addListener(function(a,b,c){...
# The name changes with every engine build, so it is discovered rather than
# hardcoded. web/bridgeHarness.js has the same problem and solves it by guessing
# ("Xh", "Vh", "Uh") then scanning every property of window; here the generated
# bootstrap can publish the real name, and the harness scan then finds it.
_ENGINE_GLOBAL_PATTERN = re.compile(
    r"var\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*new\s+[A-Za-z_$][\w$]*\s*(?:,\s*[A-Za-z_$][\w$]*\s*=\s*null\s*)?;",
)
_LISTENER_ANCHOR = "chrome.runtime.onMessage.addListener"
# web/bridgeHarness.js:getTtsEngine() probes these first; publishing the engine
# under one of them keeps that lookup O(1) instead of scanning globalThis.
_HARNESS_PROBE_PATTERN = re.compile(r"for\s*\(\s*const\s+key\s+of\s*\[(?P<names>[^\]]*)\]\s*\)")

_PRELUDE = '"use strict";\nrequire({shim});\n'
_IIFE_OPEN = "\n;(function (module, exports, require, __dirname, __filename) {\n"
_PTHREAD_TAIL = """
;(function __gttsPthreadSelfBoot() {
  if (typeof loadWasmTtsBindings !== "function") return;
  var workerThreads = require("node:worker_threads");
  var nameIsPthread = false;
  try { nameIsPthread = String(globalThis.name || "").startsWith("em-pthread"); } catch (error) {}
  if (!workerThreads.isMainThread && (workerThreads.workerData === "em-pthread" || nameIsPthread)) {
    loadWasmTtsBindings();
  }
})();
"""


class BunBootstrapError(RuntimeError):
    """Raised when the generated Bun entry points cannot be produced."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_engine_global(offscreenSource: str) -> str:
    """Return the minified global that holds the engine instance.

    Prefers the declaration immediately preceding the ``chrome.runtime.onMessage``
    registration, then falls back to any ``var X = new Y, Z = null;`` declaration.
    """
    anchor = offscreenSource.find(_LISTENER_ANCHOR)
    if anchor >= 0:
        window = offscreenSource[max(0, anchor - 400) : anchor]
        matches = list(_ENGINE_GLOBAL_PATTERN.finditer(window))
        if matches:
            return str(matches[-1].group("name"))
    matches = list(_ENGINE_GLOBAL_PATTERN.finditer(offscreenSource))
    if matches:
        return str(matches[-1].group("name"))
    raise BunBootstrapError(
        "Could not locate the WASM TTS engine instance in offscreen_compiled.js. "
        "The bundled engine layout changed; bun_bootstrap.find_engine_global() needs updating."
    )


def harness_probe_names() -> tuple[str, ...]:
    """Return the global names web/bridgeHarness.js probes before scanning window."""
    harnessPath = BASE_DIR / "web" / "bridgeHarness.js"
    try:
        source = harnessPath.read_text(encoding="utf-8")
    except OSError:
        return ()
    match = _HARNESS_PROBE_PATTERN.search(source)
    if match is None:
        return ()
    return tuple(re.findall(r"""["']([^"']+)["']""", match.group("names")))


def bootstrap_dir() -> Path:
    """Writable directory for generated entry points."""
    path = voice_store.data_root() / "runtime" / "bunBootstrap"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _iife_close(engineDir: Path) -> str:
    bindingsPath = (engineDir / _BINDINGS_NAME).as_posix()
    return (
        "})(typeof module !== 'undefined' ? module : {exports: {}}, "
        "typeof exports !== 'undefined' ? exports : {}, "
        f"require, {json.dumps(engineDir.as_posix())}, {json.dumps(bindingsPath)});\n"
    )


def _write_atomic(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def generate(engineDir: Path | None = None, outputDir: Path | None = None) -> dict[str, Path]:
    """Generate ``engine_boot.js`` and ``pthread_worker.js``.

    Returns a mapping with ``main``, ``pthread`` and ``worker`` paths. Output is
    cached and only rewritten when an input hash changes, so the cost is paid once
    per engine/shim revision rather than on every NVDA start.
    """
    engine = Path(engineDir) if engineDir is not None else ENGINE_DIR
    bindingsPath = engine / _BINDINGS_NAME
    offscreenPath = engine / _OFFSCREEN_NAME
    for required in (bindingsPath, offscreenPath, SHIM_PATH, WORKER_PATH):
        if not required.is_file():
            raise BunBootstrapError(f"Required runtime file is missing: {required}")

    output = Path(outputDir) if outputDir is not None else bootstrap_dir()
    output.mkdir(parents=True, exist_ok=True)

    bindingsSource = bindingsPath.read_text(encoding="utf-8")
    offscreenSource = offscreenPath.read_text(encoding="utf-8")
    engineGlobal = find_engine_global(offscreenSource)
    probes = [name for name in harness_probe_names() if name]

    fingerprint = {
        "bindings": _sha256(bindingsPath),
        "offscreen": _sha256(offscreenPath),
        "shim": _sha256(SHIM_PATH),
        "worker": _sha256(WORKER_PATH),
        "engineGlobal": engineGlobal,
        "engineDir": engine.as_posix(),
        "shimPath": SHIM_PATH.as_posix(),
    }
    manifestPath = output / MANIFEST_NAME
    mainPath = output / MAIN_ENTRY_NAME
    pthreadPath = output / PTHREAD_ENTRY_NAME
    if (
        manifestPath.is_file()
        and mainPath.is_file()
        and pthreadPath.is_file()
        and json.loads(manifestPath.read_text(encoding="utf-8")) == fingerprint
    ):
        return {"main": mainPath, "pthread": pthreadPath, "worker": WORKER_PATH}

    prelude = _PRELUDE.format(shim=json.dumps(SHIM_PATH.as_posix()))
    iifeOpen = _IIFE_OPEN
    iifeClose = _iife_close(engine)

    publishLines = [
        "",
        ";try {",
        f"  if (typeof {engineGlobal} !== 'undefined') {{",
        f"    globalThis.{engineGlobal} = {engineGlobal};",
        f"    globalThis.__gttsEngine = {engineGlobal};",
    ]
    for probe in probes:
        if probe != engineGlobal:
            publishLines.append(f"    globalThis.{probe} = {engineGlobal};")
    publishLines += ["  }", "} catch (error) {}", ""]
    publish = "\n".join(publishLines)

    mainSource = prelude + iifeOpen + bindingsSource + "\n;\n" + offscreenSource + publish + iifeClose
    pthreadSource = prelude + iifeOpen + bindingsSource + _PTHREAD_TAIL + iifeClose

    _write_atomic(mainPath, mainSource)
    _write_atomic(pthreadPath, pthreadSource)
    _write_atomic(manifestPath, json.dumps(fingerprint, indent=1, sort_keys=True) + "\n")
    log.debug(
        "Google TTS generated Bun bootstrap in %s (engine global %s, %d bytes).",
        output,
        engineGlobal,
        len(mainSource) + len(pthreadSource),
    )
    return {"main": mainPath, "pthread": pthreadPath, "worker": WORKER_PATH}
