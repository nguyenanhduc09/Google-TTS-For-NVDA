# Translating Google TTS For NVDA

This add-on uses standard gettext conventions for interface strings and supports HTML, PO, or Markdown workflows for user documentation. Existing localized folders (`googleTtsForNvda/locale/vi/` and `googleTtsForNvda/doc/vi/`) serve as complete in-tree reference implementations.

### Translation File Layout

- **googleTtsForNvda/**
   - **locale/**
      - **nvda.pot** — UI string template (generated)
      - **<language>/**
         - **LC_MESSAGES/**
            - **nvda.po** — Source interface translations
            - **nvda.mo** — Compiled binary catalog (generated)
         - **manifest.ini** — Translated add-on metadata (generated)
         - **languageSort.json** — Optional visible language sorting
   - **doc/**
      - **readme.pot** — Documentation template (generated)
      - **<language>/**
         - **readme.html** — User help guide (required for packaging)
         - **readme.po** — Optional gettext documentation source
         - **readme.md** — Optional Markdown documentation source

Regenerate templates after adding or modifying user-facing strings or English documentation. See [Checking and building](#checking-and-building) for exact commands.

---

## Prerequisites & Recommended Tools

`build_i18n.py` includes pure-Python fallbacks for all operations, allowing basic checks and builds to work out of the box. However, installing recommended libraries and GNU gettext tools is strongly advised for optimal performance, syntax validation, and feature support:

### 1. Python packages for documentation processing

```bash
pip install markdown nh3 lxml pymdown-extensions mdx_truly_sane_lists mdx_gh_links
```

- **`markdown` & extensions (`pymdown-extensions`, `mdx_truly_sane_lists`, `mdx_gh_links`)**: Converts Markdown to accessible HTML with full support for tables, tab-preserving code blocks, clean nested lists, GitHub links, and `<kbd>` tags.
- **`nh3` (Ammonia engine)**: High-speed HTML sanitization removing unsafe scripts while preserving documentation markup (`<kbd>`, MathML, anchor IDs, tables).
- **`lxml`**: High-performance XML/HTML parser that strictly validates HTML syntax and catches unclosed tags or broken structures.
- *(Optional)* **`l2m4m`** (`pip install l2m4m`): Converts LaTeX formulas to accessible MathML.

### 2. GNU gettext tools for PO/MO operations

`build_i18n.py` uses GNU `msgfmt` and `msgmerge` for strict PO syntax verification, template merging, and binary MO compilation:
- **Windows**: Install [Poedit](https://poedit.com/). `build_i18n.py` automatically detects `msgfmt.exe` and `msgmerge.exe` in standard Poedit installation folders.
- **Linux / WSL**: Install gettext via your package manager:
  ```bash
  sudo apt update && sudo apt install gettext
  ```

If `msgmerge` or `msgfmt` are absent, `build_i18n.py` falls back to internal pure-Python routines.

---

## Translation Components & Capabilities

`build_i18n.py` automates translation maintenance across UI strings and documentation:

1. **Interface Strings & Add-on Metadata (`locale/<language>/`)**:
   - `LC_MESSAGES/nvda.po`: Primary gettext source containing all UI strings, dialog labels, and manifest fields (`summary`, `description`).
   - `LC_MESSAGES/nvda.mo`: Runtime binary catalog compiled from `nvda.po` via GNU `msgfmt` or internal compiler.
   - `manifest.ini`: Translated metadata displayed in NVDA's Add-on Store, generated automatically from `nvda.po`.
   - `languageSort.json`: Optional custom alphabetical sorting rules for language names displayed in Voice Manager.
   - `nvda.pot`: Source template extracted from Python source code and manifest.

2. **User Documentation (`doc/<language>/`)**:
   - `readme.html`: User help guide opened via NVDA's Add-on Help menu. Required for release packaging.
   - `readme.po`: Optional gettext file extracted from English `readme.html` segments for translators using Poedit or translation memory tools.
   - `readme.md`: Optional Markdown document that can be bidirectionally converted to or from `readme.html`.
   - `readme.pot`: Documentation template extracted from `doc/en/readme.html`.

---

## Translation Quality & NVDA Conventions

- **Completeness & Accuracy**: Every translated section should be complete for its scope, clear to screen-reader users, and faithful to the source meaning. Never omit warnings, setup steps, limitations, security notes, or compatibility notes.
- **Standard NVDA Terminology**: Always match the established terms NVDA users hear in your target language for core concepts (e.g. Synthesizer, Voice, Rate, Pitch, Volume, Add-on Store, Input Gestures) and standard dialog buttons (`OK`, `Cancel`, `Apply`, `Close`, `Yes`, `No`).
- **Terminology Consistency**: When translating documentation, check your locale's `nvda.po` first. Reuse those exact terms for menu paths, dialog titles, settings, and gestures instead of inventing synonyms.

---

## Starting a New Language

Sync your local branch before starting. Use standard NVDA locale codes (e.g. `de`, `es`, `fr`, `ja`, `pt_BR`, `zh_CN`). If a language is not yet in NVDA's installed locale folder, the add-on translation can still be prepared (a notice will indicate the code is not detected in local NVDA installations).

### 1. Interface translation (UI)

1. Generate or refresh the UI template:
   ```powershell
   python build_i18n.py --extract-template
   ```
2. Create the target language directory:
   ```powershell
   mkdir googleTtsForNvda/locale/<language>/LC_MESSAGES
   ```
3. Copy `googleTtsForNvda/locale/nvda.pot` to `googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.po` (or initialize via Poedit).
4. Translate all `msgstr` entries.
5. Validate and build `nvda.mo` and `manifest.ini`:
   ```powershell
   python build_i18n.py --build-ui -l <language>
   ```

### 2. Documentation translation

Choose one of three supported documentation workflows:

- **Workflow A: Gettext PO (Recommended)**:
  1. Extract or update the documentation PO:
     ```powershell
     python build_i18n.py --update-doc-po -l <language>
     ```
  2. Translate `doc/<language>/readme.po` in Poedit.
  3. Compile to HTML:
     ```powershell
     python build_i18n.py --build-docs -l <language>
     ```

- **Workflow B: Markdown**:
  1. Create `doc/<language>/readme.md` (or convert English HTML with `python build_i18n.py --html-to-md -l en` and copy it).
  2. Translate the Markdown document.
  3. Compile to HTML:
     ```powershell
     python build_i18n.py --md-to-html -l <language>
     ```

- **Workflow C: Direct HTML**:
  1. Copy `doc/en/readme.html` to `doc/<language>/readme.html`.
  2. Translate the HTML directly, preserving tags and layout.
  3. Validate syntax:
     ```powershell
     python build_i18n.py --check-docs -l <language>
     ```

---

## Visible Language Sorting (`languageSort.json`)

Voice Manager normally follows catalog order. If a language requires custom alphabetical collation, add `googleTtsForNvda/locale/<language>/languageSort.json`:

```json
{
  "stripPrefixes": ["LanguagePrefix "],
  "letterOrder": ["a", "b", "c", "..."],
  "ignoreCombiningMarks": ["acute", "grave", "tilde", "circumflex"]
}
```

- `stripPrefixes`: Words or articles stripped solely for sorting (still visible in UI).
- `letterOrder`: Dedicated alphabetical sequence for languages where accented or modified letters have separate positions.
- `ignoreCombiningMarks`: Unicode combining mark names to ignore during sort comparisons.

This file is optional and only affects visible sorting in Voice Manager. It does not alter package IDs, downloads, or runtime logic.

---

## Checking and building

### Interactive Menu

Run without arguments for an interactive numbered menu:

```powershell
python build_i18n.py
```

```text
Task:
  1. Check or build UI translations (nvda.mo, manifest.ini)
  2. Generate UI string template (locale\nvda.pot)
  3. Update UI PO files from the template (locale\<lang>\LC_MESSAGES\nvda.po)
  4. Check or build documentation translations (doc\<lang>\readme.html)
  5. Generate documentation string template (doc\readme.pot)
  6. Update documentation PO files from the template (doc\<lang>\readme.po)
  7. Convert documentation HTML to Markdown (doc\<lang>\readme.html -> readme.md)
  8. Convert documentation Markdown to HTML (doc\<lang>\readme.md -> readme.html)
```

---

### Command-Line (CLI) Commands

In all commands below:
- Target a single language with `-l <language>` (e.g. `-l de`).
- Target multiple languages with repeated flags (`-l de -l fr`).
- Target all languages with `--all-languages`.

#### UI translation commands

```powershell
python build_i18n.py --extract-template                  # Generate UI template (locale/nvda.pot)
python build_i18n.py --update-po -l <language>           # Update UI PO from template
python build_i18n.py --check -l <language>               # Validate UI translations without writing files
python build_i18n.py --build-ui -l <language>            # Compile nvda.mo and generate manifest.ini
python build_i18n.py -l <language>                       # Shorthand for --build-ui
```

#### Documentation translation commands

```powershell
python build_i18n.py --extract-doc-template              # Generate doc template (doc/readme.pot)
python build_i18n.py --update-doc-po -l <language>       # Update doc PO from doc template
python build_i18n.py --check-docs -l <language>          # Validate documentation syntax and tags
python build_i18n.py --build-docs -l <language>          # Build readme.html (resolves PO -> MD -> HTML)
python build_i18n.py --html-to-md -l <language>          # Convert readme.html -> readme.md
python build_i18n.py --md-to-html -l <language>          # Convert readme.md -> readme.html
```

*Note on `--build-docs` priority:*
1. Compiles `doc/<lang>/readme.po` if present.
2. Converts `doc/<lang>/readme.md` if present.
3. Validates existing `doc/<lang>/readme.html`.

#### Custom tool paths

If GNU gettext or NVDA locale folders are in non-standard locations:
- `--msgmerge <path>`: Path to `msgmerge.exe`.
- `--msgfmt <path>`: Path to `msgfmt.exe`.
- `--nvda-locale-dir <path>`: Path to NVDA locale folder (can be repeated).

Example:
```powershell
python build_i18n.py --update-po -l <language> --msgmerge "C:\Tools\msgmerge.exe" --msgfmt "C:\Tools\msgfmt.exe"
```

---

## Check categories

Filter validation checks using `--checks <comma-separated-checks>`:

- `language`: Verifies the language code exists in NVDA's locale directory (when found).
- `manifest`: Verifies `summary` and `description` are translated in `nvda.po` and match `manifest.ini`.
- `docs`: Verifies documentation exists and RTL direction (`dir="rtl"`) is configured correctly.
- `ui`: Verifies all Python `_()` strings and manifest entries have non-empty translations.
- `placeholders`: Verifies HTML tags, links, and Python placeholders (`{runtime}`, `%s`) match between source and translation.
- `sort`: Verifies optional `languageSort.json` syntax and schema.
- `obsolete`: Flags active `msgid` entries in `.po` files no longer present in source templates.
- `fuzzy`: Reports fuzzy translation entries (`#, fuzzy`) needing translator review.
- `all`: Runs all available checks.

**Defaults**:
- UI check (`--check`): `language`, `manifest`, `docs`, `ui`, `placeholders`, `sort`, `obsolete`.
- Doc check (`--check-docs`): `language`, `docs`, `placeholders`, `obsolete`.

Examples:
```powershell
python build_i18n.py --check -l <language> --checks manifest,ui
python build_i18n.py --check -l <language> --checks placeholders,fuzzy
python build_i18n.py --check-docs -l <language> --checks docs,placeholders
python build_i18n.py --check -l <language> --checks all
```

---

## NVDA locale folders

`build_i18n.py` automatically checks standard NVDA installation folders:
- `C:\Program Files\NVDA\locale`
- `C:\Program Files (x86)\NVDA\locale`

To validate against a custom location or across WSL/Linux (`/mnt/c/...`):
```powershell
python build_i18n.py --check -l <language> --nvda-locale-dir "D:\NVDA\locale"
```

If no NVDA locale folder is found, `build_i18n.py` prints an informational warning and skips language-code matching without failing the build.
