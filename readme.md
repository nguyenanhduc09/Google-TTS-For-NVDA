# Google TTS For NVDA

An NVDA screen reader synthesizer add-on that uses Google's WebAssembly (WASM) Text-to-Speech engine locally through a supported Chromium browser runtime, such as Google Chrome, Microsoft Edge, or Brave, to provide high-quality, natural-sounding voices offline.

This project was created to make Google's high-quality local WebAssembly Text-to-Speech engine usable as a practical, everyday NVDA synthesizer on Windows computers.

*This add-on is co-developed by [Nguyen Anh Duc](https://github.com/nguyenanhduc09), [Dao Duc Trung](https://github.com/daoductrung) and [Pham Hung Vuong](https://github.com/phamhungvuong302).*

---

## Current Status

This add-on is actively maintained by Nguyen Anh Duc, Dao Duc Trung and Pham Hung Vuong.

---

## Features

* **Comprehensive Voice Support**: Supports the Google TTS languages and voice packages available to the bundled WasmTtsEngine.
* **100% Offline Speech**: Speech is rendered locally via a supported headless Chromium browser runtime, such as Google Chrome, Microsoft Edge, or Brave.
* **Low Latency**: Uses voice warm-up and background text segmentation to improve speech responsiveness.
* **Volatile Audio Cache**: In-memory cache for short phrases (under 5000 characters) to optimize repeated announcements safely.
* **Automatic Language Profiles**: Optionally use per-language profiles so different languages can have their own voice and speech values.
* **Voice Manager**: Browse, download, remove, and inspect Google TTS voice packages from an accessible dialog.
* **Background Operations**: Downloads and removals run without blocking NVDA.
* **Chromium Browser Runtime Selection**: Choose Google Chrome, Microsoft Edge, or Brave as the supported background browser runtime.
* **Built-in Add-on Updates**: Check for add-on updates from NVDA Settings, with an optional once-per-startup automatic check.

---

## Requirements

* **NVDA**: Version 2024.1 or newer. The add-on supports NVDA 2024 through 2026 on both 32-bit (x86) and 64-bit (x64) NVDA builds.
* **Chromium browser runtime**: Google Chrome, Microsoft Edge, or Brave must be present on the system. Microsoft Edge WebView2 Runtime is required only when Microsoft Edge is selected or used as the effective runtime; Google Chrome and Brave do not use WebView2.
* **Interactive Windows user session**: The add-on depends on a background supported Chromium browser runtime. Do not rely on it in environments where that runtime is unavailable or not allowed to start, such as the Windows sign-in screen, secure desktop contexts, Windows PE, recovery environments, or other minimal Windows sessions.

---

## Installation & First Run

You can install Google TTS For NVDA in one of these ways:

1. Download the latest `.nvda-addon` package from the [Releases](https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases) page. While NVDA is running, open the downloaded file directly from File Explorer and follow the prompts.
2. In NVDA, open **NVDA Menu -> Tools -> Add-on Store**. Choose **Install from external source**, select the downloaded `.nvda-addon` file, and follow the prompts.
3. In the future, once approved by NV Access in the Add-on Store, you will be able to install it directly without downloading a file first. Open **NVDA Menu -> Tools -> Add-on Store**. Move to the **Available add-ons** tab, search for **Google TTS For NVDA**, select it in the list, tab to **Actions**, choose **Install**, close the Add-on Store, and restart NVDA when prompted.

After installation:

1. Open NVDA's **Select Synthesizer** dialog with **`NVDA+Ctrl+S`** and choose **Google TTS For NVDA**.
2. Upon first selecting **Google TTS For NVDA** as your synthesizer, if no voice packages are installed, NVDA will prompt you indicating that no Google TTS For NVDA voices are installed. Press **OK** to open Google TTS Voice Manager and download a voice package, or press **Cancel** to keep using your current synthesizer.
3. Alternatively, you can also press **`NVDA+Ctrl+Shift+G`** or go to **NVDA Menu -> Tools -> Google TTS Voice Manager...** at any time to manage your voice packages.
4. In Google TTS Voice Manager, download at least one voice package from the **Download** tab.

---

## Managing Voice Packages

Google TTS Voice Manager has two tabs:

* **Installed**: Shows voice packages already stored on your computer. The **Status** column tells you whether each package is usable, unsupported by the bundled engine, missing a required package, required by another installed package, or dependent on another package.
* **Download**: Shows packages available to download. The **Status** column explains whether a package is available, requires another package, already has its required package installed, or is required by other downloadable packages.

On each tab, the controls appear in this order: **Filter by language**, **Check all voice packages**, the package list, then either **Remove checked voice packages** or **Download checked voice packages**. The dialog also has a **Status** line and the buttons **Refresh**, **Open voice packages folder**, and **Close**.

To add voices, use the **Download** tab, check the packages you want, and click **Download checked voice packages**. To remove voices, use the **Installed** tab, check the packages you no longer want, and click **Remove checked voice packages**.

Some voice packages depend on another package. When you download a dependent package, Voice Manager can also download the required package. When you remove a package that other installed packages depend on, Voice Manager includes those dependent packages so you do not leave unusable voices behind.

Voice Manager protects you from accidentally removing the last usable voice package. If Google TTS For NVDA is not the current synthesizer, the warning defaults to **No**. If Google TTS For NVDA is the current synthesizer, Voice Manager asks you to switch to another synthesizer first and keeps the last usable package installed if you do not switch away.

Downloads and removals run in the background. Progress announcements are kept to broad milestones and final results so they do not repeat every small percentage change.

---

## Configuration Settings

### Synthesizer Settings

NVDA's Speech settings dialog provides **Pauses** for Google TTS For NVDA whether automatic language profiles are on or off. When automatic language profiles are off, the dialog also provides:

* **Voice**: Choose the installed Google TTS language.
* **Variant**: Choose the voice name within that language, including Chrome OS and Google Natural voices when installed.
* **Rate**: Speech rate. Non-SeaNet packages use the Chromium browser runtime rate path; SeaNet packages may use post-synthesis artificial rate processing at higher speeds.
* **Rate Boost**: Enable to double the computed speech rate for fast reading. High-speed SeaNet speech may use more CPU because the add-on processes generated audio after synthesis.
* **Pitch**: Speech pitch adjustment.
* **Volume**: Speech volume (maps to the Chromium browser runtime's 0.0 - 1.0 volume range).

**Pauses** lets you choose whether pauses stay unchanged, are shortened only at the end of text, or are shortened throughout speech for faster reading. It applies to all Google TTS speech.

### Google TTS For NVDA Settings

The add-on includes a custom settings panel under **NVDA Settings (NVDA Menu -> Preferences -> Settings) -> Google TTS For NVDA**:
* **Chromium browser runtime**: Select which supported Chromium browser runtime to use (Google Chrome, Microsoft Edge, or Brave). The panel shows browser availability on your system.
* **Chromium browser runtime status**: Review the selected runtime, browser availability, and the active fallback runtime when one is used.
* **Use automatic language profiles**: Enable automatic profile selection and open the profile controls described below.
* **Preferred profile language**: Choose the enabled profile language used when a sentence is unclear. This list can be empty until at least one language profile is enabled.
* **Automatic language profile**: Choose which installed language profile to edit.
* **Use this language profile**: Enable the selected language profile.
* **Variant, Rate, Rate boost, Pitch, Volume, Capital pitch change percentage, Say cap before capitals, Beep for capitals, Use spelling functionality if supported**: Configure speech values for the selected enabled profile.
* **Automatic language profiles status**: Review whether profiles are off, unavailable, missing a selected profile, or active.
* **Updates**: Contains the add-on update controls below.
* **Automatically check for add-on updates when NVDA starts**: Let Google TTS For NVDA check once after the next NVDA startup. This is off by default.
* **Add-on update status**: Review the installed add-on version, whether automatic startup checks are on or off, and whether a check is currently running.
* **Check for updates...**: Manually check for the latest stable add-on release.

### Custom Browser Runtime Paths

Google TTS For NVDA normally finds Google Chrome, Microsoft Edge, or Brave automatically from common Windows install locations and the registry. If you use a portable browser, a custom installation folder, or a managed computer where the browser is installed somewhere unusual, set one of these environment variables to the full browser executable path:

* `CHROME_PATH` for Google Chrome
* `EDGE_PATH` for Microsoft Edge
* `BRAVE_PATH` for Brave

The value must point to the browser `.exe` file, not only to the containing folder. Common examples are:

```text
C:\Program Files\Google\Chrome\Application\chrome.exe
C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe
```

To set a path with the Windows graphical interface:

1. Press **`Windows+R`** to open the Run dialog.
2. Type `SystemPropertiesAdvanced` and press **Enter**. This opens the **Advanced** tab of the **System Properties** dialog.
3. Press **Tab** until you reach **Environment Variables...**, then press **Enter**.
4. In the **User variables** section, choose **New...**. Use **User variables** unless you specifically need the setting for every Windows account on the computer; editing **System variables** may require administrator permission.
5. In **Variable name**, enter one of these names: `CHROME_PATH`, `EDGE_PATH`, or `BRAVE_PATH`.
6. In **Variable value**, enter the full path to the matching browser executable, such as `C:\Program Files\Google\Chrome\Application\chrome.exe`.
7. Press **OK** to close each dialog.
8. Restart NVDA so the add-on can read the new value.

Screen reader note: in the **Environment Variables** dialog, there are two variable lists: **User variables** first, then **System variables**. If you hear the System variables list, press **Shift+Tab** to move back toward the User variables controls, or keep tabbing until you reach the **New...** button associated with User variables.

To set a path for the current Windows user from Command Prompt or PowerShell instead, use `setx`:

```bat
setx CHROME_PATH "C:\Path\To\chrome.exe"
setx EDGE_PATH "C:\Path\To\msedge.exe"
setx BRAVE_PATH "C:\Path\To\brave.exe"
```

After setting or changing one of these variables, restart NVDA so the add-on can read the new value. Then open **NVDA Settings -> Google TTS For NVDA** and select the matching Chromium browser runtime.

You only need to set the variable for the runtime you want to override. If the variable is missing or points to a file that does not exist, the add-on continues checking the normal install locations.

### Add-on Updates

Open **NVDA Settings -> Google TTS For NVDA** and press **Check for updates...** to check the latest stable release. Manual checks show an OK dialog when the add-on is already up to date, when the internet connection cannot be reached, or when the update response cannot be understood.

When a newer version is available, Google TTS For NVDA shows an **Update information** dialog with the download size, minimum NVDA version, last tested NVDA version, and the change log. Choose **Yes** to download the `.nvda-addon` package inside NVDA. While downloading, the **Downloading Google TTS For NVDA update** dialog shows **Download status**, a progress bar when the size is known, and **Cancel**. The add-on verifies the downloaded file against the update manifest's size and SHA256 checksum, then opens NVDA's normal add-on installer.

Automatic startup checks are off by default. If you turn them on, Google TTS For NVDA checks once after the next NVDA startup and stays silent unless a newer version is available. Manual checking remains available whether automatic checks are on or off. If a background startup check is already running while the settings category is open, the manual check button is disabled until that check finishes. Changing the **Automatically check for add-on updates when NVDA starts** checkbox affects future NVDA startups and does not interrupt a check that is already running.

### Automatic Language Profiles

When you enable **Use automatic language profiles**, the add-on uses its own per-language profiles instead of the normal NVDA Speech settings for detected sentences. If only one language profile is enabled, that profile is used for every sentence. This keeps your regular Google TTS language and variant settings unchanged for times when automatic language profiles are off.

Automatic language profiles use bundled CLD2 detector libraries for both 32-bit (x86) and 64-bit (x64) NVDA builds. CLD2 results are accepted only when they are reliable enough for one of the enabled languages. If text is too short or unclear, the add-on uses conservative local language signals where available, then falls back to the preferred enabled language.

In the Google TTS For NVDA settings category:

1. Turn on **Use automatic language profiles**.
2. Move to **Preferred profile language**. Only enabled languages appear in this list, so it may be empty until you enable a profile.
3. Choose an **Automatic language profile**.
4. Select the **Use this language profile** checkbox for each language you want automatic language profiles to use. Language profiles are off until this checkbox is selected.
5. For each enabled language, set **Variant**, **Rate**, **Rate boost**, **Pitch**, **Volume**, **Capital pitch change percentage**, **Say cap before capitals**, **Beep for capitals**, and **Use spelling functionality if supported**.
6. Return to **Preferred profile language** and choose the enabled language used when a sentence is unclear or does not contain enough language clues.

Only enabled languages appear in the preferred language list. Rate, pitch, and volume use sliders like NVDA's Speech settings. Capital-letter pitch uses a numeric edit/spin control so you can adjust it precisely.

The Google TTS settings category includes a focusable status line for automatic language profiles. It changes with the current state:

* If no language voice package is installed, it asks you to install at least one language voice package.
* If automatic language profiles are off, it explains that Google TTS is using NVDA's normal Speech Settings values for voice, variant, rate, pitch, volume, capitals, and spelling.
* If automatic language profiles are on but no language profile is selected, it asks you to select at least one language profile.
* If one or more profiles are selected, it explains that the selected installed language profiles are used; with one selected profile, that profile is used for every sentence.

NVDA-wide settings such as punctuation and symbol level, automatic dialect switching, language change reporting, Unicode normalization, Unicode Consortium data including emoji, extra symbol dictionaries, delayed character descriptions, and speech mode choices remain in NVDA's Speech settings.

Google TTS ignores NVDA's own automatic language and dialect switching commands for this synthesizer, so those global options do not force Google TTS to change voices. Use Google TTS automatic language profiles when you want automatic voice/profile selection in this add-on. If NVDA's "Report language changes while reading" option is enabled, reported language names follow Google TTS automatic language profile detection while profiles are on. When profiles are off, NVDA can still use app-provided language changes for language reporting and character/symbol processing, including punctuation and symbol dictionaries, but Google TTS will not change voices from those commands.

Automatic language profiles set the language context before NVDA processes text, so NVDA's symbol pronunciation and speech dictionary processing stay in the normal speech flow for the selected language context.

When automatic language profiles are off, NVDA voice dictionaries work normally for the currently selected Google TTS variant. When automatic language profiles are on, the add-on temporarily uses the voice dictionary for each enabled language profile's selected variant while NVDA processes that segment. NVDA's default and temporary dictionaries still follow NVDA's normal behavior.

While automatic language profiles are enabled, NVDA's Speech settings will not offer the normal voice, variant, rate, rate boost, pitch, and volume controls for this synthesizer. Instead, it shows a focusable notice telling you to configure these values from **NVDA Settings -> Google TTS For NVDA**. **Pauses** remains available in Speech settings and applies to all Google TTS speech. The normal synth settings ring commands, such as **NVDA+Ctrl+Arrow keys** on desktop keyboards or **NVDA+Shift+Ctrl+Arrow keys** on laptop keyboards, announce this notice instead of changing the Google TTS voice or speech values in this mode. Google TTS also uses each enabled profile's capital-letter and spelling options while automatic language profiles are on; the normal Speech settings values remain available again when automatic language profiles are turned off. Status messages in the Google TTS For NVDA settings category are also reachable with Tab so screen readers can announce them.

---

## Troubleshooting Edge Runtime Silence

If you choose **Microsoft Edge** as the Chromium browser runtime, Google TTS For NVDA also checks whether Microsoft Edge WebView2 Runtime is available. Edge may be installed while WebView2 is missing or damaged, and in that case the add-on cannot use Edge for speech. Google Chrome and Brave do not depend on WebView2.

WebView2 is the Microsoft Edge runtime used by native Windows applications to host web content. It is related to Microsoft Edge, but having the normal Edge browser available does not always mean the WebView2 Runtime is installed and healthy for app scenarios. Microsoft recommends the Evergreen WebView2 Runtime for applications because it is shared, automatically updated, and the small Evergreen Bootstrapper downloads the matching runtime for the device architecture.

When WebView2 is needed, Google TTS For NVDA offers buttons to download Microsoft's online Evergreen Bootstrapper, open Microsoft's WebView2 page for offline installers or fixed-version packages, or leave the change for later. NVDA keeps using the previous synthesizer until WebView2 is available.

If Windows cannot open the download page, the add-on shows the download address in a labeled read-only field and includes a **Copy link** button so you can paste the address into a browser manually.

For an online computer, download the Microsoft Edge WebView2 Evergreen Bootstrapper here: [Download Microsoft Edge WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703).

Run the installer, restart NVDA, then select **Google TTS For NVDA** again. For offline installers or a fixed-version WebView2 Runtime package, open Microsoft's WebView2 page: [Microsoft Edge WebView2](https://developer.microsoft.com/microsoft-edge/webview2).

---

## Build Instructions (For Advanced Users)

To package the add-on yourself:

1. Clone this repository using `git clone https://github.com/nguyenanhduc09/Google-TTS-For-NVDA.git` and navigate to the directory.
2. Make sure you have **Python** and **Node.js** installed on your system.
3. Run the automated build script:

```bat
build.bat
```

On WSL or Linux, run the equivalent shell script instead:

```bash
bash build.sh
```

Both scripts read the version from `googleTtsForNvda/manifest.ini`, build all add-on locales non-interactively, check Python and JavaScript syntax, verify that no `.zvoice` voice packages are inside the source tree, remove generated `__pycache__` folders, and package the add-on. `build.sh` needs Python 3, Node.js, and `zip` on the WSL/Linux side.

WSL/Linux can build, check, and package the add-on, but it cannot run or test it: NVDA and the Chromium browser runtime this add-on depends on are Windows-only, so install and runtime testing must still happen on Windows. WSL's Python is usually newer than the one bundled with NVDA, so its syntax check catches outright syntax errors but cannot catch new syntax that NVDA's own Python does not accept yet.

The verified `.nvda-addon` package will be created in the `dist/` directory, with a name like:

```text
dist/googleTtsForNvda-0.5.nvda-addon
```

---

## Contributing

We strongly welcome contributions from the community. Pull requests are considered for acceptance when the code runs reliably, fits the add-on's existing logic and interface, avoids introducing regressions in other add-on features, does not interfere with other NVDA add-ons or NVDA itself, does not weaken add-on security, and does not collect user data without permission. If you have ideas, bug fixes, improvements, or language updates, please feel free to open an issue or submit a pull request. For localization workflow details, see [TRANSLATING.md](TRANSLATING.md); translation contributors should review its translation quality guidance and sync the source tree before starting or updating a translation.

---

## Contact

If you have any questions, feedback, or need support, feel free to reach out to us via email or Telegram:
* **Nguyen Anh Duc**: [ducna1803@gmail.com](mailto:ducna1803@gmail.com) | Telegram: [t.me/anhduc1803](https://t.me/anhduc1803)
* **Dao Duc Trung**: [trung@ddt.one](mailto:trung@ddt.one) | Telegram: [t.me/Daoductrung](https://t.me/Daoductrung)
* **Pham Hung Vuong**: [hungvuong106206@gmail.com](mailto:hungvuong106206@gmail.com) | Telegram: [t.me/phamhungvuong302](https://t.me/phamhungvuong302)
