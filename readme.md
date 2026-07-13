# Google TTS For NVDA

An NVDA screen reader synthesizer add-on that uses Google's WebAssembly (WASM) Text-to-Speech engine locally through a supported browser runtime (Microsoft Edge or Google Chrome) to provide high-quality, natural-sounding voices offline.

This project was created to make Google's high-quality local WebAssembly Text-to-Speech engine usable as a practical, everyday NVDA synthesizer on Windows computers.

*This add-on is co-developed by [Nguyen Anh Duc](https://github.com/nguyenanhduc09), [Dao Duc Trung](https://github.com/daoductrung) and [Pham Hung Vuong](https://github.com/phamhungvuong302).*

---

## Current Status

This add-on is currently being actively maintained and developed by Nguyen Anh Duc, Dao Duc Trung and Pham Hung Vuong. Version 0.3 significantly improves several everyday speech paths, though browser runtime, WASM, cache, and engine behavior can still affect the final result:
* Voice package startup is improved because the add-on prepares the currently selected package instead of broadly warming multiple packages.
* Long text and UI speech handling is improved with more careful background segmentation, so speech can often begin sooner while keeping spoken output more natural.
* Audio balance and harshness are improved across voice packages with dynamic gain control and limiting, reducing the chance of clipping or distorted sound.
* SeaNet voice packages use post-synthesis artificial rate processing at higher speeds to preserve quality better; this can increase CPU usage when reading quickly.

We highly welcome and appreciate any feedback from the community to help us improve!

---

## Features

* **Comprehensive Voice Support**: Supports all languages and voices available in WasmTtsEngine. This includes Chrome OS packages (optimized for frequent use and high-speed screen reading) and Google Natural packages (designed for higher quality, standard text reading).
* **100% Offline Speech**: Speech is rendered locally via a supported headless browser runtime (Microsoft Edge or Google Chrome).
* **Low Latency**: Uses current-package warm-up and advanced background text segmentation to improve speech responsiveness.
* **Volatile Audio Cache**: In-memory cache for short phrases (under 5000 characters) to optimize repeated announcements safely.
* **Sentence-Level Language Auto Detect**: Optionally switch voices sentence by sentence between selected installed languages. The add-on uses bundled CLD2 language detection, then applies its enabled language profiles and preferred language when text is unclear.
* **Voice Manager**: Easily browse, filter by language, download, or remove voice packages in batches using a multi-select checkbox interface. Also includes an **Open voice packages folder** button to inspect storage locations.
* **Background Operations**: Non-blocking downloads and removals on background threads.
* **Accessible Shortcut**: Press **`NVDA+Ctrl+Shift+G`** to open the Voice Manager instantly.
* **Browser Runtime Selection**: Choose between Microsoft Edge and Google Chrome as the underlying engine directly from the NVDA settings panel.

---

## Requirements

* **NVDA**: Version 2024.1 or newer. The add-on supports NVDA 2024 through 2026 on both 32-bit (x86) and 64-bit (x64) NVDA builds.
* **Browser runtime**: Microsoft Edge or Google Chrome must be present on the system. The add-on will search common paths or check your registry automatically. You can also specify a custom path using the `EDGE_PATH` or `CHROME_PATH` environment variable.
* **Interactive Windows user session**: The add-on depends on a background Microsoft Edge or Google Chrome runtime. Do not rely on it in environments where that browser runtime is unavailable or not allowed to start, such as the Windows sign-in screen, secure desktop contexts, Windows PE, recovery environments, or other minimal Windows sessions.

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
4. In Google TTS Voice Manager, you can use the **Filter by language** dropdown to quickly find voices for your language, check the boxes next to the voice packages you want, and click **Download checked voice packages**.

---

## Configuration Settings

### Synthesizer Settings

When automatic language detection is off, the synthesizer supports the standard NVDA Speech settings ring:

* **Voice**: Choose from your installed speaker/language voice packages.
* **Rate**: Speech rate. Non-SeaNet packages use the browser runtime rate path; SeaNet packages may use post-synthesis artificial rate processing at higher speeds.
* **Rate Boost**: Enable to double the computed speech rate for fast reading. High-speed SeaNet speech may use more CPU because the add-on processes generated audio after synthesis.
* **Pitch**: Speech pitch adjustment.
* **Volume**: Speech volume (maps to the browser runtime's 0.0 - 1.0 volume range).

### Browser Runtime Settings

The add-on includes a custom settings panel under **NVDA Settings (NVDA Menu -> Preferences -> Settings) -> Google TTS For NVDA**:
* **Browser runtime**: Select which browser runtime to use (Microsoft Edge or Google Chrome). The panel shows the availability status of each browser on your system.
* **Automatically detect language**: Enable sentence-level language detection and open the profile controls described below.

### Automatic Language Detection Profiles

When you enable **Automatically detect language**, the add-on uses its own per-language profiles instead of the normal NVDA Speech settings for detected sentences. This keeps your regular Google TTS voice settings unchanged for times when automatic language detection is off.

Automatic language detection uses bundled CLD2 detector libraries for both 32-bit (x86) and 64-bit (x64) NVDA builds. CLD2 results are accepted only when they are reliable enough for one of the enabled languages. If text is too short or unclear, the add-on uses conservative local language signals where available, then falls back to the preferred enabled language.

In the Google TTS For NVDA settings category:

1. Turn on **Automatically detect language between installed voices**.
2. Choose an **Auto-detect language profile**.
3. Check **Use this language in auto-detect** for each language you want the detector to consider. Language profiles are off until you check them.
4. For each enabled language, choose its voice and adjust rate, rate boost, pitch, and volume.
5. Choose the **Preferred auto-detect language** from the enabled languages. This language is used when a sentence is unclear or does not contain enough language clues.

Only enabled languages appear in the preferred language list. Rate, pitch, and volume use sliders like NVDA's Speech settings. The labels for voice, rate, rate boost, pitch, and volume follow NVDA's own translated setting names.

Automatic language detection marks the language before NVDA processes text, so NVDA's symbol pronunciation and speech dictionary processing stay in the normal speech pipeline for the selected language context.

When automatic language detection is off, NVDA voice dictionaries work normally for the currently selected Google TTS voice. When automatic language detection is on, the add-on temporarily uses the voice dictionary for each enabled language profile's selected voice while NVDA processes that segment. NVDA's default and temporary dictionaries still follow NVDA's normal behavior.

While automatic language detection is enabled, NVDA's Speech settings will not offer the normal voice, rate, rate boost, pitch, and volume controls for this synthesizer. Instead, it shows a focusable notice telling you to configure these values from **NVDA Settings -> Google TTS For NVDA**. Status messages in the Google TTS For NVDA settings category are also reachable with Tab so screen readers can announce them. Turn automatic language detection off to use the normal NVDA Speech settings again.

---

## Troubleshooting Edge Runtime Silence

If you choose **Microsoft Edge** as the browser runtime and Google TTS For NVDA stays silent even though Microsoft Edge is installed, install or repair the Microsoft Edge WebView2 Runtime.

WebView2 is the Microsoft Edge runtime used by native Windows applications to host web content. It is related to Microsoft Edge, but having the normal Edge browser available does not always mean the WebView2 Runtime is installed and healthy for app scenarios. Microsoft recommends the Evergreen WebView2 Runtime for applications because it is shared, automatically updated, and the small Evergreen Bootstrapper downloads the matching runtime for the device architecture.

For an online computer, download the Microsoft Edge WebView2 Evergreen Bootstrapper here:

<https://go.microsoft.com/fwlink/p/?LinkId=2124703>

Run the installer, restart NVDA, then select **Google TTS For NVDA** again. For offline installers or a fixed-version WebView2 Runtime package, open Microsoft's WebView2 page:

<https://developer.microsoft.com/microsoft-edge/webview2>

---

## Build Instructions (For Advanced Users)

To package the add-on yourself:

1. Clone this repository using `git clone https://github.com/nguyenanhduc09/Google-TTS-For-NVDA.git` and navigate to the directory.
2. Make sure you have **Python** and **Node.js** installed on your system.
3. Run the automated build script:

```bat
build.bat
```

The build script reads the version from `googleTtsForNvda/manifest.ini`, builds all add-on locales non-interactively, checks Python and JavaScript syntax, verifies that no `.zvoice` voice packages are inside the source tree, removes generated `__pycache__` folders, and packages the add-on.

The verified `.nvda-addon` package will be created in the `dist/` directory, with a name like:

```text
dist/googleTtsForNvda-0.3.nvda-addon
```

---

## Translation

We warmly welcome translations for new languages or updates to existing ones!

If you would like to translate this add-on into your local language:
* Read the detailed translation guide in [TRANSLATING.md](TRANSLATING.md) to understand the layout, workflow, and how to use translation tools such as Poedit.
* Use the helper script `build_i18n.py` to validate or build your translation files:
  * Running `python build_i18n.py` opens an interactive menu to guide you.
  * Running `python build_i18n.py --check --all-languages` validates all existing translations.
  * Running `python build_i18n.py --all-languages` compiles and updates translation files for all locales.

---

## Contributing

We strongly welcome contributions from other developers! If you have ideas, bug fixes, or improvements, please feel free to open an issue or submit a pull request.

---

## Contact

If you have any questions, feedback, or need support, feel free to reach out to us via email or Telegram:
* **Nguyen Anh Duc**: [ducna1803@gmail.com](mailto:ducna1803@gmail.com) | Telegram: [t.me/anhduc1803](https://t.me/anhduc1803)
* **Dao Duc Trung**: [trung@ddt.one](mailto:trung@ddt.one) | Telegram: [t.me/Daoductrung](https://t.me/Daoductrung)
* **Pham Hung Vuong**: [hungvuong106206@gmail.com](mailto:hungvuong106206@gmail.com) | Telegram: [t.me/phamhungvuong302](https://t.me/phamhungvuong302)
