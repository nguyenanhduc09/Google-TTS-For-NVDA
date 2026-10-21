# Translating Google TTS For NVDA

Thank you for helping bring Google TTS to more NVDA users around the world!

Localizing this add-on involves two main parts:
1. **Interface Strings (UI)**: Menus, settings dialogs, messages, and add-on store metadata (`nvda.po`).
2. **User Documentation**: The user guide opened via NVDA's Add-on Help menu (`readme.html`, `readme.po`, or `readme.md`).

All translation tools are built into the standalone script [`build_i18n.py`](build_i18n.py). It includes an accessible interactive menu and runs with zero external dependencies out of the box.

---

## File and Directory Structure

All translation files reside within the `googleTtsForNvda/` folder:

- `googleTtsForNvda/`: Add-on root folder.
  - `locale/`: Interface string catalogs and add-on store metadata.
    - `nvda.pot`: UI string template (extracted from source code).
    - `<language>/`: Translation directory for your locale.
      - `manifest.ini`: Localized add-on title and summary.
      - `languageSort.json`: Optional custom collation or sorting rules.
      - `LC_MESSAGES/`: Gettext message catalogs.
        - `nvda.po`: Primary UI translation catalog (Gettext PO).
        - `nvda.mo`: Compiled binary catalog used by NVDA (generated).
  - `doc/`: User documentation and help guides.
    - `readme.pot`: Documentation string template (extracted from HTML).
    - `en/`: Base English documentation.
      - `readme.html`: Base English help guide.
    - `<language>/`: Localized documentation directory for your locale.
      - `readme.html`: Localized documentation guide (displayed in NVDA).
      - `readme.po`: Optional gettext documentation PO catalog.
      - `readme.md`: Optional Markdown documentation source.

> [!NOTE]
> `<language>` represents the standard NVDA locale code (for example, a two-letter language code like `<lang>` or a language and region code separated by an underscore like `<lang>_<REGION>`).

---

## Recommended Tools & Setup

`build_i18n.py` runs with zero external dependencies using Python's standard library. However, installing dedicated external tools beforehand is strongly recommended because they provide faster processing, strict syntax verification, and a superior translation experience:

- **[Poedit](https://poedit.com/)** (Recommended for UI and documentation PO files):
  - Provides a specialized translation interface that highlights placeholders, tags, and syntax errors.
  - Automatically bundles GNU gettext binaries (`msgfmt` and `msgmerge`), which `build_i18n.py` auto-detects for strict PO syntax verification, automatic template merging, and binary `.mo` compilation.
- **Python Libraries** (Recommended for documentation processing):
  ```bash
  pip install markdown nh3 lxml pymdown-extensions mdx_truly_sane_lists mdx_gh_links
  ```
  - `markdown` & extensions (`pymdown-extensions`, `mdx_truly_sane_lists`, `mdx_gh_links`): High-fidelity Markdown-to-HTML conversion.
  - `nh3`: Fast, secure HTML sanitization preserving accessibility markup (`<kbd>`, tables, IDs).
  - `lxml`: Strict HTML/XML parser that detects unclosed tags and structural syntax errors.
  - (Optional) `l2m4m` (`pip install l2m4m`): Converts LaTeX formulas to accessible MathML.

---

## Quick Start Guide

### Step 1: Open the Interactive Menu

In your terminal or command prompt, run:

```powershell
python build_i18n.py
```

This opens an accessible numbered menu:

```text
Google TTS For NVDA translation tools

Task:
  1. Check or build UI translations (nvda.mo, manifest.ini)
  2. Generate UI string template (locale\nvda.pot)
  3. Update UI PO files from the template (locale\<lang>\LC_MESSAGES\nvda.po)
  4. Check or build documentation translations (doc\<lang>\readme.html)
  5. Generate documentation string template (doc\readme.pot)
  6. Update documentation PO files from the template (doc\<lang>\readme.po)
  7. Convert documentation HTML to Markdown (doc\<lang>\readme.html -> readme.md)
  8. Convert documentation Markdown to HTML (doc\<lang>\readme.md -> readme.html)
  0. Exit
```

**Navigation tips:**
- Type a number and press <kbd>Enter</kbd> to choose an action.
- Choose `0` at the main menu to exit, or enter `0` in any submenu to return to the previous menu.
- All prompts require pressing <kbd>Enter</kbd>, allowing screen reader users to review or edit input before confirming.

---

### Step 2: Translate Interface Strings (UI)

1. **Create or update your language files:**
   - In the menu, select `3. Update UI PO files from the template`.
   - Choose your language from the list, or choose the manual entry option to enter your NVDA locale code.
   - The tool updates `googleTtsForNvda/locale/nvda.pot` from source code, then merges it into `googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.po`.
2. **Translate the strings:**
   - Open `nvda.po` in Poedit (recommended) or any UTF-8 text editor.
   - Fill in the translated text for each string in `msgstr`.
   - Translate the add-on `summary` and `description` headers near the top of the file.
3. **Validate and build:**
   - In the menu, select `1. Check or build UI translations`.
   - Choose your language (or `1. All addon locales`).
   - Select check mode: enter `1` for default checks (or choose a specific check category).
   - Select action: choose `2. Build generated files` (or `1. Check only` to validate without writing files).
   - The tool validates syntax, compiles `googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.mo`, and writes `googleTtsForNvda/locale/<language>/manifest.ini`.

---

### Step 3: Translate User Documentation

Choose one of three workflows according to your preference:

#### Option A: Gettext PO (Recommended)
1. In the menu, select `6. Update documentation PO files from the template` and choose your language. This updates `doc/readme.pot` from the base English guide and merges it into `googleTtsForNvda/doc/<language>/readme.po`.
2. Translate `readme.po` using Poedit.
3. In the menu, select `4. Check or build documentation translations`:
   - Choose your language.
   - Select check mode: enter `1` for default doc checks.
   - Select action: choose `2. Build generated files` to compile `googleTtsForNvda/doc/<language>/readme.html`.

#### Option B: Markdown
1. In the menu, select `7. Convert documentation HTML to Markdown`:
   - Choose `en` to convert the base English guide to `googleTtsForNvda/doc/en/readme.md`, then copy this file to `googleTtsForNvda/doc/<language>/readme.md`.
2. Translate the Markdown content in any UTF-8 text editor.
3. In the menu, select `8. Convert documentation Markdown to HTML` (or select task `4` with action `2. Build generated files`) to compile `googleTtsForNvda/doc/<language>/readme.html`.

#### Option C: Direct HTML
1. Copy `googleTtsForNvda/doc/en/readme.html` to `googleTtsForNvda/doc/<language>/readme.html`.
2. Translate the text inside the HTML tags directly, keeping all tags, attributes, and formatting intact.
3. In the menu, select `4. Check or build documentation translations`:
   - Choose your language.
   - Select check mode: enter `1` for default doc checks.
   - Select action: choose `1. Check only` to verify HTML syntax and structure.

---

## Translation Guidelines & Conventions

To ensure translations work reliably and sound natural with NVDA:

1. **Preserve Variables & Placeholders Exactly:**
   - Never translate placeholders like `{name}`, `{version}`, `{runtime}`, `%s`, or `%(key)s`.
   - Leave them in your translation exactly as they appear in the source string (for example, `"Found {count} voices"` must retain `{count}`).
2. **Preserve HTML Tags and Links:**
   - Keep tags like `<kbd>...</kbd>`, `<code>...</code>`, and `<strong>...</strong>` intact.
   - Do not translate URLs in `<a href="...">` links.
3. **Use Official NVDA Terminology:**
   - Match the standard terminology NVDA uses in your language for core concepts (such as Synthesizer, Voice, Rate, Pitch, Volume, Add-on Store, Input Gestures).
4. **Right-to-Left (RTL) Languages:**
   - For RTL languages (such as Arabic, Hebrew, or Persian), the build script automatically configures `dir="rtl"` in generated documentation HTML.

---

## Submitting Your Translation with GitHub Desktop

GitHub Desktop provides full keyboard shortcut navigation on Windows, making it accessible with screen readers. Once your translations are complete and verified:

1. **Clone your fork in GitHub Desktop:**
   - Open GitHub Desktop and press <kbd>Ctrl+Shift+O</kbd> (or press <kbd>Alt</kbd>, then navigate to **File** > **Clone repository...**).
   - Select your fork of `Google-TTS-For-NVDA` (or switch to the **URL** tab with <kbd>Tab</kbd> and paste the repository URL).
   - Set the local directory and press <kbd>Enter</kbd> to clone.
2. **Create a new branch:**
   - Press <kbd>Ctrl+Shift+N</kbd> (or press <kbd>Alt</kbd>, then navigate to **Branch** > **New branch...**).
   - Type a descriptive branch name (for example, `i18n-<language>`) and press <kbd>Enter</kbd>.
3. **Add and verify your translated files:**
   - Place your translated files in their respective directories:
     - `googleTtsForNvda/locale/<language>/`
     - `googleTtsForNvda/doc/<language>/`
   - Run `python build_i18n.py` to ensure all UI catalogs and documentation build without errors.
4. **Review and commit your changes:**
   - Switch to GitHub Desktop and press <kbd>Ctrl+1</kbd> to focus the **Changes** list.
   - Use the arrow keys and <kbd>Space</kbd> to verify that your modified or added files are selected.
   - Press <kbd>Ctrl+G</kbd> to move directly to the **Summary** input field.
   - Enter a concise title (for example: `Add <language> translation` or `Update <language> translation`).
   - Press <kbd>Ctrl+Enter</kbd> to commit your changes.
5. **Publish and submit a Pull Request:**
   - Press <kbd>Ctrl+P</kbd> to push your commits and publish the branch to GitHub.
   - Press <kbd>Ctrl+R</kbd> (or navigate to **Branch** > **Create pull request**) to open the Pull Request creation page in your web browser.
   - Review your pull request title and description, then press <kbd>Enter</kbd> on the **Create pull request** button.

> [!TIP]
> If you do not use GitHub Desktop or Git, you can also open an **Issue** on the repository and attach your translated `nvda.po` and documentation file.

---

## Command-Line (CLI) Reference

If you prefer running commands directly or are integrating with automated scripts:

```powershell
# UI translation commands
python build_i18n.py --extract-template              # Extract UI source strings to locale/nvda.pot
python build_i18n.py --update-po -l <language>       # Update or initialize locale/<lang>/LC_MESSAGES/nvda.po
python build_i18n.py --check -l <language>           # Validate UI translations without writing files
python build_i18n.py --build-ui -l <language>        # Compile nvda.mo and manifest.ini
python build_i18n.py --build-ui --all-languages      # Build UI translations for all locales

# Documentation translation commands
python build_i18n.py --extract-doc-template          # Extract doc strings to doc/readme.pot
python build_i18n.py --update-doc-po -l <language>   # Update or initialize doc/<lang>/readme.po
python build_i18n.py --check-docs -l <language>      # Validate documentation syntax and tags
python build_i18n.py --build-docs -l <language>      # Build doc/<lang>/readme.html from readme.po or readme.md
python build_i18n.py --build-docs --all-languages    # Build documentation for all locales

# Format conversion commands
python build_i18n.py --html-to-md -l <language>      # Convert doc readme.html -> readme.md
python build_i18n.py --md-to-html -l <language>      # Convert doc readme.md -> readme.html

# Additional options
python build_i18n.py --menu                          # Explicitly open the interactive numbered menu
python build_i18n.py --check -l <language> --nvda-locale-dir "C:\Program Files\NVDA\locale"  # Custom NVDA locale path
python build_i18n.py --update-po -l <language> --msgmerge "path/to/msgmerge.exe"             # Custom gettext path
```

### Validation Check Categories
`build_i18n.py` groups validation checks into specific categories:
- `language`: Validates that the language code is recognized in NVDA's official locale directory.
- `manifest`: Verifies `summary` and `description` are translated and written to `manifest.ini`.
- `docs`: Verifies documentation exists and RTL direction (`dir="rtl"`) is configured correctly.
- `ui`: Verifies that UI strings have non-empty translations.
- `placeholders`: Verifies HTML tags, links (`<a> href`), and Python placeholders (`{name}`, `%s`, `%(key)s`) match the source.
- `sort`: Validates syntax and schema of optional `languageSort.json`.
- `obsolete`: Flags old source strings in `.po` files no longer present in the template.
- `fuzzy`: Reports fuzzy translation entries (`#, fuzzy`) needing review.
- `all`: Runs all available checks across UI and documentation.

**Default check scopes:**
- **UI check** (`--check` / Task 1): `language`, `manifest`, `ui`, `placeholders`, `sort`, `obsolete`, `fuzzy`.
- **Documentation check** (`--check-docs` / Task 4): `language`, `docs`, `placeholders`, `obsolete`, `fuzzy`.

**Filtering checks on the command line:**
```powershell
python build_i18n.py --check -l <language> --checks manifest,ui,fuzzy
python build_i18n.py --check-docs -l <language> --checks docs,placeholders
```

---

## Advanced Options

### Custom Alphabetical Sorting (`languageSort.json`)
By default, language lists in Voice Manager follow catalog order. If your language requires custom alphabetical collation rules (for example, ignoring grammatical prefixes or handling accented letters in specific positions), you can optionally add `googleTtsForNvda/locale/<language>/languageSort.json`:

```json
{
  "stripPrefixes": ["LanguagePrefix "],
  "letterOrder": ["a", "b", "c"],
  "ignoreCombiningMarks": ["acute", "grave", "tilde"]
}
```
Field rules:
- `letterOrder` (required): A non-empty list of unique characters or substrings defining collation sequence.
- `stripPrefixes` (optional): A list of prefix strings to strip prior to alphabetical sorting.
- `ignoreCombiningMarks` (optional): A list of combining mark names (such as `"acute"`, `"grave"`, `"tilde"`) to ignore when comparing characters.
