# Google TTS For NVDA Updater Release Guide

This guide is for the person publishing a new Google TTS For NVDA release package and generating the `stable.json` manifest used by the updater.

## 1. Prepare release metadata before building

Before building the final package, update the main add-on manifest:

```text
manifest.ini
version = ...
changelog = ...
```

The release publisher is responsible for the English `changelog` in the main `manifest.ini`.

Localized changelogs are contributor-provided translation inputs. If translators provide them before the final package is built, include them in:

```text
locale/<locale>/manifest.ini
changelog = ...
```

Examples:

```text
locale/vi/manifest.ini
locale/zh_CN/manifest.ini
locale/zh_TW/manifest.ini
```

The updater uses exact locale matches only. For example, `zh_CN` does not fall back to `zh`.

If a locale does not provide a changelog, it is skipped and users with that NVDA interface language will see the English release notes.

## 2. Build the add-on package

Build the final `.nvda-addon` package after the release metadata and any available localized changelogs have been merged.

The file name should follow this format:

```text
googleTtsForNvda-<version>.nvda-addon
```

Example:

```text
googleTtsForNvda-0.4.5.nvda-addon
```

The version in the file name must match the `version` value inside the package's `manifest.ini`.

## 3. Generate stable.json

After building the `.nvda-addon` file, run:

```powershell
python make_update_manifest.py
```

This creates:

```text
stable.json
```

The script automatically fills:

```text
schema
addonId
channel
version
fileName
url
size
sha256
minimumNVDAVersion
lastTestedNVDAVersion
releaseNotes
releaseNotesByLocale
```

Do not calculate `size` or `sha256` manually. The script reads them from the final `.nvda-addon` file.

When no package path is provided, the script scans the current directory recursively, finds valid `googleTtsForNvda-<version>.nvda-addon` packages, and uses the highest version it finds. The selected package path is printed in the command output.

If you need to use a specific package, you can still pass it explicitly:

```powershell
python make_update_manifest.py path\to\googleTtsForNvda-0.4.5.nvda-addon
```

## 4. Publish the GitHub Release

Create a GitHub Release using this tag format:

```text
v<version>
```

Example:

```text
v0.4.5
```

Upload both files as release assets:

```text
googleTtsForNvda-0.4.5.nvda-addon
stable.json
```

The release must be the latest stable release because the updater checks:

```text
https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/latest/download/stable.json
```

Do not use a draft release for a version that should be visible to the updater.

## 5. Verify before announcing

After uploading the release assets, verify these links:

```text
https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/latest/download/stable.json
https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/download/v0.4.5/googleTtsForNvda-0.4.5.nvda-addon
```

Check that `stable.json` contains the correct:

```text
version
url
size
sha256
releaseNotes
releaseNotesByLocale
minimumNVDAVersion
lastTestedNVDAVersion
```

## 6. Important notes

Generate `stable.json` from the final package that will be published.

Avoid editing `stable.json` manually. If it must be edited, make sure `url`, `size`, and `sha256` exactly match the uploaded `.nvda-addon` file.

The release version in `manifest.ini`, the `.nvda-addon` file name, the Git tag, and the URL inside `stable.json` must all match. For example, version `0.4.5` uses:

```text
manifest.ini: version = 0.4.5
package: googleTtsForNvda-0.4.5.nvda-addon
tag: v0.4.5
url: https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/download/v0.4.5/googleTtsForNvda-0.4.5.nvda-addon
```
