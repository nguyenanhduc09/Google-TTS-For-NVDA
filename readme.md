# Google TTS For NVDA

**Google TTS For NVDA** is an open-source NVDA screen reader synthesizer add-on that brings Google's natural, high-quality WebAssembly (WASM) Text-to-Speech voices to Windows 10 and Windows 11. The engine runs locally inside a **bundled speech runtime**, so it provides fully offline, low-latency, and responsive speech without an active internet connection and **without requiring you to install a web browser**.

For a long time, screen reader users have wished they could use familiar voices from Google's text-to-speech technology on NVDA for Windows, similar to the experience on Android screen readers. Google TTS For NVDA fulfills that wish, delivering responsive, natural speech to enhance daily accessibility. Once your desired voice packages are downloaded, the add-on operates entirely offline on your computer.

*This add-on is co-developed by [Nguyễn Anh Đức](https://github.com/nguyenanhduc09), [Đào Đức Trung](https://github.com/daoductrung) and [Phạm Hùng Vương](https://github.com/phamhungvuong302).*

**Independence notice:** Google TTS For NVDA is an independent, open-source accessibility project. It is not affiliated with, sponsored by, or endorsed by Google LLC. Google is a trademark of Google LLC.

---

## Table of Contents

* [Current Status](#current-status)
* [Key Features](#key-features)
* [System Requirements](#system-requirements)
* [Quick Start Guide](#quick-start-guide)
* [Managing Voices](#managing-voices)
* [Configuration](#configuration)
  * [Speech Settings](#speech-settings)
  * [Add-on Settings](#add-on-settings)
  * [Custom Browser Runtime Paths](#custom-browser-runtime-paths)
  * [Add-on Updates](#add-on-updates)
  * [Automatic Language Profiles](#automatic-language-profiles)
* [Troubleshooting Edge Runtime Silence](#troubleshooting-edge-runtime-silence)
* [Clearing Browser Runtime Cache and Resolving Conflicts](#clearing-browser-runtime-cache-and-resolving-conflicts)
* [Frequently Asked Questions](#frequently-asked-questions)
* [Build Instructions (For Advanced Users)](#build-instructions-for-advanced-users)
* [Community Contributions](#community-contributions)
* [Support & Contact](#support--contact)

---

## Current Status

Latest stable release: **Google TTS For NVDA 1.0**, last updated on **October 12, 2026**.

Version 1.0 marks a significant step in the project's maturity. With a supported Chromium browser runtime available, Google TTS For NVDA is now a dependable choice for everyday NVDA use, offering faster long-text reading, broader multilingual text handling, steady speech output, and resilient runtime recovery.

* Full change log for version 1.0: [Google TTS For NVDA 1.0 release notes](https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/tag/v1.0).
* Download the latest stable package: [googleTtsForNvda-1.0.nvda-addon](https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/download/v1.0/googleTtsForNvda-1.0.nvda-addon).

---

## Key Features

* **Fully offline operation:** Speech is processed locally after the needed voice packages are downloaded.
* **Diverse languages and voices:** Provides multiple Google TTS voice packages for supported languages with high-quality natural voices.
* **Fast response and low latency:** Uses text segmentation and audio buffering to improve NVDA responsiveness.
* **Google TTS Voice Manager:** Browse, download, remove, and inspect voice packages from an accessible dialog.
* **Bundled speech runtime:** Google's WASM engine runs inside a Bun executable that ships with the add-on, so no browser installation is required.
* **Chromium browser runtime selection:** Optionally run the engine in Google Chrome, Microsoft Edge, or Brave instead, with automatic candidate fallback if the selected browser is unavailable.
* **Automatic language profiles:** Optionally use per-language profiles so different languages can have their own voice and speech values.
* **Built-in add-on updates:** Check for add-on updates directly from NVDA Settings, with an optional once-per-startup automatic check.

---

## System Requirements

* **NVDA:** Version 2024.1 or newer. The add-on supports NVDA 2024 through 2026 on both 32-bit (x86) and 64-bit (x64) NVDA builds.
* **Speech runtime:** The bundled runtime ships inside the add-on, so nothing else needs to be installed. It is a separate 64-bit process, which works under both 64-bit (x64) and 32-bit (x86) NVDA as long as Windows itself is 64-bit. On 32-bit Windows, select a Chromium browser runtime (Google Chrome, Microsoft Edge, or Brave) in the add-on settings instead; Microsoft Edge additionally requires the Microsoft Edge WebView2 Runtime.
* **Interactive Windows user session:** The add-on depends on a background speech runtime process. Do not rely on it where that process cannot start, such as the Windows sign-in screen, secure desktop contexts, Windows PE, recovery environments, or other minimal Windows sessions.

---

## Quick Start Guide

### Installation

You can install Google TTS For NVDA in one of these ways:

1. **Direct download:** Download the latest `.nvda-addon` file from the [Releases](https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases) page. While NVDA is running, open the downloaded file directly from File Explorer and follow the prompts.
2. **NVDA Add-on Store (External source):** In NVDA, open **NVDA Menu -> Tools -> Add-on Store**, choose **Install from external source**, select the downloaded `.nvda-addon` file, and confirm.
3. **NVDA Add-on Store (Direct):** Once approved by NV Access, search for **Google TTS For NVDA** in the **Available add-ons** tab, press <kbd>Enter</kbd>, choose **Install**, and restart NVDA when prompted.

### First Run Setup

1. Open NVDA's **Select Synthesizer** dialog by pressing <kbd>NVDA+Ctrl+S</kbd> and choose **Google TTS For NVDA**.
2. If no voice packages are installed, NVDA will display a message indicating that no Google TTS For NVDA voices are installed. Press **OK** to open Google TTS Voice Manager and download a voice package. Press **Cancel** to keep using your current synthesizer for now.
3. In Google TTS Voice Manager, download at least one voice package from the **Download** tab.
4. Once downloaded, open **Speech settings** (<kbd>NVDA+Ctrl+V</kbd>) to select your preferred language and variant.

---

## Managing Voices

You can open Google TTS Voice Manager at any time to add or remove voice packages:

* Press the shortcut <kbd>NVDA+Ctrl+Shift+G</kbd>, or
* Go to **NVDA Menu -> Tools -> Google TTS Voice Manager...**

Google TTS Voice Manager has two tabs:

* **Installed:** Shows voice packages that are already stored on your computer in `%appdata%\nvda\googleTtsForNvda\voices\`. The **Status** column tells you whether each package is **Usable**, not supported by the bundled engine, missing a required package, required by another installed package, or dependent on another package.
* **Download:** Shows packages that can be downloaded from Google's voice catalog. The **Status** column explains whether a package is available to download, requires another package, already has its required package installed, or is required by other downloadable packages.

On each tab, the controls appear in this order: **Filter by language**, **Check all voice packages**, the package list, then either **Remove checked voice packages** or **Download checked voice packages**. The dialog also provides a focusable **Status** line and buttons for **Refresh**, **Open voice packages folder**, and **Close**.

* **Adding and removing voices:** On the **Download** tab, check the packages you want and press **Download checked voice packages**. On the **Installed** tab, check the packages you no longer want and press **Remove checked voice packages**.
* **Automatic dependency handling:** Some voice packages depend on another package. When you download a dependent package, Voice Manager can also download the required package. When you remove a package that other installed packages depend on, Voice Manager includes those dependent packages so you do not leave unusable voices behind.
* **Safety protection:** Voice Manager protects you from accidentally removing the last usable voice package. If Google TTS For NVDA is not the current synthesizer, the warning defaults to No. If Google TTS For NVDA is the current synthesizer, Voice Manager asks you to switch to another synthesizer first and keeps the last usable package installed if you do not switch away.
* **Language filtering:** You can use the **Filter by language** dropdown box on either tab to display only the voice packages for your desired language to find voices more quickly. Additionally, you can click **Open voice packages folder** if you wish to check where voice packages are stored on your computer.
* **Background operation:** Voice downloads and removals are handled in the background so you can continue using NVDA without interruption. Progress announcements are kept to broad milestones and final results so they do not repeat every small percentage change.

---

## Configuration

### Speech Settings

NVDA's Speech settings dialog (<kbd>NVDA+Ctrl+V</kbd>) provides **Pauses** for Google TTS For NVDA whether automatic language profiles are on or off. When automatic language profiles are off, the dialog also provides:

* **Voice:** Choose the installed Google TTS language.
* **Variant:** Choose the voice name within that language, including Chrome OS and Google Natural voices when installed.
* **Rate:** Adjust the reading speed. Non-SeaNet packages use the Chromium browser runtime rate path; SeaNet packages may use post-synthesis artificial rate processing at higher speeds.
* **Rate Boost:** Enable to double the computed speech rate for fast reading. High-speed SeaNet speech may use more CPU because the add-on processes generated audio after synthesis.
* **Pitch:** Adjust the pitch of the voice.
* **Volume:** Adjust the speech volume.
* **Pauses:** Choose whether pauses stay unchanged, are shortened only at the end of text, or are shortened throughout speech for faster reading. This setting was inspired by the [IBMTTS driver for NVDA](https://github.com/davidacm/NVDA-IBMTTS-Driver) and applies to all Google TTS speech.

### Add-on Settings

Under **NVDA Menu -> Preferences -> Settings -> Google TTS For NVDA**, you can configure:

* **Speech runtime:** Choose the **bundled runtime** (default, no browser required) or a **Chromium browser runtime**. The status line reports whether the bundled runtime was found and which runtime will actually be used.
* **Chromium browser runtime:** When a Chromium browser runtime is selected, choose Google Chrome, Microsoft Edge, or Brave. The settings panel displays browser availability.
* **Chromium browser runtime status:** Review the selected runtime, browser availability, and the active fallback runtime when one is used.
* **Keep Google TTS browser runtime ready in the background:** Turn on to let Google TTS prepare the browser runtime and current voice package while another synthesizer is active, making switching back to Google TTS faster. This setting is off by default. After you turn it on and press OK or Apply, it may use more memory and keep runtime files open until you turn it off or exit NVDA.
* **Background browser runtime status:** Review whether this setting is off, unnecessary because Google TTS is already the current synthesizer, or waiting to start after you press OK or Apply.
* **Use automatic language profiles:** Enable automatic profile selection and open the profile controls described below:
  * **Preferred profile language:** Choose the enabled profile language used when a sentence is unclear. This list can be empty until at least one language profile is enabled.
  * **Automatic language profile:** Choose which installed language profile to edit.
  * **Use this language profile:** Enable the selected language profile.
  * **Variant, Rate, Rate boost, Pitch, Volume, Capital pitch change percentage, Say cap before capitals, Beep for capitals, Use spelling functionality if supported:** Configure speech values for the selected enabled profile.
  * **Automatic language profiles status:** Review whether profiles are off, unavailable, missing a selected profile, or active.
* **Updates:** Contains the add-on update controls below:
  * **Automatically check for add-on updates when NVDA starts:** Let Google TTS For NVDA check once after the next NVDA startup. This is off by default.
  * **Add-on update status:** Review the installed add-on version, whether automatic startup checks are on or off, and whether a check is currently running.
  * **Check for updates...:** Manually check for the latest stable add-on release.
* **Input Gestures:** You can assign a custom shortcut to open this settings panel directly from **NVDA Menu -> Preferences -> Input Gestures** under the **Google TTS For NVDA** category.

### Custom Browser Runtime Paths

The bundled speech runtime is shipped inside the add-on and normally needs no configuration. Set `GTTS_BUN_PATH` to the full path of a Bun executable if you want to use your own copy instead (for example to test a newer Bun build).

Google TTS For NVDA automatically discovers Google Chrome, Microsoft Edge, or Brave from standard Windows installation locations and the registry. If you use a portable browser, a custom installation folder, or an enterprise environment where the executable resides elsewhere, set one of these environment variables to the full executable path:

* `CHROME_PATH` for Google Chrome (points to `chrome.exe`)
* `EDGE_PATH` for Microsoft Edge (points to `msedge.exe`)
* `BRAVE_PATH` for Brave (points to `brave.exe`)
* `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER` for Microsoft Edge WebView2 Runtime (points to the folder containing `msedgewebview2.exe` or fixed-version packages)

#### Setting paths via Windows GUI:

1. Press <kbd>Windows+R</kbd>, type `SystemPropertiesAdvanced`, and press <kbd>Enter</kbd>.
2. Press <kbd>Tab</kbd> to reach **Environment Variables...** and press <kbd>Enter</kbd>.
3. In the **User variables** section, choose **New...**. (Screen reader note: in the Environment Variables dialog, there are two variable lists: User variables first, then System variables. If you hear System variables, press <kbd>Shift+Tab</kbd> to return to User variables).
4. In **Variable name**, enter `CHROME_PATH`, `EDGE_PATH`, `BRAVE_PATH`, or `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER`.
5. In **Variable value**, enter the full path to the executable or folder.
6. Press **OK** on each dialog, then restart NVDA so the add-on reads the new path.

#### Setting paths via Command Prompt or PowerShell:

```bat
setx CHROME_PATH "C:\Path\To\chrome.exe"
setx EDGE_PATH "C:\Path\To\msedge.exe"
setx BRAVE_PATH "C:\Path\To\brave.exe"
```

### Add-on Updates

* **Manual Checks:** Open **NVDA Menu -> Preferences -> Settings -> Google TTS For NVDA** and press **Check for updates...**. If an update is available, the **Update information** dialog displays the release notes, download size, and version requirements. Choosing **Yes** downloads the package, then opens NVDA's standard add-on installer.
* **Automatic Checks:** Enabling **Automatically check for add-on updates when NVDA starts** checks once silently after startup and notifies you only when a new release is available.
* **Hotfix Builds:** The updater recognizes small hotfix builds within the same public version number, delivering urgent compatibility or stability fixes without waiting for a new major release.
* **NVDA Add-on Store:** Once published in the Add-on Store, updates can also be installed directly from **NVDA Menu -> Tools -> Add-on Store -> Updatable add-ons**.

### Automatic Language Profiles

When **Use automatic language profiles** is enabled, the add-on dynamically applies per-language voice and speech settings to detected sentences, inspired by [Auto TTS](https://play.google.com/store/apps/details?id=com.vnspeak.autotts) on Android:

1. Open **NVDA Menu -> Preferences -> Settings -> Google TTS For NVDA**.
2. Check **Use automatic language profiles**.
3. Move to **Preferred profile language** and select your primary language fallback. Only enabled languages appear in this list, so it may be empty until you enable a profile
4. Choose an **Automatic language profile** from the list.
5. Check **Use this language profile** for each language you wish to activate.
6. Configure **Variant**, **Rate**, **Rate boost**, **Pitch**, **Volume**, capital-letter handling, and spelling behavior for that language.
7. Return to **Preferred profile language** and choose the enabled language used when a sentence is unclear.
8. Press **OK** or **Apply** to save changes.

Rate, pitch, and volume use slider controls like NVDA's Speech Settings. Capital-letter pitch uses a numeric edit/spin control so you can adjust it precisely.

> [!NOTE]
> While automatic language profiles are enabled, NVDA's Speech settings dialog (<kbd>NVDA+Ctrl+V</kbd>) and the synth settings ring (<kbd>NVDA+Ctrl+Shift+Arrow keys</kbd>) display a focusable notice directing you to configure voice parameters in the add-on settings category. This keeps per-language profile values protected from accidental overwrites. **Pauses** remains available in Speech settings and applies to all Google TTS speech. When automatic language profiles are turned off, normal Speech Settings controls and values immediately return.

---

## Troubleshooting Edge Runtime Silence

If you choose **Microsoft Edge** as the Chromium browser runtime and speech remains silent:

1. **Verify WebView2 Runtime:** Microsoft Edge requires the Microsoft Edge WebView2 Runtime for headless engine hosting. Google Chrome and Brave do not depend on WebView2.
2. **Install Evergreen Bootstrapper:** Download Microsoft's small bootstrapper installer: [Download Microsoft Edge WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703).
3. **Restart NVDA:** Run the installer, restart NVDA, then select **Google TTS For NVDA** again.
4. **Offline Installers:** For offline machines or fixed versions, visit [Microsoft Edge WebView2](https://developer.microsoft.com/microsoft-edge/webview2). If using a fixed-version package, you can set `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER` to that directory.

---

## Clearing Browser Runtime Cache and Resolving Conflicts

If Google TTS encounters profile conflicts (exit code 21 or 0), fails to initialize after a browser update, or remains silent:

> [!NOTE]
> Clearing the browser runtime cache does **not** delete your downloaded voice packages. Voice packages (`.zvoice` files) are stored separately in `%appdata%\nvda\googleTtsForNvda\voices\` and remain completely safe.

1. **Switch Synthesizers:** Press <kbd>NVDA+Ctrl+S</kbd>, select any other synthesizer (such as **Windows OneCore voices**), and press <kbd>Enter</kbd>.
2. **Disable Background Readiness:** Open **NVDA Menu -> Preferences -> Settings -> Google TTS For NVDA**, uncheck **Keep Google TTS browser runtime ready in the background**, and press **OK** so all browser processes cleanly exit.
3. **Open Cache Folder:** Press <kbd>Windows+R</kbd>, type `%localappdata%\googleTtsForNvda`, and press <kbd>Enter</kbd>.
4. **Delete Runtime Profiles:** Press <kbd>Ctrl+A</kbd> to select all folders inside (`chromeProfiles`, `edgeProfiles`, `braveProfiles`) and press <kbd>Shift+Delete</kbd>, then choose **Yes** to delete them.
5. **Switch Back to Google TTS:** Press <kbd>NVDA+Ctrl+S</kbd>, select **Google TTS For NVDA**, and press <kbd>Enter</kbd>. The add-on will automatically rebuild clean, conflict-free runtime profiles.

---

## Frequently Asked Questions

### Can Google TTS For NVDA work offline?
Yes. After downloading your desired voice packages, the add-on generates speech locally on your computer through the bundled WASM TTS Engine and the local Chromium browser runtime. An internet connection is only needed to download additional voice packages or add-on updates.

### Which Chromium browser runtimes are supported?
Google Chrome, Microsoft Edge, and Brave are supported. You can select your preferred browser from the add-on settings category, and the add-on can automatically fall back to another installed supported runtime if the chosen browser cannot start.

### Must a browser window remain open while NVDA is speaking?
No. The add-on starts and manages its own background headless Chromium process. You do not need to open or keep a regular browser window open.

### Why can the first speech take a moment after NVDA starts?
The Chromium browser runtime, WASM TTS Engine, and selected voice package require a brief startup phase. Enabling **Keep Google TTS browser runtime ready in the background** prepares the runtime sooner while another synthesizer is active, at the cost of additional background memory use.

### Why are normal voice controls disabled in NVDA Speech Settings when automatic language profiles are enabled?
Because automatic language profiles manage separate voice, variant, rate, pitch, and spelling options for every enabled language, standard single-voice settings cannot represent all active profiles simultaneously. Presenting them separately protects both your normal voice preferences and your per-language configurations. Normal controls return immediately when automatic language profiles are disabled.

### Why do some voice package families show as "Not supported by the bundled engine"?
In Google TTS Voice Manager, certain voice packages (such as packages containing **Locomel** or **Lemonbalm**) are marked with the status **Not supported by the bundled engine** and cannot be used:

* **Designed for Mobile Hardware:** Packages like *Lemonbalm* and *Locomel* were developed for Android devices and rely on specialized on-device neural hardware accelerators (such as Google's Edge TPU / Tensor NPU, codenamed *Darwinn Rio* in Google Pixel devices). Standard desktop computers do not have these mobile neural chips.
* **WASM TTS Engine Capabilities:** The Chromium WebAssembly (WASM) TTS engine bundled with Google Chrome and this add-on is compiled specifically for desktop PC environments. While it fully supports Google's standard multi-speaker voices (`multi`), neural vocoder enhancement (`seanet`), and classical English packages (`afh`, `fis`), it deliberately omits the execution graphs and neural operators required by Locomel and Lemonbalm. In fact, Google's official Chrome TTS catalog only ships `multi` and `seanet` packages.
* **Proactive Protection:** To prevent synthesizer errors, silent speech, or browser crashes, Google TTS For NVDA identifies these incompatible package families in the catalog and marks them as unsupported in advance. They are kept in the catalog so that if a future upstream WASM engine update introduces software decoding for them, they can be enabled smoothly.

### Does Microsoft Edge require WebView2 Runtime?
Yes, but only when Microsoft Edge is the effective runtime. Google Chrome and Brave operate independently and do not require WebView2.

### Can Google TTS speak on the Windows sign-in screen or secure desktop?
Do not rely on it there. Google TTS For NVDA requires an interactive Windows user session to run its background browser process, which is restricted in secure desktop and pre-login environments.

### Under what license is Google TTS For NVDA released?
* **Add-on source code:** GNU General Public License version 2.0 (GPL-2.0), matching NVDA.
* **Google WASM TTS Engine:** BSD 3-Clause License (from the Chromium project).
* **Eigen math library:** Apache License 2.0.
* **Compact Language Detector 2 (CLD2):** Apache License 2.0.
* **websocket-client:** Apache License 2.0.
* **Unicode Character Database (UCD):** Unicode License V3 (Unicode-3.0).
* **Unicode Common Locale Data Repository (CLDR):** Unicode License V3 (Unicode-3.0).
* **Voice packages (`.zvoice`):** Downloaded directly on demand from Google's official public speech CDN; not distributed within the add-on package.

---

## Build Instructions (For Advanced Users)

To package the add-on yourself:

1. Clone this repository: `git clone https://github.com/nguyenanhduc09/Google-TTS-For-NVDA.git`
2. Ensure **Python** and **Node.js** are installed on your computer.
3. Run the automated build script:

```bat
build.bat
```

The build script reads the version from `googleTtsForNvda/manifest.ini`, verifies Python and JavaScript syntax, confirms no `.zvoice` packages are in the source tree, cleans cache folders, and packages the add-on into `dist/`.

---

## Community Contributions

We value pull requests from the community. A contribution can be accepted when the code runs reliably, fits the add-on's existing logic and interface, avoids introducing regressions in other add-on features, does not interfere with other NVDA add-ons or NVDA itself, does not weaken add-on security, and does not collect user data without permission. For development guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md). For translation workflows, see [TRANSLATING.md](TRANSLATING.md).

---

## Support & Contact

If you have questions, feedback, or need assistance, feel free to contact the development team:

* **[Nguyen Anh Duc](https://github.com/nguyenanhduc09):** [ducna1803@gmail.com](mailto:ducna1803@gmail.com) | Telegram: [t.me/anhduc1803](https://t.me/anhduc1803)
* **[Dao Duc Trung](https://github.com/daoductrung):** [trung@ddt.one](mailto:trung@ddt.one) | Telegram: [t.me/Daoductrung](https://t.me/Daoductrung)
* **[Pham Hung Vuong](https://github.com/phamhungvuong302):** [hungvuong106206@gmail.com](mailto:hungvuong106206@gmail.com) | Telegram: [t.me/phamhungvuong302](https://t.me/phamhungvuong302)
