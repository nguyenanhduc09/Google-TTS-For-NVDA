# Google TTS For NVDA Updater Release Guide

This guide is for the person publishing a new Google TTS For NVDA release package and generating the `stable.json` manifest used by the updater.

## 1. Prepare version, build, and release metadata before building

Before building the final package, update the main add-on manifest when this is a new product
version:

```text
manifest.ini
version = ...
changelog = ...
```

The updater also uses internal hotfix build metadata:

```text
googleTtsForNvda/buildInfo.json
```

For a new version, set `baseVersion` to the same value as `manifest.ini` and reset
`updateBuild` to `1`:

```json
{
  "schema": 1,
  "baseVersion": "0.6",
  "updateBuild": 1
}
```

For a hotfix inside the same public version, keep `manifest.ini` unchanged and increment only
`updateBuild`:

```json
{
  "schema": 1,
  "baseVersion": "0.5",
  "updateBuild": 2
}
```

Do not use `size` or `sha256` to decide whether a hotfix exists. The updater compares
`baseVersion` first and compares `updateBuild` only when the installed and remote
`baseVersion` values are the same.

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

Build the final `.nvda-addon` package after the release metadata, `buildInfo.json`, and any
available localized changelogs have been merged.

The file name should follow this format:

```text
googleTtsForNvda-<version>.nvda-addon
```

Example:

```text
googleTtsForNvda-0.5.nvda-addon
```

The version in the file name must match the `version` value inside the package's `manifest.ini`.
Hotfix builds do not change the file name. For example, `0.5 build 1` and `0.5 build 2`
are both packaged as:

```text
googleTtsForNvda-0.5.nvda-addon
```

## 3. Generate stable.json

After building the `.nvda-addon` file, run:

```powershell
python make_update_manifest.py
```

This creates:

```text
dist\stable.json
```

The script automatically fills:

```text
schema
addonId
channel
version
baseVersion
displayVersion
updateBuild
fileName
url
size
sha256
minimumNVDAVersion
lastTestedNVDAVersion
releaseNotes
releaseNotesByLocale
```

Do not calculate `size` or `sha256` manually. The script reads them from the final
`.nvda-addon` file. It also reads `baseVersion` and `updateBuild` from the packaged
`buildInfo.json`, so generate `stable.json` only after rebuilding the add-on package.

When no package path is provided, the script scans the current directory recursively, finds valid `googleTtsForNvda-<version>.nvda-addon` packages, and uses the highest version it finds. By default, `stable.json` is written to the same directory as the selected `.nvda-addon` package. The selected package path and generated manifest path are printed in the command output.

If you need to use a specific package, you can still pass it explicitly:

```powershell
python make_update_manifest.py path\to\googleTtsForNvda-0.5.nvda-addon
```

Use an absolute `--output` path only when you intentionally want to write the update manifest somewhere else. Relative `--output` paths are resolved from the directory containing the selected `.nvda-addon` package.

## 4. Publish the GitHub Release

Create a GitHub Release using this tag format:

```text
v<version>
```

Example:

```text
v0.5
```

Upload both files as release assets:

```text
googleTtsForNvda-0.5.nvda-addon
stable.json
```

The release must be the latest stable release because the updater checks:

```text
https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/latest/download/stable.json
```

Do not use a draft release for a version that should be visible to the updater.

For a hotfix inside an existing version, replace the release assets with the same names. Upload
the new `.nvda-addon` first, then upload the regenerated `stable.json` last so the update
manifest points at the final package checksum as soon as possible. Replacing same-name assets is
not atomic; if a user checks during the short replacement window, checksum verification may fail
temporarily instead of installing the wrong package.

## 5. Verify before announcing

After uploading the release assets, verify these links:

```text
https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/latest/download/stable.json
https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/download/v0.5/googleTtsForNvda-0.5.nvda-addon
```

Check that `stable.json` contains the correct:

```text
version
baseVersion
displayVersion
updateBuild
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

The release version in `manifest.ini`, the `.nvda-addon` file name, the Git tag, and the URL inside `stable.json` must all match. The hotfix build number is tracked separately in `buildInfo.json` and `stable.json`. For example, version `0.5 build 2` uses:

```text
manifest.ini: version = 0.5
buildInfo.json: baseVersion = 0.5, updateBuild = 2
package: googleTtsForNvda-0.5.nvda-addon
tag: v0.5
url: https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/download/v0.5/googleTtsForNvda-0.5.nvda-addon
```

When moving from `0.5` to `0.6`, update `manifest.ini` to `0.6`, set
`buildInfo.json` to `baseVersion: "0.6"` and `updateBuild: 1`, build
`googleTtsForNvda-0.6.nvda-addon`, and publish it under tag `v0.6`.
