# Contributing to Google TTS For NVDA

Thanks for your interest in helping out! Whether you're fixing a bug, adding a feature, improving translations, or just cleaning up docs, every contribution counts.

This guide explains how to set up your environment, run checks locally, understand continuous integration (CI), and submit pull requests.

> [!NOTE]
> - **End-users**: If you are looking for installation, usage instructions, or voice configuration, please refer to [readme.md](readme.md).
> - **Translators**: If you are translating interface messages or documentation, see [TRANSLATING.md](TRANSLATING.md). You do not need to install Python linting or test tools.
> - **Bug reports & suggestions**: If you found a bug or have an idea, you can open an issue on GitHub directly without touching code.

---

## How CI Works

Every push and every pull request triggers the **Tests** workflow on GitHub Actions (`.github/workflows/test.yml`). It runs on **Windows** (`windows-latest`) against four Python versions in parallel: **Python 3.11**, **Python 3.12**, **Python 3.13**, and **Python 3.14**. All must pass for a PR to be mergeable.

The workflow uses `fail-fast: false`, so if one Python version fails, the others still finish running — you'll see all failures at once instead of having to fix them one at a time. It also enforces `permissions: contents: read` for least-privilege security and uses `concurrency` with `cancel-in-progress: true` to automatically cancel obsolete in-flight runs when new commits are pushed.

### CI Steps (in order)

The workflow runs these checks in sequence:

1. **Ruff lint** — `python -m ruff check` catches unused imports, undefined names, common bugs, and style issues. The config in `ruff.toml` excludes vendored directories (`websocketClientRepo`, `WasmTtsEngine`, `cld2`, `web`) and targets Python 3.11.

2. **Ruff format** — `python -m ruff format --check` verifies code formatting. If your code isn't formatted, the check fails. Run `python -m ruff format` to fix it automatically.

3. **Mypy type check** — validates type hints across `synthDrivers/`, `tests/`, and all `globalPlugins/` modules. Because NVDA core APIs are external and untyped during standalone testing, `mypy.ini` ignores external missing imports and focuses strictly on internal type consistency within add-on code.

4. **Unit tests** — `python -m unittest discover -s tests -v` runs all standalone tests. These don't need NVDA installed, so they work on any Windows machine (and Linux/macOS with the right setup).

After all checks finish, CI runs `git clean -fdX` to remove all files listed in `.gitignore` (caches, build artifacts, etc.). This step runs with `if: always()` so cleanup occurs whether prior checks pass or fail, keeping the workspace clean without hardcoding paths.

### When does CI run?

| Event | Branch | Runs? |
|---|---|---|
| Push | Any branch | Yes |
| Pull request (any branch) | — | Yes |

---

## Running Checks Locally

You don't need to push just to find out if your code passes. Run these locally first — they're fast and catch most issues before you even open a PR.

### Opening a terminal in the project folder

The quickest way:

1. Open the project folder in **File Explorer** (the folder containing `CONTRIBUTING.md`).
2. Press **Alt + D** to focus the address bar.
3. Type `powershell` and press **Enter**. A PowerShell window opens in that folder.

### Install the tools

```powershell
pip install ruff mypy markdown nh3 lxml
```

You only need to do this once (or when the project updates its tool versions).

### Auto-fix and run all checks (Recommended)

To automatically apply safe lint fixes and formatting, followed by type checking, unit tests, and temporary cache cleanup:

```powershell
python -m ruff check --fix ; python -m ruff format ; python -m mypy --config-file mypy.ini --explicit-package-bases --exclude "websocketClientRepo" googleTtsForNvda/synthDrivers/ tests/ googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updateGui.py googleTtsForNvda/globalPlugins/googleTtsForNvda/uiUtils.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updater.py googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py ; python -m unittest discover -s tests -v ; git clean -fdX
```

### Strict verification check (CI-style)

To verify the codebase without modifying any files (matching the exact sequence run by CI):

```powershell
python -m ruff check ; python -m ruff format --check ; python -m mypy --config-file mypy.ini --explicit-package-bases --exclude "websocketClientRepo" googleTtsForNvda/synthDrivers/ tests/ googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updateGui.py googleTtsForNvda/globalPlugins/googleTtsForNvda/uiUtils.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updater.py googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py ; python -m unittest discover -s tests -v ; git clean -fdX
```

### Individual checks

You can also run any check independently:

```powershell
# Linting & code formatting
python -m ruff check --fix      # check lint rules and auto-fix safe issues
python -m ruff format           # format code to project standard

# Type checking
python -m mypy --config-file mypy.ini --explicit-package-bases --exclude "websocketClientRepo" googleTtsForNvda/synthDrivers/ tests/ googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updateGui.py googleTtsForNvda/globalPlugins/googleTtsForNvda/uiUtils.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updater.py googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py

# Standalone unit tests
python -m unittest discover -s tests -v

# Clean temporary build/cache files
git clean -fdX
```

---

## Testing in the Workflow

All unit tests run **without NVDA installed**, making them fast, self-contained, and safe to run in any environment (Windows, Linux, or macOS) before submitting changes.

- **Pre-commit verification**: Run the full test suite locally via the [recommended all-in-one command](#auto-fix-and-run-all-checks-recommended) before opening a pull request or pushing new commits.
- **Coverage for changes**: Any new standalone functionality, edge cases, or bug fixes must include accompanying unit tests in `tests/`.
- **Real-world verification (Dogfooding)**: Automated tests are mandatory, but not sufficient on their own. You **must** install and test your changes in an actual NVDA session to verify speech responsiveness, UI behavior, and audio quality. PRs generated by AI without hands-on verification will be rejected.
- **Test architecture & inventory**: For the complete module overview, class-by-class inventory, and benchmark suites, refer directly to [tests/README.md](tests/README.md).

---

## Contribution Acceptance Criteria

We value pull requests from the community. A contribution can be accepted when the code runs reliably, fits the add-on's existing logic and interface, avoids introducing regressions in other add-on features, does not interfere with other NVDA add-ons or NVDA itself, does not weaken add-on security, and does not collect user data without permission.

---

## Pull Request Checklist

Before opening a PR, run through this:

1. **All checks pass locally.** Run the [all-in-one command](#auto-fix-and-run-all-checks-recommended) or each step individually — all should be clean.
2. **No NVDA-specific imports** are added to standalone modules like `speech_processing.py`, `language_detector.py`, `language_profiles.py`, or `unicode_data.py`. Those must remain runnable without NVDA. (CI will catch this with `ModuleNotFoundError` failures.)
3. **New tests are added** for any new standalone functionality.
4. **Build succeeds** — run `build.bat` (Windows) or `build.sh` (Linux/macOS) and verify no errors.
5. **Code matches the existing style.** Ruff handles most of this, but also check naming conventions, docstrings, and comment style against nearby code.
6. **User-facing strings use `_()` for translation** where applicable in NVDA UI code.
7. **Hands-on testing performed.** Install and test the built add-on in a live NVDA session before submitting.
8. **Meets acceptance criteria.** Review your changes against the [Contribution Acceptance Criteria](#contribution-acceptance-criteria) above.

---

## What Happens After You Open a PR

1. **CI runs automatically.** You'll see the workflow status directly on your PR page (a checkmark or red X).
2. **All Python versions must pass.** With `fail-fast: false`, all versions (3.11 through 3.14) run to completion even if one fails, so you'll see all issues at once.
3. **Reviewers will verify** that the workflow is green and that the contribution meets all [acceptance criteria](#contribution-acceptance-criteria) before approving.
4. **If CI fails**, read the logs from the failed job, fix it locally, and push again — CI re-runs on every new push.

---

## Common CI Failures and Fixes

| Failure | Likely cause | Fix |
|---|---|---|
| `ruff check` finds unused imports | Import added but not used, or used only in NVDA code | Remove the unused import; move NVDA-only imports into the driver code |
| `ruff format` shows differences | Code not formatted | Run `python -m ruff format` and commit the result |
| `mypy` reports type errors | New code doesn't match expected types | Check the type annotation and fix it, or add a `# type: ignore` with a comment if it's a known NVDA quirk |
| `ModuleNotFoundError` in tests | New import added to a standalone module | Remove NVDA-only imports from standalone modules; keep them in driver code only |
| `AssertionError` in segmentation tests | Segmenter behavior changed | Update the expected values in `segmentation_corpus.json` or fix the segmenter logic |
| `AssertionError` in dependency isolation | A new top-level import leaks | Ensure vendored modules remain private and anchored |
| Unicode / script test failure | `unicode_data.py` regenerated incorrectly | Re-run `generate_unicode_data.py` and commit the updated file |
| Build error | Syntax error or missing file | Run `build.bat` / `build.sh` locally to reproduce and fix |

---

## Running NVDA API Contract Checks

If you have a local NVDA source checkout, you can verify static API contracts:

```powershell
python tests\check_nvda_api_contracts.py
```

The script looks for a sibling `NVDA source code` directory by default. Point it elsewhere if needed:

```powershell
python tests\check_nvda_api_contracts.py "C:\path\to\NVDA source code"
```

This is optional for most contributions, but recommended when you're touching the synth driver, audio output, or other NVDA integration points. The script checks contracts across several categories: synth driver, global plugin, speech hooks, settings dialog, voice manager, updater, browser runtime, and shared NVDA state.

---

## Additional Resources

- [TRANSLATING.md](TRANSLATING.md) — localization workflow and translation quality guidance
- [UPDATER_RELEASE_GUIDE.md](UPDATER_RELEASE_GUIDE.md) — release packaging and update manifest generation
- [tests/README.md](tests/README.md) — detailed standalone test documentation
- [readme.md](readme.md) — full add-on documentation, features, and configuration
- [AGENTS.md](AGENTS.md) — comprehensive engineering guide for coding agents (useful reference for human contributors too)

---

## Questions?

Open an issue or reach out via the contact information in [readme.md](readme.md).
