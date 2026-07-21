# Translating Google TTS For NVDA

This add-on uses NVDA's gettext layout for interface and manifest translations:

```text
googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.po
googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.mo
googleTtsForNvda/locale/<language>/manifest.ini
```

Documentation is translated separately:

```text
googleTtsForNvda/doc/<language>/readme.html
```

The English source-string template lives at:

```text
googleTtsForNvda/locale/nvda.pot
```

Regenerate it after adding or changing user-facing strings. See [Checking and building](#checking-and-building) for the exact commands.

## What each translation part means

Each translation part affects a different place in the add-on:

- Interface strings in `nvda.po` are the text users hear or see inside NVDA dialogs, settings, prompts, progress messages, errors, and Voice Manager controls.
- `nvda.mo` is the compiled form of `nvda.po`. NVDA loads this file at runtime, so it must match the current `.po` when the translation is installed or packaged.
- The localized `manifest.ini` provides translated add-on metadata, especially the summary and description shown by NVDA's add-on interface.
- Translated documentation in `doc/<language>/readme.html` is the user's help page for the add-on in that language. It should explain installation, first-run behavior, settings, voice management, and any translation-specific notes in natural language.
- `languageSort.json` is optional. It only changes the visible order of language names in Voice Manager for that locale, so users see a natural alphabetic order in their own language.
- `nvda.pot` is not a user-facing translation. It is the English source-string template that translators use to create or update `.po` files.

## What the i18n script does

`build_i18n.py` validates translations, extracts the English source-string template, compiles `.po` files into `.mo`, and writes localized `manifest.ini` files. It does not create translated documentation or `languageSort.json`; those files are written by translators and then validated by the script.

## Translation quality

Whether you translate a complete locale or only a specific part, every translated part should be complete for its scope, easy for users to understand, and faithful to the source meaning. Do not omit warnings, setup steps, limitations, security notes, or compatibility notes from a translated section.

Use natural wording for the target language while keeping technical meaning accurate. Keep terminology consistent with the add-on UI, NVDA's own translated terms, and the surrounding documentation.

## Starting a new language

Before starting or updating a translation, sync your local source tree with the latest project changes. This helps you translate the newest source strings, manifest text, documentation, and any updated terminology instead of working from stale files.

Add-on translations should use language codes that NVDA supports. In practice, this means the language has been contributed to NVDA itself, including NVDA's interface and documentation translation, and appears in NVDA's installed `locale` folder. When that is true, this add-on accepts the same locale code.

Use NVDA's locale code for the target language. Examples:

```text
zh_CN  Simplified Chinese / Mainland China
km     Khmer
ru     Russian
```

If a language is not present in NVDA's installed locale folder yet, the add-on translation can still be prepared, but the validation script will report that the language code is not currently supported by that NVDA installation.

To start the interface translation:

1. Generate or refresh the source template if it is missing or stale.
2. Create this folder:

```text
googleTtsForNvda/locale/<language>/LC_MESSAGES
```

3. Use Poedit to create `nvda.po` from `googleTtsForNvda/locale/nvda.pot`, or copy the template to:

```text
googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.po
```

4. Translate all `msgstr` entries.
5. Translate the documentation by copying `googleTtsForNvda/doc/en/readme.html` to:

```text
googleTtsForNvda/doc/<language>/readme.html
```

When translating user documentation, check your locale's `nvda.po` and existing localized UI wording first. Reuse those exact terms for menu paths, dialog names, settings, status labels, and gesture-management wording instead of inventing synonyms. For Vietnamese, follow the terms already used in `googleTtsForNvda/locale/vi/LC_MESSAGES/nvda.po`, such as `Google TTS Cho NVDA`, `cấu hình`, `hồ sơ ngôn ngữ tự động`, `Trình quản lý giọng Google TTS`, and `Quản lý thao tác`.

6. If your language needs a custom alphabetic order for language names in Voice Manager, see [Visible language sorting](#visible-language-sorting).
7. Check or build the locale as described in [Checking and building](#checking-and-building).

## Visible language sorting

Voice Manager normally keeps the catalog order for language lists. If your translation needs a more natural visible order, add:

```text
googleTtsForNvda/locale/<language>/languageSort.json
```

This file is optional. It is only used to sort the language names that users see in Voice Manager. It must not change translated names, package IDs, catalog data, voice downloads, voice removal, or runtime behavior.

Example:

```json
{
  "stripPrefixes": ["Tiếng "],
  "letterOrder": ["a", "ă", "â", "b", "c", "d", "đ"],
  "ignoreCombiningMarks": ["grave", "acute", "tilde", "hook above", "dot below"]
}
```

Fields:

- `stripPrefixes`: visible prefixes ignored only for sorting. The prefix still appears in the UI.
- `letterOrder`: alphabet order for this translation.
- `ignoreCombiningMarks`: Unicode combining marks ignored while sorting.

For example, Vietnamese displays `Tiếng Anh`, but sorts it internally as `Anh`. The UI must still show `Tiếng Anh`, not `anh`.

If `languageSort.json` is missing, Voice Manager keeps catalog order. If the file is invalid, the translation checker reports an error. To check only this file, use the `sort` check described in [Check categories](#check-categories).

## Vietnamese

The Vietnamese translation is the in-tree example for a complete locale:

```text
googleTtsForNvda/locale/vi/LC_MESSAGES/nvda.po
googleTtsForNvda/doc/vi/readme.html
googleTtsForNvda/locale/vi/languageSort.json
```

Vietnamese generated files are:

```text
googleTtsForNvda/locale/vi/LC_MESSAGES/nvda.mo
googleTtsForNvda/locale/vi/manifest.ini
```

When Vietnamese text names standard dialog buttons, use the same labels NVDA users hear:

- `OK`: `Đồng ý`
- `Cancel`: `Hủy bỏ`
- `Yes`: `Có`
- `No`: `Không`

## Checking and building

After adding or changing user-facing source strings, regenerate the source-string template:

```powershell
python build_i18n.py --extract-template
```

On WSL/Linux, use `python3` instead of `python`:

```bash
python3 build_i18n.py --extract-template
```

After editing a translation, check it first:

```powershell
python build_i18n.py --check --language <language>
```

```bash
python3 build_i18n.py --check --language <language>
```

If your translation tool already saved synchronized `.po` and `.mo` files, this check is the normal use of `build_i18n.py` for the interface translation. If the `.mo` file or localized `manifest.ini` should be generated by the script, run:

```powershell
python build_i18n.py --language <language>
```

```bash
python3 build_i18n.py --language <language>
```

In non-interactive mode, `--all-languages` checks or builds every language folder currently present in:

```text
googleTtsForNvda/locale
```

For Vietnamese specifically, replace `<language>` with `vi`.

`build.bat` runs `python build_i18n.py --all-languages` automatically before packaging the add-on, so packaging does not stop at the interactive menu. `build.sh`, the WSL/Linux equivalent of `build.bat`, does the same thing with `python3 build_i18n.py --all-languages`.

For the interactive numbered menu, run:

```powershell
python build_i18n.py
```

```bash
python3 build_i18n.py
```

The menu opens by default when no arguments are provided. `python build_i18n.py --menu` is still accepted when you want to request it explicitly. The menu lists broad choices first: all add-on locales before individual locales, and default/all checks before individual check categories. It also lets you choose check-only or build mode and has a separate option to generate the source string template.

## Check categories

Available checks:

- `language`: verifies the language code exists in NVDA's locale folder, when available.
- `manifest`: verifies translated manifest summary and description.
- `docs`: verifies `doc/<language>/readme.html` exists.
- `ui`: verifies all Python `_()` strings and manifest strings have translations. This is included in the default checks.
- `placeholders`: verifies placeholders such as `{runtime}` and `{size:.1f}` match between `msgid` and `msgstr`.
- `sort`: verifies optional `locale/<language>/languageSort.json` files are valid.
- `obsolete`: reports active `msgid` entries in `nvda.po` that no longer exist in the current source strings.

By default, the script runs `language`, `manifest`, `docs`, `ui`, `placeholders`, `sort`, and `obsolete`. `--strict` is kept for compatibility; UI strings are already checked by default.

Examples:

```powershell
python build_i18n.py --check --language vi --checks manifest
python build_i18n.py --check --language vi --checks docs
python build_i18n.py --check --language vi --checks ui
python build_i18n.py --check --language vi --checks sort
python build_i18n.py --check --language vi --checks obsolete
python build_i18n.py --check --language vi --checks manifest,docs,ui
python build_i18n.py --check --language vi --checks all
```

Strict mode is still accepted for translation review workflows. Missing `msgstr` entries are reported with the first source locations where each string appears:

```powershell
python build_i18n.py --check --strict
python build_i18n.py --check --strict --language vi
```

The check fails when:

- The selected add-on locale folder does not exist.
- The language code is not present in NVDA's installed locale folder, when available.
- The localized `locale/<language>/manifest.ini` file is missing in check-only mode.
- The localized `doc/<language>/readme.html` file is missing.
- A manifest translation is missing from `nvda.po`.
- A current Python `_()` or manifest source string is missing from `nvda.po` or has an empty `msgstr`.
- `nvda.po` contains an active `msgid` that no longer exists in the current Python `_()` strings or manifest strings.
- Python-style placeholders such as `{runtime}` or `{package}` do not match between `msgid` and `msgstr`.
- The optional `locale/<language>/languageSort.json` file is present but has invalid JSON or invalid sorting fields.
- The `.po` file cannot be parsed.

Poedit may keep old strings as commented `#~ msgid` entries. Those are ignored by this checker. Only active `msgid` entries are treated as obsolete source strings.

Language codes are normalized, so `Vi` and `vi` both select `locale/vi`.

## NVDA locale folders

The script validates language codes against NVDA's installed locale folders when they exist:

```text
C:\Program Files\NVDA\locale
C:\Program Files (x86)\NVDA\locale
```

If NVDA is installed somewhere else, provide the locale folder explicitly:

```powershell
python build_i18n.py --check --strict --language vi --nvda-locale-dir "D:\NVDA\locale"
```

You can pass `--nvda-locale-dir` more than once when you need to validate against multiple NVDA installations.

On WSL, NVDA itself is installed on the Windows side, not inside the Linux filesystem. WSL can usually still read it through the `/mnt/c` mount, so point `--nvda-locale-dir` at the Windows install from WSL:

```bash
python3 build_i18n.py --check --strict --language vi --nvda-locale-dir "/mnt/c/Program Files/NVDA/locale"
```

`build.sh` does this automatically: it checks whether `/mnt/c/Program Files/NVDA/locale` exists and, if so, passes it as `--nvda-locale-dir` before running `--all-languages`. If that path is not found (a different Windows drive letter, a portable NVDA install, or a non-WSL Linux machine with no Windows side at all), `build_i18n.py` prints `[WARN] NVDA locale folder was not found; language-code support check is skipped.` and continues without failing the build.
