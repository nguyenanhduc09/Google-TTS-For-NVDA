from __future__ import annotations

import contextlib
import functools
import inspect
import os
import threading
import unicodedata
import webbrowser
from typing import Any

import addonHandler
import config
import globalPluginHandler
import gui
import speech.extensions
import speech.shortcutKeys as shortcutKeysModule
import speechDictHandler
import synthDriverHandler
import wx
from autoSettingsUtils.driverSetting import BooleanDriverSetting, NumericDriverSetting
from gui import guiHelper
from logHandler import log
from speech import speech as speechModule

try:
    from speech import languageHandling as languageHandlingModule
except ImportError:
    languageHandlingModule = None  # type: ignore[assignment]
try:
    from speech import manager as speechManagerModule
except ImportError:
    speechManagerModule = None  # type: ignore[assignment]
try:
    from speech import sayAll as sayAllModule
except ImportError:
    sayAllModule = None  # type: ignore[assignment]
try:
    from speech.commands import CharacterModeCommand, EndUtteranceCommand, LangChangeCommand
except ImportError:
    from speech.commands import LangChangeCommand  # type: ignore[assignment]

    CharacterModeCommand = None  # type: ignore[misc,assignment]
    EndUtteranceCommand = None  # type: ignore[misc,assignment]
try:
    from speech.commands import SuppressUnicodeNormalizationCommand
except ImportError:
    SuppressUnicodeNormalizationCommand = None  # type: ignore[misc,assignment]
from synthDrivers.googleTtsForNvda import (
    audio_math,
    language_detector,
    language_profiles,
    language_utils,
    standby,
    voice_store,
)
from synthDrivers.googleTtsForNvda.bridge import (
    CONFIG_AUTO_LANGUAGE_CANDIDATES as CONFIG_AUTO_LANGUAGE_CANDIDATES,
)
from synthDrivers.googleTtsForNvda.bridge import (
    CONFIG_AUTO_LANGUAGE_DETECTION as CONFIG_AUTO_LANGUAGE_DETECTION,
)
from synthDrivers.googleTtsForNvda.bridge import (
    CONFIG_AUTO_LANGUAGE_PREFERRED as CONFIG_AUTO_LANGUAGE_PREFERRED,
)
from synthDrivers.googleTtsForNvda.bridge import (
    CONFIG_AUTO_LANGUAGE_PROFILES as CONFIG_AUTO_LANGUAGE_PROFILES,
)
from synthDrivers.googleTtsForNvda.bridge import (
    CONFIG_BROWSER_RUNTIME as CONFIG_BROWSER_RUNTIME,
)
from synthDrivers.googleTtsForNvda.bridge import (
    CONFIG_KEEP_BROWSER_RUNTIME_READY as CONFIG_KEEP_BROWSER_RUNTIME_READY,
)
from synthDrivers.googleTtsForNvda.bridge import (
    CONFIG_SECTION as CONFIG_SECTION,
)
from synthDrivers.googleTtsForNvda.bridge import (
    DEFAULT_AUTO_LANGUAGE_CANDIDATES as DEFAULT_AUTO_LANGUAGE_CANDIDATES,
)
from synthDrivers.googleTtsForNvda.bridge import (
    DEFAULT_AUTO_LANGUAGE_DETECTION as DEFAULT_AUTO_LANGUAGE_DETECTION,
)
from synthDrivers.googleTtsForNvda.bridge import (
    DEFAULT_AUTO_LANGUAGE_PREFERRED as DEFAULT_AUTO_LANGUAGE_PREFERRED,
)
from synthDrivers.googleTtsForNvda.bridge import (
    DEFAULT_AUTO_LANGUAGE_PROFILES as DEFAULT_AUTO_LANGUAGE_PROFILES,
)
from synthDrivers.googleTtsForNvda.bridge import (
    DEFAULT_BROWSER_RUNTIME as DEFAULT_BROWSER_RUNTIME,
)
from synthDrivers.googleTtsForNvda.bridge import (
    DEFAULT_KEEP_BROWSER_RUNTIME_READY as DEFAULT_KEEP_BROWSER_RUNTIME_READY,
)
from synthDrivers.googleTtsForNvda.bridge import (
    SYNTH_NAME,
    edge_webview2_blocks_effective_runtime,
    ensure_config_spec,
)
from synthDrivers.googleTtsForNvda.catalog import EngineLibraryError, VoiceCatalog, engine_library_error_message

from . import settings as settingsModule
from . import updateGui, updater
from .settings import GoogleTtsSettingsPanel
from .uiUtils import (
    _from_dip,
    bind_read_only_text_focus_announcement,
    dialog_parent,
    is_secure_mode,
    post_popup,
    pre_popup,
    show_message_box,
    show_runtime_error_dialog,
    window_is_alive,
)
from .uiUtils import (
    call_compat as _call_compat,
)
from .voiceManager import VoiceManagerDialog

addonHandler.initTranslation()
ensure_config_spec()

_AUTO_LANGUAGE_NOTICE_ID = "notice"
_dialog: VoiceManagerDialog | None = None
_originalSetSynth: Any | None = None
_originalSettingsDialogSetSynth: Any | None = None
_originalAutoSettingsGetSettingMaker: Any | None = None
_originalAutoSettingsUpdateValueForControl: Any | None = None
_originalAutoSettingsOnDiscard: Any | None = None
_originalAutoSettingsRefreshGui: Any | None = None
_originalVoiceSettingsMakeSettings: Any | None = None
_originalSpeechSpeak: Any | None = None
_originalSpeechPackageSpeak: Any | None = None
_originalSayAllSpeak: Any | None = None
_originalSayAllSpeakWithoutPauses: Any | None = None
_originalSayAllInitialize: Any | None = None
_originalSpeechProcessText: Any | None = None
_originalSpeechPackageProcessText: Any | None = None
_originalSpeechGetSpellingSpeech: Any | None = None
_originalSpeechPackageGetSpellingSpeech: Any | None = None
_originalSpeechGetSingleCharDescription: Any | None = None
_originalSpeechPackageGetSingleCharDescription: Any | None = None
_originalShortcutKeysShouldUseSpellingFunctionality: Any | None = None
_originalLanguageHandlingShouldMakeLangChangeCommand: Any | None = None
_originalLanguageHandlingShouldSwitchVoice: Any | None = None
_originalSpeechManagerShouldSwitchVoice: Any | None = None
_originalSpeechDictLoadVoiceDict: Any | None = None
_originalSpeechDictDefinitionsLoadVoiceDict: Any | None = None
_originalPopupSettingsDialog: Any | None = None
_patchedAutoSettingsGetSettingMaker: Any | None = None
_patchedAutoSettingsUpdateValueForControl: Any | None = None
_patchedAutoSettingsOnDiscard: Any | None = None
_patchedAutoSettingsRefreshGui: Any | None = None
_patchedVoiceSettingsMakeSettings: Any | None = None
_patchedSpeechSpeak: Any | None = None
_patchedSpeechPackageSpeak: Any | None = None
_patchedSayAllSpeak: Any | None = None
_patchedSayAllSpeakWithoutPauses: Any | None = None
_patchedSayAllInitialize: Any | None = None
_patchedSpeechProcessText: Any | None = None
_patchedSpeechGetSpellingSpeech: Any | None = None
_patchedSpeechGetSingleCharDescription: Any | None = None
_patchedShortcutKeysShouldUseSpellingFunctionality: Any | None = None
_patchedLanguageHandlingShouldMakeLangChangeCommand: Any | None = None
_patchedLanguageHandlingShouldSwitchVoice: Any | None = None
_patchedSpeechManagerShouldSwitchVoice: Any | None = None
_patchedSpeechDictLoadVoiceDict: Any | None = None
_patchedPopupSettingsDialog: Any | None = None
_autoLanguageSpeechFilterRegistered = False
_GOOGLE_TTS_LANG_CHANGE_ATTR = language_detector.GOOGLE_TTS_LANG_CHANGE_ATTR
_GOOGLE_TTS_SPELLING_LANG_ATTR = "googleTtsForNvdaSpellingLanguage"
_MISSING_GOOGLE_TTS_LANGUAGE = language_detector.MISSING_GOOGLE_TTS_LANGUAGE
_GoogleTtsProfileLangCommand = language_detector.GoogleTtsProfileLangCommand
_missingVoicesPromptActive = False
_edgeWebView2PromptActive = False
_voiceDictLock = threading.RLock()
_speechConfigOverlayLock = threading.RLock()
_autoProfileSpeakDepth = 0
_speakPipelineState = threading.local()
_EDGE_WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
_EDGE_WEBVIEW2_DOWNLOAD_PAGE_URL = "https://developer.microsoft.com/microsoft-edge/webview2"
_UNSET = object()
_SET_SYNTH_WRAPPER_METADATA: dict[str, Any] | None = None
_retainedSetSynth: Any | None = None
_synthSelectionPatchActive = False
_readOnlyTextSettingPatchActive: list[bool] | None = None
_voiceDictionaryDialogPatchActive: list[bool] | None = None
_voiceDictionaryLoadingPatchActive: list[bool] | None = None
_autoLanguagePatchActive: list[bool] | None = None


def _wrap_nvda_callable(wrapper: Any, original: Any) -> Any:
    """Copy NVDA callable metadata and __wrapped__ onto a monkeypatch wrapper."""
    if wrapper is None or original is None:
        return wrapper
    with contextlib.suppress(Exception):
        functools.wraps(original)(wrapper)
    return wrapper


def _extract_named_arg(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    name: str,
    posIndex: int,
    default: Any = None,
) -> Any:
    if name in kwargs:
        return kwargs[name]
    if len(args) > posIndex:
        return args[posIndex]
    return default


def _replace_text_locale(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    textIndex: int = 0,
    localeIndex: int = 1,
    text: Any = _UNSET,
    locale: Any = _UNSET,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    newArgs = list(args)
    newKwargs = dict(kwargs)
    if text is not _UNSET:
        if "text" in newKwargs:
            newKwargs["text"] = text
        elif len(newArgs) > textIndex:
            newArgs[textIndex] = text
        else:
            newKwargs["text"] = text
    if locale is not _UNSET:
        if "locale" in newKwargs:
            newKwargs["locale"] = locale
        elif len(newArgs) > localeIndex:
            newArgs[localeIndex] = locale
        else:
            newKwargs["locale"] = locale
    return tuple(newArgs), newKwargs


def _extract_set_synth_invocation(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str | None, bool, list[str] | None, tuple[Any, ...], dict[str, Any], bool, bool]:
    remainingKwargs = dict(kwargs)
    if args:
        name = args[0]
    elif "name" in remainingKwargs:
        name = remainingKwargs.pop("name")
    elif "synthName" in remainingKwargs:
        name = remainingKwargs.pop("synthName")
    else:
        raise TypeError("setSynth() missing required argument: 'name'")
    explicitIsFallback = False
    isFallback = False
    if len(args) >= 2:
        if "isFallback" in remainingKwargs:
            raise TypeError("setSynth() got multiple values for argument 'isFallback'")
        isFallback = bool(args[1])
        explicitIsFallback = True
    elif "isFallback" in remainingKwargs:
        isFallback = bool(remainingKwargs.pop("isFallback"))
        explicitIsFallback = True
    explicitLeftToTry = False
    _leftToTry = None
    if len(args) >= 3:
        if "_leftToTry" in remainingKwargs:
            raise TypeError("setSynth() got multiple values for argument '_leftToTry'")
        _leftToTry = args[2]
        explicitLeftToTry = True
    elif "_leftToTry" in remainingKwargs:
        _leftToTry = remainingKwargs.pop("_leftToTry")
        explicitLeftToTry = True
    extraArgs = args[3:]
    return name, isFallback, _leftToTry, extraArgs, remainingKwargs, explicitIsFallback, explicitLeftToTry


def _call_set_synth_compat(
    setSynth: Any,
    name: str | None,
    isFallback: bool = False,
    _leftToTry: list[str] | None = None,
    extraArgs: tuple[Any, ...] = (),
    extraKwargs: dict[str, Any] | None = None,
    *,
    explicitIsFallback: bool = True,
    explicitLeftToTry: bool = False,
) -> bool:
    extraKwargs = dict(extraKwargs or {})
    try:
        signature = inspect.signature(setSynth, follow_wrapped=False)
    except (TypeError, ValueError):
        try:
            if explicitLeftToTry:
                return bool(setSynth(name, isFallback, _leftToTry, *extraArgs, **extraKwargs))
            if explicitIsFallback:
                return bool(setSynth(name, isFallback, *extraArgs, **extraKwargs))
            return bool(setSynth(name, *extraArgs, **extraKwargs))
        except TypeError as exc:
            message = str(exc)
            if "_leftToTry" in message or "positional argument" in message:
                try:
                    return bool(setSynth(name, isFallback))
                except TypeError:
                    return bool(setSynth(name))
            raise

    parameters = signature.parameters
    acceptsVarargs = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values())
    acceptsKwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    positionalArgs: list[Any] = [name]
    kwargs: dict[str, Any] = {}
    for parameterName, value, shouldForwardIfVar in (
        ("isFallback", isFallback, explicitIsFallback or "isFallback" in parameters),
        ("_leftToTry", _leftToTry, explicitLeftToTry or "_leftToTry" in parameters),
    ):
        parameter = parameters.get(parameterName)
        if parameter is not None:
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                positionalArgs.append(value)
            else:
                kwargs[parameterName] = value
        elif acceptsKwargs and shouldForwardIfVar:
            kwargs[parameterName] = value
        elif acceptsVarargs and shouldForwardIfVar and not acceptsKwargs:
            positionalArgs.append(value)

    if acceptsVarargs and extraArgs:
        positionalArgs.extend(extraArgs)
    for key, value in extraKwargs.items():
        parameter = parameters.get(key)
        if acceptsKwargs or (
            parameter is not None
            and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        ):
            kwargs[key] = value
    try:
        return bool(setSynth(*positionalArgs, **kwargs))
    except TypeError:
        if extraKwargs and acceptsKwargs:
            filteredKwargs = {key: value for key, value in kwargs.items() if key not in extraKwargs}
            return bool(setSynth(*positionalArgs, **filteredKwargs))
        raise


def _normalize_set_synth_args(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str | None, bool, list[str] | None]:
    name, isFallback, _leftToTry, _extraArgs, _extraKwargs, _expFallback, _expLeft = _extract_set_synth_invocation(
        args,
        kwargs,
    )
    return name, isFallback, _leftToTry


def _clear_dialog_reference(dialog: VoiceManagerDialog) -> None:
    global _dialog
    if _dialog is dialog:
        _dialog = None


def _open_voice_manager(initialPage: str = "installed") -> None:
    global _dialog
    if is_secure_mode():
        return
    if _dialog is not None:
        if not window_is_alive(_dialog):
            _dialog = None
        else:
            try:
                if _dialog.IsShown():
                    if initialPage == "download":
                        _dialog.show_download_tab()
                    _dialog.Raise()
                    _dialog.focus_default_control()
                    return
            except RuntimeError:
                _dialog = None
    parent = dialog_parent()
    pre_popup()
    try:
        try:
            _dialog = VoiceManagerDialog(parent, _clear_dialog_reference, initialPage=initialPage)
        except EngineLibraryError as exc:
            _show_engine_library_error(exc)
            return
        _dialog.Show()
    finally:
        post_popup()


def open_voice_manager_download_tab() -> None:
    _open_voice_manager("download")


def _google_tts_voice_status() -> str:
    try:
        fullCatalog = VoiceCatalog.load()
        installedPackages = voice_store.installed_packages(fullCatalog)
        if not installedPackages:
            return "missing"
        if not VoiceCatalog(installedPackages).speakers:
            return "unusable"
        return "ready"
    except EngineLibraryError:
        raise
    except Exception:
        log.exception("Could not check installed Google TTS voice packages.", exc_info=True)
        return "missing"


def _show_engine_library_error(error: EngineLibraryError) -> None:
    log.error("Google TTS WASM TTS Engine error: %s", error.technicalDetail)
    show_runtime_error_dialog(engine_library_error_message(error))


def _show_missing_voices_prompt(message: str | None = None) -> None:
    global _missingVoicesPromptActive
    if _missingVoicesPromptActive or is_secure_mode():
        return
    _missingVoicesPromptActive = True
    try:
        answer = show_message_box(
            message
            or _(
                "No Google TTS For NVDA voices are installed.\n\n"
                "Press OK to open Google TTS Voice Manager and download a voice package.\n"
                "Press Cancel to keep using your current synthesizer for now.\n\n"
                "You can also open Voice Manager later from NVDA Menu > Tools > "
                "Google TTS Voice Manager, or press NVDA+Ctrl+Shift+G."
            ),
            _("Google TTS For NVDA"),
            wx.OK | wx.CANCEL | wx.ICON_INFORMATION,
            dialog_parent(),
        )
        if answer == wx.OK or answer == getattr(wx, "ID_OK", wx.OK):
            open_voice_manager_download_tab()
    finally:
        _missingVoicesPromptActive = False


def _open_web_url(url: str) -> None:
    try:
        webbrowser.open(url, new=2)
    except Exception:
        log.exception("Could not open URL for Microsoft Edge WebView2 Runtime.", exc_info=True)
        _show_manual_web_url_dialog(url)


def _copy_text_to_clipboard(text: str) -> bool:
    if not wx.TheClipboard.Open():
        return False
    try:
        wx.TheClipboard.SetData(wx.TextDataObject(text))
        wx.TheClipboard.Flush()
        return True
    finally:
        wx.TheClipboard.Close()


def _show_manual_web_url_dialog(url: str) -> None:
    if is_secure_mode():
        return
    parent = dialog_parent()
    pre_popup()
    try:
        dialog = wx.Dialog(parent, title=_("Google TTS For NVDA"))
        try:
            mainSizer = wx.BoxSizer(wx.VERTICAL)
            helper = guiHelper.BoxSizerHelper(dialog, sizer=mainSizer)
            message = wx.StaticText(
                dialog,
                label=_("The download page could not be opened. Please visit this address manually:"),
            )
            message.Wrap(_from_dip(dialog, 520))
            helper.addItem(message, flag=wx.EXPAND)
            urlControl = helper.addLabeledControl(
                _("Download address") + ":",
                wx.TextCtrl,
                value=url,
                style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
            )
            urlControl.SetName(_("Download address"))
            bind_read_only_text_focus_announcement(urlControl, minLines=2, maxLines=5)
            buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
            copyButton = wx.Button(dialog, label=_("&Copy link"))
            closeButton = wx.Button(dialog, id=wx.ID_OK, label=_("&Close"))
            buttonSizer.Add(copyButton, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.RIGHT)
            buttonSizer.Add(closeButton)
            mainSizer.Add(buttonSizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.ALIGN_RIGHT)
            dialog.SetSizerAndFit(mainSizer)
            dialog.SetEscapeId(wx.ID_OK)
            closeButton.SetDefault()

            def on_copy(evt: wx.CommandEvent) -> None:
                if _copy_text_to_clipboard(url):
                    show_message_box(
                        _("The link was copied to the clipboard."),
                        _("Google TTS For NVDA"),
                        wx.OK | wx.ICON_INFORMATION,
                        dialog,
                    )
                else:
                    show_message_box(
                        _("The link could not be copied to the clipboard."),
                        _("Google TTS For NVDA"),
                        wx.OK | wx.ICON_ERROR,
                        dialog,
                    )

            copyButton.Bind(wx.EVT_BUTTON, on_copy)
            closeButton.Bind(wx.EVT_BUTTON, lambda evt: dialog.EndModal(wx.ID_OK))
            dialog.ShowModal()
        finally:
            dialog.Destroy()
    finally:
        post_popup()


def show_edge_webview2_prompt() -> None:
    global _edgeWebView2PromptActive
    if _edgeWebView2PromptActive or is_secure_mode():
        return
    _edgeWebView2PromptActive = True
    try:
        parent = dialog_parent()
        pre_popup()
        result = wx.ID_CANCEL
        try:
            dialog = wx.Dialog(parent, title=_("Microsoft Edge WebView2 Runtime is needed"))
            try:
                mainSizer = wx.BoxSizer(wx.VERTICAL)
                message = wx.StaticText(
                    dialog,
                    label=_(
                        "Microsoft Edge is available, but Microsoft Edge WebView2 Runtime is not available.\n\n"
                        "Google TTS For NVDA needs WebView2 when Microsoft Edge is the Chromium browser runtime. "
                        "NVDA will keep using the previous synthesizer until WebView2 is installed.\n\n"
                        "Choose Download online installer to download Microsoft's Evergreen Bootstrapper. "
                        "Choose Open Microsoft WebView2 page if you need an offline installer or a fixed-version package."
                    ),
                )
                message.Wrap(_from_dip(dialog, 520))
                mainSizer.Add(message, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND)
                buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
                downloadButton = wx.Button(dialog, label=_("&Download online installer"))
                webPageButton = wx.Button(dialog, label=_("&Open Microsoft WebView2 page"))
                laterButton = wx.Button(dialog, id=wx.ID_CANCEL, label=_("&Maybe later"))
                buttonSizer.Add(downloadButton, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.RIGHT)
                buttonSizer.Add(webPageButton, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.RIGHT)
                buttonSizer.Add(laterButton)
                mainSizer.Add(buttonSizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.ALIGN_RIGHT)
                dialog.SetSizerAndFit(mainSizer)
                dialog.SetEscapeId(wx.ID_CANCEL)
                downloadButton.SetDefault()
                downloadButton.Bind(wx.EVT_BUTTON, lambda evt: dialog.EndModal(wx.ID_YES))
                webPageButton.Bind(wx.EVT_BUTTON, lambda evt: dialog.EndModal(wx.ID_NO))
                laterButton.Bind(wx.EVT_BUTTON, lambda evt: dialog.EndModal(wx.ID_CANCEL))
                result = dialog.ShowModal()
            finally:
                dialog.Destroy()
        finally:
            post_popup()
        if result == wx.ID_YES:
            _open_web_url(_EDGE_WEBVIEW2_BOOTSTRAPPER_URL)
        elif result == wx.ID_NO:
            _open_web_url(_EDGE_WEBVIEW2_DOWNLOAD_PAGE_URL)
    finally:
        _edgeWebView2PromptActive = False


def _set_synth_with_google_tts_voice_prompt(
    *args: Any,
    **kwargs: Any,
) -> bool:
    name, isFallback, _leftToTry, extraArgs, extraKwargs, explicitIsFallback, explicitLeftToTry = (
        _extract_set_synth_invocation(args, kwargs)
    )
    # Keep the current synthesizer active so NVDA can speak the prompt instead of
    # showing its generic "could not load synthesizer" error first.
    if _synthSelectionPatchActive and name == SYNTH_NAME and not isFallback:
        if edge_webview2_blocks_effective_runtime():
            wx.CallAfter(show_edge_webview2_prompt)
            return True
        try:
            voiceStatus = _google_tts_voice_status()
        except EngineLibraryError as exc:
            wx.CallAfter(_show_engine_library_error, exc)
            return True
        if voiceStatus != "ready":
            message = None
            if voiceStatus == "unusable":
                message = _(
                    "No installed Google TTS For NVDA voices can be used.\n\n"
                    "Press OK to open Google TTS Voice Manager and install another voice package.\n"
                    "Press Cancel to keep using your current synthesizer for now."
                )
            wx.CallAfter(_show_missing_voices_prompt, message)
            return True
    originalSetSynth = _originalSetSynth if _synthSelectionPatchActive else _retainedSetSynth
    if originalSetSynth is None:
        return False
    return _call_set_synth_compat(
        originalSetSynth,
        name,
        isFallback,
        _leftToTry,
        extraArgs,
        extraKwargs,
        explicitIsFallback=explicitIsFallback,
        explicitLeftToTry=explicitLeftToTry,
    )


def _callable_contains_target(func: Any, target: Any) -> bool:
    seen: set[int] = set()
    current = func
    while callable(current) and id(current) not in seen:
        if current is target:
            return True
        seen.add(id(current))
        current = getattr(current, "__wrapped__", None)
    return False


def _unwrap_self_reference(func: Any, target: Any) -> Any:
    current = func
    while current is target:
        unwrapped = getattr(current, "__wrapped__", None)
        if not callable(unwrapped) or unwrapped is current:
            return None
        current = unwrapped
    return current


def _patch_synth_selection() -> None:
    global _originalSetSynth, _originalSettingsDialogSetSynth, _SET_SYNTH_WRAPPER_METADATA
    global _retainedSetSynth, _synthSelectionPatchActive
    if _originalSetSynth is not None:
        return
    currentSetSynth = synthDriverHandler.setSynth
    if _retainedSetSynth is not None and _callable_contains_target(
        currentSetSynth, _set_synth_with_google_tts_voice_prompt
    ):
        _synthSelectionPatchActive = True
        _originalSetSynth = _retainedSetSynth
        _retainedSetSynth = None
        settingsDialogs = getattr(gui, "settingsDialogs", None)
        if settingsDialogs is not None and hasattr(settingsDialogs, "setSynth"):
            dialogSetSynth = settingsDialogs.setSynth
            if not _callable_contains_target(dialogSetSynth, _set_synth_with_google_tts_voice_prompt):
                _originalSettingsDialogSetSynth = dialogSetSynth
                settingsDialogs.setSynth = _set_synth_with_google_tts_voice_prompt
        return
    originalSetSynth = _unwrap_self_reference(currentSetSynth, _set_synth_with_google_tts_voice_prompt)
    if originalSetSynth is None:
        return
    _synthSelectionPatchActive = True
    _retainedSetSynth = None
    _originalSetSynth = originalSetSynth
    if _SET_SYNTH_WRAPPER_METADATA is None:
        _SET_SYNTH_WRAPPER_METADATA = {
            "__module__": _set_synth_with_google_tts_voice_prompt.__module__,
            "__name__": _set_synth_with_google_tts_voice_prompt.__name__,
            "__qualname__": _set_synth_with_google_tts_voice_prompt.__qualname__,
            "__doc__": _set_synth_with_google_tts_voice_prompt.__doc__,
            "__annotations__": dict(getattr(_set_synth_with_google_tts_voice_prompt, "__annotations__", {})),
        }
    _wrap_nvda_callable(_set_synth_with_google_tts_voice_prompt, _originalSetSynth)
    synthDriverHandler.setSynth = _set_synth_with_google_tts_voice_prompt
    settingsDialogs = getattr(gui, "settingsDialogs", None)
    if settingsDialogs is not None and hasattr(settingsDialogs, "setSynth"):
        dialogSetSynth = _unwrap_self_reference(settingsDialogs.setSynth, _set_synth_with_google_tts_voice_prompt)
        _originalSettingsDialogSetSynth = dialogSetSynth if dialogSetSynth is not None else originalSetSynth
        settingsDialogs.setSynth = _set_synth_with_google_tts_voice_prompt


def _unpatch_synth_selection() -> None:
    global _originalSetSynth, _originalSettingsDialogSetSynth, _SET_SYNTH_WRAPPER_METADATA
    global _retainedSetSynth, _synthSelectionPatchActive
    if _originalSetSynth is None:
        return
    _synthSelectionPatchActive = False
    stillReferenced = False
    if getattr(synthDriverHandler, "setSynth", None) is _set_synth_with_google_tts_voice_prompt:
        synthDriverHandler.setSynth = _originalSetSynth
    else:
        stillReferenced = True
    settingsDialogs = getattr(gui, "settingsDialogs", None)
    if settingsDialogs is not None and _originalSettingsDialogSetSynth is not None:
        if getattr(settingsDialogs, "setSynth", None) is _set_synth_with_google_tts_voice_prompt:
            settingsDialogs.setSynth = _originalSettingsDialogSetSynth
        else:
            stillReferenced = True
    if stillReferenced:
        _retainedSetSynth = _originalSetSynth
    else:
        _retainedSetSynth = None
        if _SET_SYNTH_WRAPPER_METADATA is not None:
            for attrName, attrValue in _SET_SYNTH_WRAPPER_METADATA.items():
                with contextlib.suppress(Exception):
                    setattr(_set_synth_with_google_tts_voice_prompt, attrName, attrValue)
            with contextlib.suppress(Exception):
                delattr(_set_synth_with_google_tts_voice_prompt, "__wrapped__")
            _SET_SYNTH_WRAPPER_METADATA = None
    _originalSetSynth = None
    _originalSettingsDialogSetSynth = None


def _make_read_only_text_setting_control(self: Any, setting: Any, settingsStorage: Any) -> wx.BoxSizer:
    labelText = f"{getattr(setting, 'displayNameWithAccelerator', getattr(setting, 'displayName', setting.id))}:"
    value = str(getattr(settingsStorage, setting.id, "") or "")
    labeledControl = guiHelper.LabeledControlHelper(
        self,
        labelText,
        wx.TextCtrl,
        value=value,
        style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
    )
    edit = labeledControl.control
    edit.SetName(str(getattr(setting, "displayName", setting.id)))
    bind_read_only_text_focus_announcement(edit, minLines=2, maxLines=5)
    setattr(self, f"{setting.id}Edit", edit)
    setattr(self, f"{setting.id}List", edit)
    try:
        self.bindHelpEvent(self._getSettingControlHelpId(setting.id), edit)
    except Exception:
        log.debug("Could not bind help for Google TTS read-only speech setting.", exc_info=True)
    lastControl = getattr(self, "lastControl", None)
    if lastControl is not None:
        edit.MoveAfterInTabOrder(lastControl)
    self.lastControl = edit
    return labeledControl.sizer


def _is_google_tts_read_only_setting(setting: Any, settingsStorage: Any | None = None) -> bool:
    if getattr(setting, "id", "") != _AUTO_LANGUAGE_NOTICE_ID:
        return False
    if not getattr(setting, "readOnlyText", False):
        return False
    return not (settingsStorage is not None and getattr(settingsStorage, "name", "") != SYNTH_NAME)


def _hide_google_tts_auto_profile_speech_controls(panel: Any) -> None:
    if not window_is_alive(panel):
        return
    driver = getattr(panel, "driver", None)
    if driver is None and callable(getattr(panel, "getSettings", None)):
        with contextlib.suppress(Exception):
            driver = panel.getSettings()
    isGoogleTts = driver is not None and getattr(driver, "name", "") == SYNTH_NAME
    hideControls = isGoogleTts and _google_auto_language_detection_active()
    previouslyHidden = bool(getattr(panel, "_googleTtsHidAutoProfileSpeechControls", False))
    if not hideControls and not previouslyHidden:
        return
    for controlName in (
        "capPitchChangeEdit",
        "sayCapForCapsCheckBox",
        "beepForCapsCheckBox",
        "useSpellingFunctionalityCheckBox",
    ):
        control = getattr(panel, controlName, None)
        if control is None or not window_is_alive(control):
            continue
        try:
            if controlName == "capPitchChangeEdit":
                sizer = control.GetContainingSizer()
                if sizer is not None:
                    sizer.ShowItems(not hideControls)
                    control.Enable(not hideControls)
                    continue
            if hideControls:
                control.Hide()
                control.Disable()
            else:
                control.Show()
                control.Enable()
        except Exception:
            log.debug("Could not hide Google TTS auto-profile speech setting.", exc_info=True)
    panel._googleTtsHidAutoProfileSpeechControls = hideControls
    try:
        panel.Layout()
        parent = panel.GetParent()
        if parent is not None and window_is_alive(parent):
            parent.Layout()
    except Exception:
        log.debug("Could not relayout Google TTS Speech Settings controls.", exc_info=True)


def _patch_read_only_text_setting() -> None:
    global \
        _originalAutoSettingsGetSettingMaker, \
        _originalAutoSettingsUpdateValueForControl, \
        _originalAutoSettingsOnDiscard
    global _originalAutoSettingsRefreshGui
    global _originalVoiceSettingsMakeSettings
    global _patchedAutoSettingsGetSettingMaker, _patchedAutoSettingsUpdateValueForControl, _patchedAutoSettingsOnDiscard
    global _patchedAutoSettingsRefreshGui
    global _patchedVoiceSettingsMakeSettings
    global _readOnlyTextSettingPatchActive
    if _originalAutoSettingsGetSettingMaker is not None:
        return
    autoSettingsMixin = getattr(gui.settingsDialogs, "AutoSettingsMixin", None)
    if autoSettingsMixin is None:
        return
    voiceSettingsPanel = getattr(gui.settingsDialogs, "VoiceSettingsPanel", None)
    _originalAutoSettingsGetSettingMaker = autoSettingsMixin._getSettingMaker
    _originalAutoSettingsUpdateValueForControl = autoSettingsMixin._updateValueForControl
    _originalAutoSettingsOnDiscard = autoSettingsMixin.onDiscard
    _originalAutoSettingsRefreshGui = autoSettingsMixin.refreshGui
    originalGetSettingMaker = _originalAutoSettingsGetSettingMaker
    originalUpdateValueForControl = _originalAutoSettingsUpdateValueForControl
    originalOnDiscard = _originalAutoSettingsOnDiscard
    originalRefreshGui = _originalAutoSettingsRefreshGui
    if voiceSettingsPanel is not None:
        _originalVoiceSettingsMakeSettings = voiceSettingsPanel.makeSettings
        originalVoiceMakeSettings = _originalVoiceSettingsMakeSettings
    else:
        originalVoiceMakeSettings = None
    patchActive = [True]
    _readOnlyTextSettingPatchActive = patchActive

    def _get_setting_maker(self: Any, setting: Any, *args: Any, **kwargs: Any) -> Any:
        if patchActive[0] and _is_google_tts_read_only_setting(setting):

            def _make_control(
                setting: Any,
                settingsStorage: Any,
                *controlArgs: Any,
                **controlKwargs: Any,
            ) -> wx.BoxSizer:
                if not patchActive[0] or not _is_google_tts_read_only_setting(setting, settingsStorage):
                    maker = _call_compat(originalGetSettingMaker, self, setting, *args, **kwargs)
                    return _call_compat(maker, setting, settingsStorage, *controlArgs, **controlKwargs)
                return _make_read_only_text_setting_control(self, setting, settingsStorage)

            return _make_control
        return _call_compat(originalGetSettingMaker, self, setting, *args, **kwargs)

    def _update_value_for_control(
        self: Any,
        setting: Any,
        settingsStorage: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        originalKwargs = dict(kwargs)
        if not patchActive[0]:
            return _call_compat(originalUpdateValueForControl, self, setting, settingsStorage, *args, **originalKwargs)
        if "settingsStorage" in originalKwargs:
            if settingsStorage is None:
                settingsStorage = originalKwargs["settingsStorage"]
            del originalKwargs["settingsStorage"]
        if _is_google_tts_read_only_setting(setting, settingsStorage):
            try:
                if setting.id not in getattr(self, "sizerDict", {}):
                    return
                self.settingsSizer.Show(self.sizerDict[setting.id])
                getattr(self, f"{setting.id}Edit").SetValue(str(getattr(settingsStorage, setting.id, "") or ""))
            except Exception:
                log.debug("Could not update Google TTS read-only speech setting.", exc_info=True)
            return
        settingId = getattr(setting, "id", "")
        if getattr(settingsStorage, "name", "") == SYNTH_NAME and settingId in ("voice", "variant"):
            try:
                availableAttr = "availableVoices" if settingId == "voice" else "availableVariants"
                available = getattr(settingsStorage, availableAttr, None)
                if available is not None and hasattr(available, "values"):
                    options = list(available.values())
                    control = getattr(self, f"{settingId}List", None)
                    if control is not None:
                        currentOptions = getattr(self, f"_{settingId}s", [])
                        if [option.id for option in currentOptions] != [option.id for option in options]:
                            setattr(self, f"_{settingId}s", options)
                            control.Clear()
                            for option in options:
                                control.Append(option.displayName)
            except Exception:
                log.debug("Could not refresh Google TTS %s choices.", settingId, exc_info=True)
        return _call_compat(originalUpdateValueForControl, self, setting, settingsStorage, *args, **originalKwargs)

    def _on_discard(self: Any, *args: Any, **kwargs: Any) -> None:
        if not patchActive[0]:
            return _call_compat(originalOnDiscard, self, *args, **kwargs)
        try:
            settingsInst = self.getSettings()
        except Exception:
            return _call_compat(originalOnDiscard, self, *args, **kwargs)
        if getattr(settingsInst, "name", "") != SYNTH_NAME:
            return _call_compat(originalOnDiscard, self, *args, **kwargs)
        supportedSettings = tuple(getattr(settingsInst, "supportedSettings", ()))
        nonStringIds = {
            getattr(setting, "id", "")
            for setting in supportedSettings
            if isinstance(setting, (NumericDriverSetting, BooleanDriverSetting))
        }
        candidateIds = {
            getattr(setting, "id", "")
            for setting in supportedSettings
            if getattr(setting, "id", "") and getattr(setting, "id", "") not in nonStringIds
        }
        sizerDict = getattr(self, "sizerDict", None)
        if isinstance(sizerDict, dict):
            candidateIds.update(name for name in sizerDict if name and name not in nonStringIds)
        for settingId in candidateIds:
            control = getattr(self, f"{settingId}List", None)
            if control is None:
                continue
            try:
                control.Unbind(wx.EVT_CHOICE)
            except Exception:
                log.debug("Could not unbind Google TTS speech setting control.", exc_info=True)
        settingsInst.loadSettings()
        if originalOnDiscard is not None:
            try:
                _call_compat(originalOnDiscard, self, *args, **kwargs)
            except AttributeError:
                log.debug("Ignoring missing control during base NVDA onDiscard cleanup.", exc_info=True)

    def _refresh_gui(self: Any, *args: Any, **kwargs: Any) -> None:
        if not patchActive[0]:
            return _call_compat(originalRefreshGui, self, *args, **kwargs)
        try:
            hadAliveSettingsRef = bool(
                callable(getattr(self, "_currentSettingsRef", None)) and self._currentSettingsRef() is not None
            )
            result = _call_compat(originalRefreshGui, self, *args, **kwargs)
            if hadAliveSettingsRef:
                try:
                    settingsInst = self.getSettings()
                    if getattr(settingsInst, "name", "") == SYNTH_NAME and isinstance(
                        getattr(self, "sizerDict", None), dict
                    ):
                        supportedIds = {getattr(s, "id", "") for s in getattr(settingsInst, "supportedSettings", ())}
                        for name, sizer in self.sizerDict.items():
                            if name not in supportedIds:
                                self.settingsSizer.Hide(sizer)
                        self.updateDriverSettings()
                        _hide_google_tts_auto_profile_speech_controls(self)
                    elif getattr(self, "_googleTtsHidAutoProfileSpeechControls", False):
                        _hide_google_tts_auto_profile_speech_controls(self)
                except Exception:
                    log.debug("Could not sync Google TTS Speech Settings panel on refresh.", exc_info=True)
            return result
        except RuntimeError as exc:
            if window_is_alive(self) and "has been deleted" not in str(exc):
                raise
            log.debug("Ignoring refresh for destroyed NVDA auto settings panel.", exc_info=True)

    def _voice_make_settings(self: Any, settingsSizer: wx.Sizer, *args: Any, **kwargs: Any) -> None:
        _call_compat(originalVoiceMakeSettings, self, settingsSizer, *args, **kwargs)
        if patchActive[0]:
            _hide_google_tts_auto_profile_speech_controls(self)

    _wrap_nvda_callable(_get_setting_maker, originalGetSettingMaker)
    _wrap_nvda_callable(_update_value_for_control, originalUpdateValueForControl)
    _wrap_nvda_callable(_on_discard, originalOnDiscard)
    _wrap_nvda_callable(_refresh_gui, originalRefreshGui)
    _patchedAutoSettingsGetSettingMaker = _get_setting_maker
    _patchedAutoSettingsUpdateValueForControl = _update_value_for_control
    _patchedAutoSettingsOnDiscard = _on_discard
    _patchedAutoSettingsRefreshGui = _refresh_gui
    autoSettingsMixin._getSettingMaker = _get_setting_maker
    autoSettingsMixin._updateValueForControl = _update_value_for_control
    autoSettingsMixin.onDiscard = _on_discard
    autoSettingsMixin.refreshGui = _refresh_gui
    if voiceSettingsPanel is not None and originalVoiceMakeSettings is not None:
        _wrap_nvda_callable(_voice_make_settings, originalVoiceMakeSettings)
        _patchedVoiceSettingsMakeSettings = _voice_make_settings
        voiceSettingsPanel.makeSettings = _voice_make_settings


def _unpatch_read_only_text_setting() -> None:
    global \
        _originalAutoSettingsGetSettingMaker, \
        _originalAutoSettingsUpdateValueForControl, \
        _originalAutoSettingsOnDiscard
    global _originalAutoSettingsRefreshGui
    global _originalVoiceSettingsMakeSettings
    global _patchedAutoSettingsGetSettingMaker, _patchedAutoSettingsUpdateValueForControl, _patchedAutoSettingsOnDiscard
    global _patchedAutoSettingsRefreshGui
    global _patchedVoiceSettingsMakeSettings
    global _readOnlyTextSettingPatchActive
    if _originalAutoSettingsGetSettingMaker is None:
        return
    if _readOnlyTextSettingPatchActive is not None:
        _readOnlyTextSettingPatchActive[0] = False
        _readOnlyTextSettingPatchActive = None
    autoSettingsMixin = getattr(gui.settingsDialogs, "AutoSettingsMixin", None)
    if autoSettingsMixin is not None:
        if getattr(autoSettingsMixin, "_getSettingMaker", None) is _patchedAutoSettingsGetSettingMaker:
            autoSettingsMixin._getSettingMaker = _originalAutoSettingsGetSettingMaker
        if (
            _originalAutoSettingsUpdateValueForControl is not None
            and getattr(autoSettingsMixin, "_updateValueForControl", None) is _patchedAutoSettingsUpdateValueForControl
        ):
            autoSettingsMixin._updateValueForControl = _originalAutoSettingsUpdateValueForControl
        if (
            _originalAutoSettingsOnDiscard is not None
            and getattr(autoSettingsMixin, "onDiscard", None) is _patchedAutoSettingsOnDiscard
        ):
            autoSettingsMixin.onDiscard = _originalAutoSettingsOnDiscard
        if (
            _originalAutoSettingsRefreshGui is not None
            and getattr(autoSettingsMixin, "refreshGui", None) is _patchedAutoSettingsRefreshGui
        ):
            autoSettingsMixin.refreshGui = _originalAutoSettingsRefreshGui
    voiceSettingsPanel = getattr(gui.settingsDialogs, "VoiceSettingsPanel", None)
    if (
        voiceSettingsPanel is not None
        and _originalVoiceSettingsMakeSettings is not None
        and getattr(voiceSettingsPanel, "makeSettings", None) is _patchedVoiceSettingsMakeSettings
    ):
        voiceSettingsPanel.makeSettings = _originalVoiceSettingsMakeSettings
    _originalAutoSettingsGetSettingMaker = None
    _originalAutoSettingsUpdateValueForControl = None
    _originalAutoSettingsOnDiscard = None
    _originalAutoSettingsRefreshGui = None
    _originalVoiceSettingsMakeSettings = None
    _patchedAutoSettingsGetSettingMaker = None
    _patchedAutoSettingsUpdateValueForControl = None
    _patchedAutoSettingsOnDiscard = None
    _patchedAutoSettingsRefreshGui = None
    _patchedVoiceSettingsMakeSettings = None


def _google_auto_language_detection_active() -> bool:
    try:
        synth = synthDriverHandler.getSynth()
        return getattr(synth, "name", "") == SYNTH_NAME and synth._auto_language_detection_enabled()
    except Exception:
        return False


def _google_auto_language_multiple_profiles_active() -> bool:
    try:
        synth = synthDriverHandler.getSynth()
        if getattr(synth, "name", "") != SYNTH_NAME or not synth._auto_language_detection_enabled():
            return False
        return len(synth._auto_language_candidates()) > 1
    except Exception:
        return False


def _show_voice_dictionary_auto_language_message() -> None:
    if is_secure_mode():
        return
    show_message_box(
        _(
            "Voice dictionary preferences are unavailable while automatic language profiles are enabled.\n\n"
            "Google TTS For NVDA may switch between several profile variants while speaking, so NVDA cannot "
            "know which single variant voice dictionary to edit.\n\n"
            "Open the Google TTS For NVDA category in NVDA Settings and turn off automatic language profiles, "
            "then open Voice dictionary again."
        ),
        _("Google TTS For NVDA"),
        wx.OK | wx.ICON_INFORMATION,
        dialog_parent(),
    )


def _is_voice_dictionary_dialog(dialog: Any) -> bool:
    if dialog is None:
        return False
    candidates = {
        getattr(gui, "VoiceDictionaryDialog", None),
        getattr(getattr(gui, "speechDict", None), "VoiceDictionaryDialog", None),
        getattr(getattr(gui, "settingsDialogs", None), "VoiceDictionaryDialog", None),
    }
    candidates.discard(None)
    if dialog in candidates:
        return True
    return getattr(dialog, "__name__", "") == "VoiceDictionaryDialog"


def _patch_voice_dictionary_dialog() -> None:
    global _originalPopupSettingsDialog, _patchedPopupSettingsDialog, _voiceDictionaryDialogPatchActive
    if _originalPopupSettingsDialog is not None:
        return
    mainFrame = getattr(gui, "mainFrame", None)
    if mainFrame is None or not hasattr(mainFrame, "popupSettingsDialog"):
        return
    _originalPopupSettingsDialog = mainFrame.popupSettingsDialog
    originalPopupSettingsDialog = _originalPopupSettingsDialog
    patchActive = [True]
    _voiceDictionaryDialogPatchActive = patchActive

    def popup_settings_dialog(dialog: Any, *args: Any, **kwargs: Any) -> Any:
        global _activeLoadedVoiceDictVariant
        if patchActive[0] and _is_voice_dictionary_dialog(dialog):
            if _google_auto_language_detection_active():
                _show_voice_dictionary_auto_language_message()
                return None
            _voiceDictCache.clear()
            _activeLoadedVoiceDictVariant = None
            try:
                currentSynth = synthDriverHandler.getSynth()
                if getattr(currentSynth, "name", "") == SYNTH_NAME:
                    voice = _current_google_tts_speaker_id(currentSynth)
                    if voice:
                        _load_voice_dictionary_for_voice(currentSynth, voice)
            except Exception:
                log.debug("Could not reload Google TTS voice dictionary before opening dialog.", exc_info=True)
        return _call_compat(originalPopupSettingsDialog, dialog, *args, **kwargs)

    _wrap_nvda_callable(popup_settings_dialog, originalPopupSettingsDialog)
    _patchedPopupSettingsDialog = popup_settings_dialog
    mainFrame.popupSettingsDialog = popup_settings_dialog


def _unpatch_voice_dictionary_dialog() -> None:
    global _originalPopupSettingsDialog, _patchedPopupSettingsDialog, _voiceDictionaryDialogPatchActive
    if _originalPopupSettingsDialog is None:
        return
    if _voiceDictionaryDialogPatchActive is not None:
        _voiceDictionaryDialogPatchActive[0] = False
        _voiceDictionaryDialogPatchActive = None
    mainFrame = getattr(gui, "mainFrame", None)
    if mainFrame is not None and getattr(mainFrame, "popupSettingsDialog", None) is _patchedPopupSettingsDialog:
        mainFrame.popupSettingsDialog = _originalPopupSettingsDialog
    _originalPopupSettingsDialog = None
    _patchedPopupSettingsDialog = None


def _normalize_language(language: str | None) -> str:
    return language_utils.normalize_language(language)


_normalize_language_key = _normalize_language


def _language_root(language: str | None) -> str:
    return language_utils.language_root(language)


def _language_match_keys(language: str | None) -> set[str]:
    return language_detector.language_match_keys(language)


def _language_matches(left: str | None, right: str | None) -> bool:
    return language_detector.language_matches(left, right)


_same_language = _language_matches


def _nvda_unicode_normalization_enabled() -> bool:
    try:
        speechConf = config.conf["speech"]
        if isinstance(speechConf, dict):
            value = speechConf.get("unicodeNormalization", False)
        else:
            value = speechConf["unicodeNormalization"]
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "enabled")
        return bool(value)
    except Exception:
        return False


def _google_lang_change_command(language: str | None, *, spelling: bool = False) -> LangChangeCommand:
    command = LangChangeCommand(_normalize_nvda_locale_for_language(language) or _nvda_locale_for_language(language))
    try:
        setattr(command, _GOOGLE_TTS_LANG_CHANGE_ATTR, language)
        if spelling:
            setattr(command, _GOOGLE_TTS_SPELLING_LANG_ATTR, True)
    except Exception:
        log.debug("Could not preserve Google TTS language code on LangChangeCommand.", exc_info=True)
    return command


def _append_google_lang_change(targetList: list[Any], language: str | None, *, spelling: bool = False) -> None:
    targetList.append(_google_lang_change_command(language, spelling=spelling))
    if getattr(_speakPipelineState, "depth", 0) > 0:
        targetList.append(_GoogleTtsProfileLangCommand(language, spelling=spelling))


def _google_lang_change_language(command: LangChangeCommand) -> object:
    return getattr(command, _GOOGLE_TTS_LANG_CHANGE_ATTR, _MISSING_GOOGLE_TTS_LANGUAGE)


def _normalize_nvda_locale_for_language(language: str | None) -> str | None:
    return language_utils.get_nvda_locale_for_language(language) or None


def _nvda_locale_for_language(language: str | None) -> str | None:
    return language_utils.resolve_nvda_locale(language)


def _auto_detect_language_for_speech_filter(synth: Any, text: str) -> str | None:
    candidates = synth._auto_language_candidates()
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    preferred = synth._auto_language_preferred(candidates, synth.voice)
    detected = synth._detect_auto_language(text, candidates, preferred)
    if detected is not None:
        return detected
    return preferred


def _auto_language_for_spelling_text(synth: Any, text: str) -> str | None:
    candidates = synth._auto_language_candidates()
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    preferred = synth._auto_language_preferred(candidates, synth.voice)
    normalizedText = language_profiles.normalize_mathematical_alphanumeric(text)
    stripped = normalizedText.strip()
    if stripped:
        if language_profiles.is_single_grapheme_token(stripped):
            resolved = language_detector.resolve_single_grapheme_candidate(stripped, candidates, preferred)
            if resolved:
                return resolved
        else:
            candidateRoots: dict[str, str] = {}
            for c in candidates:
                candidateRoots.setdefault(synth._language_root(c), c)
            prefRoot = synth._language_root(preferred)
            scriptSignal = language_profiles.language_script_signal(stripped, candidateRoots)
            if (
                scriptSignal
                and scriptSignal != prefRoot
                and scriptSignal in candidateRoots
                and not any(
                    language_profiles.get_character_script(ord(ch)) == "Latin" for ch in stripped if ch.isalpha()
                )
            ):
                return candidateRoots[scriptSignal]
    return preferred


def _auto_language_for_process_text(synth: Any, locale: str | None, text: str) -> str | None:
    candidates = synth._auto_language_candidates()
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    localeCandidate = synth._auto_language_candidate_for_language(locale, candidates) if locale else ""
    if getattr(_speakPipelineState, "depth", 0) > 0 and localeCandidate:
        return localeCandidate
    preferred = synth._auto_language_preferred(candidates, synth.voice)
    stripped = language_profiles.normalize_mathematical_alphanumeric(text).strip()
    if language_profiles.is_single_grapheme_token(stripped) or language_profiles.is_spelling_only_text(stripped):
        detected = synth._detect_auto_language(text, candidates, preferred)
        return detected or preferred
    detected = synth._detect_auto_language(text, candidates, None)
    if detected is not None:
        return detected
    return localeCandidate or preferred


def _auto_profile_variant_for_language(synth: Any, language: str | None) -> str | None:
    candidates = synth._auto_language_candidates()
    if not language or not candidates:
        return None
    targetLanguage = synth._auto_language_candidate_for_language(language, candidates)
    if not targetLanguage:
        return None
    profile = synth._auto_language_profile_for_language(targetLanguage)
    voice = str(profile.get("voice") or "")
    if synth._voice_matches_language(voice, targetLanguage):
        return voice
    return synth._voice_for_language(targetLanguage, synth.voice)


def _profile_int(value: Any, default: int, minimum: int = 0, maximum: int = 100) -> int:
    return audio_math.clamp_int(value, default, minimum, maximum)


def _profile_bool(value: Any, default: bool = False) -> bool:
    return audio_math.coerce_bool(value, default)


def _auto_profile_character_settings_for_language(synth: Any, language: str | None) -> dict[str, Any] | None:
    candidates = synth._auto_language_candidates()
    if not candidates:
        return None
    targetLanguage = language
    if not targetLanguage and len(candidates) == 1:
        targetLanguage = candidates[0]
    if not targetLanguage:
        return None
    profileLanguage = synth._auto_language_candidate_for_language(targetLanguage, candidates)
    if not profileLanguage:
        return None
    profile = synth._auto_language_profile_for_language(profileLanguage)
    try:
        synthConfig = config.conf["speech"][SYNTH_NAME]
    except Exception:
        return None
    return {
        "capPitchChange": _profile_int(profile.get("capPitchChange"), synthConfig.get("capPitchChange", 30), -100, 100),
        "sayCapForCapitals": _profile_bool(
            profile.get("sayCapForCapitals"),
            synthConfig.get("sayCapForCapitals", False),
        ),
        "beepForCapitals": _profile_bool(
            profile.get("beepForCapitals"),
            synthConfig.get("beepForCapitals", False),
        ),
        "useSpellingFunctionality": _profile_bool(
            profile.get("useSpellingFunctionality"),
            synthConfig.get("useSpellingFunctionality", True),
        ),
    }


def _auto_profile_character_context_for_text(
    locale: str | None,
    text: str | None,
) -> tuple[dict[str, Any], str | None] | None:
    if not isinstance(text, str):
        return None
    try:
        synth = synthDriverHandler.getSynth()
        if getattr(synth, "name", "") != SYNTH_NAME or not synth._auto_language_detection_enabled():
            return None
        targetLanguage = _auto_language_for_spelling_text(synth, text)
        settings = _auto_profile_character_settings_for_language(synth, targetLanguage)
        if settings is None:
            return None
        effectiveLocale = _nvda_locale_for_language(targetLanguage) or _nvda_locale_for_language(locale) or locale
        return settings, effectiveLocale
    except Exception:
        log.debug("Could not resolve Google TTS auto-language character settings.", exc_info=True)
        return None


def _single_auto_profile_character_settings() -> dict[str, Any] | None:
    try:
        synth = synthDriverHandler.getSynth()
        if getattr(synth, "name", "") != SYNTH_NAME or not synth._auto_language_detection_enabled():
            return None
        candidates = synth._auto_language_candidates()
        if not candidates:
            return None
        targetLanguage = (
            candidates[0] if len(candidates) == 1 else synth._auto_language_preferred(candidates, synth.voice)
        )
        return _auto_profile_character_settings_for_language(synth, targetLanguage)
    except Exception:
        log.debug("Could not resolve single Google TTS auto-language character profile.", exc_info=True)
        return None


class _VoiceDictionarySynthProxy:
    """Expose a speaker variant to NVDA's voice dictionary loader without changing the live synth."""

    def __init__(self, synth: Any, voice: str, *args: Any, **kwargs: Any) -> None:
        self._synth = synth
        self.name = getattr(synth, "name", "")
        speakerVoiceInfos = getattr(synth, "_speaker_voice_infos", None)
        self.availableVoices = (
            speakerVoiceInfos() if callable(speakerVoiceInfos) else getattr(synth, "availableVoices", {})
        )
        self.voice = voice
        self.variant = voice

    def isSupported(self, setting: str, *args: Any, **kwargs: Any) -> bool:
        if setting == "voice":
            return self.voice in self.availableVoices
        return bool(_call_compat(self._synth.isSupported, setting, *args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._synth, name)


_activeLoadedVoiceDictVariant: str | None = None
_voiceDictCache: dict[str, tuple[str | None, tuple[int, int] | None, list[Any]]] = {}


def _resolve_nvda_voice_speech_dict() -> tuple[Any | None, Any | None]:
    """Return (voiceSpeechDictDefinition_or_None, voiceSpeechDict_or_None) without triggering deprecations."""
    definitions = getattr(speechDictHandler, "definitions", None)
    if definitions is not None:
        defs = getattr(definitions, "_speechDictDefinitions", None)
        if isinstance(defs, list):
            for d in defs:
                if getattr(d, "name", None) == "voice" or str(getattr(d, "source", "")).endswith("VOICE"):
                    dictionary = getattr(d, "dictionary", None)
                    if isinstance(dictionary, list):
                        return d, dictionary
    dictionaries = speechDictHandler.__dict__.get("dictionaries")
    if isinstance(dictionaries, dict):
        dictionary = dictionaries.get("voice")
        if isinstance(dictionary, list):
            return None, dictionary
    return None, None


def _file_stat_signature(filePath: str | None) -> tuple[int, int] | None:
    if not filePath:
        return None
    try:
        st = os.stat(filePath)
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


def _load_voice_dictionary_for_voice(synth: Any, voice: str) -> bool:
    global _activeLoadedVoiceDictVariant
    speakerVoiceInfos = getattr(synth, "_speaker_voice_infos", None)
    availableVoices = speakerVoiceInfos() if callable(speakerVoiceInfos) else getattr(synth, "availableVoices", {})
    if not voice or voice not in availableVoices:
        return False
    with _voiceDictLock:
        voiceDef, voiceDict = _resolve_nvda_voice_speech_dict()
        cached = _voiceDictCache.get(voice)
        if cached is not None and voiceDict is not None:
            cachedFileName, cachedStat, cachedEntries = cached
            currentStat = _file_stat_signature(cachedFileName)
            if currentStat == cachedStat:
                voiceDefPathMismatch = voiceDef is not None and getattr(voiceDef, "path", None) != cachedFileName
                if (
                    _activeLoadedVoiceDictVariant != voice
                    or getattr(voiceDict, "fileName", None) != cachedFileName
                    or voiceDefPathMismatch
                ):
                    voiceDict[:] = cachedEntries
                    voiceDict.fileName = cachedFileName
                    if voiceDef is not None:
                        with contextlib.suppress(Exception):
                            object.__setattr__(voiceDef, "path", cachedFileName)
                    _activeLoadedVoiceDictVariant = voice
                elif voiceDict != cachedEntries:
                    _voiceDictCache[voice] = (
                        cachedFileName,
                        currentStat,
                        list(voiceDict),
                    )
                return True
        elif _activeLoadedVoiceDictVariant == voice:
            return True
        loadVoiceDict = (
            _originalSpeechDictDefinitionsLoadVoiceDict
            or _originalSpeechDictLoadVoiceDict
            or getattr(getattr(speechDictHandler, "definitions", None), "loadVoiceDict", None)
            or getattr(speechDictHandler, "loadVoiceDict", None)
        )
        if not callable(loadVoiceDict):
            return False
        _call_compat(loadVoiceDict, _VoiceDictionarySynthProxy(synth, voice))
        _activeLoadedVoiceDictVariant = voice
        voiceDef, voiceDict = _resolve_nvda_voice_speech_dict()
        if voiceDict is not None:
            loadedFileName = getattr(voiceDict, "fileName", None) or (
                getattr(voiceDef, "path", None) if voiceDef is not None else None
            )
            _voiceDictCache[voice] = (
                loadedFileName,
                _file_stat_signature(loadedFileName),
                list(voiceDict),
            )
        return True


def _current_google_tts_speaker_id(synth: Any) -> str:
    return str(getattr(synth, "variant", "") or getattr(synth, "voice", "") or "")


def _patch_google_tts_voice_dictionary_loading() -> None:
    global _originalSpeechDictLoadVoiceDict, _originalSpeechDictDefinitionsLoadVoiceDict
    global _patchedSpeechDictLoadVoiceDict, _voiceDictionaryLoadingPatchActive
    if _patchedSpeechDictLoadVoiceDict is not None:
        return
    definitions = getattr(speechDictHandler, "definitions", None)
    topLoadVoiceDict = getattr(speechDictHandler, "loadVoiceDict", None)
    defsLoadVoiceDict = getattr(definitions, "loadVoiceDict", None) if definitions is not None else None
    loadVoiceDict = defsLoadVoiceDict or topLoadVoiceDict
    if loadVoiceDict is None:
        log.debugWarning(
            "NVDA speechDictHandler.loadVoiceDict is unavailable; Google TTS voice dictionary patch disabled."
        )
        return
    _originalSpeechDictLoadVoiceDict = topLoadVoiceDict
    _originalSpeechDictDefinitionsLoadVoiceDict = defsLoadVoiceDict
    originalLoadVoiceDict = loadVoiceDict
    patchActive = [True]
    _voiceDictionaryLoadingPatchActive = patchActive

    def load_voice_dictionary_for_google_tts_variant(synth: Any, *args: Any, **kwargs: Any) -> None:
        global _activeLoadedVoiceDictVariant
        if not patchActive[0]:
            _call_compat(originalLoadVoiceDict, synth, *args, **kwargs)
            return
        try:
            if getattr(synth, "name", "") == SYNTH_NAME:
                voice = _current_google_tts_speaker_id(synth)
                if voice and _load_voice_dictionary_for_voice(synth, voice):
                    return
        except Exception:
            log.debug("Could not load Google TTS voice dictionary for the selected variant.", exc_info=True)
        _activeLoadedVoiceDictVariant = None
        _call_compat(originalLoadVoiceDict, synth, *args, **kwargs)

    _wrap_nvda_callable(load_voice_dictionary_for_google_tts_variant, originalLoadVoiceDict)
    _patchedSpeechDictLoadVoiceDict = load_voice_dictionary_for_google_tts_variant
    if _originalSpeechDictLoadVoiceDict is not None:
        speechDictHandler.loadVoiceDict = load_voice_dictionary_for_google_tts_variant
    if definitions is not None and _originalSpeechDictDefinitionsLoadVoiceDict is not None:
        definitions.loadVoiceDict = load_voice_dictionary_for_google_tts_variant
    try:
        currentSynth = synthDriverHandler.getSynth()
        if getattr(currentSynth, "name", "") == SYNTH_NAME:
            load_voice_dictionary_for_google_tts_variant(currentSynth)
    except Exception:
        log.debug("Could not load active Google TTS variant voice dictionary on patch init.", exc_info=True)


def _unpatch_google_tts_voice_dictionary_loading() -> None:
    global _originalSpeechDictLoadVoiceDict, _originalSpeechDictDefinitionsLoadVoiceDict
    global _patchedSpeechDictLoadVoiceDict, _activeLoadedVoiceDictVariant, _voiceDictionaryLoadingPatchActive
    if _patchedSpeechDictLoadVoiceDict is None:
        return
    if _voiceDictionaryLoadingPatchActive is not None:
        _voiceDictionaryLoadingPatchActive[0] = False
        _voiceDictionaryLoadingPatchActive = None
    if (
        _originalSpeechDictLoadVoiceDict is not None
        and getattr(speechDictHandler, "loadVoiceDict", None) is _patchedSpeechDictLoadVoiceDict
    ):
        speechDictHandler.loadVoiceDict = _originalSpeechDictLoadVoiceDict
    definitions = getattr(speechDictHandler, "definitions", None)
    if (
        definitions is not None
        and _originalSpeechDictDefinitionsLoadVoiceDict is not None
        and getattr(definitions, "loadVoiceDict", None) is _patchedSpeechDictLoadVoiceDict
    ):
        definitions.loadVoiceDict = _originalSpeechDictDefinitionsLoadVoiceDict
    _originalSpeechDictLoadVoiceDict = None
    _originalSpeechDictDefinitionsLoadVoiceDict = None
    _patchedSpeechDictLoadVoiceDict = None
    _activeLoadedVoiceDictVariant = None
    _voiceDictCache.clear()


def _strip_external_lang_changes_and_coalesce(speechSequence: list[Any]) -> list[Any]:
    normalizedSequence: list[Any] = []
    mergeNextStr = False
    for item in speechSequence:
        if isinstance(item, LangChangeCommand):
            googleLanguage = _google_lang_change_language(item)
            if googleLanguage is _MISSING_GOOGLE_TTS_LANGUAGE:
                if normalizedSequence and isinstance(normalizedSequence[-1], str):
                    mergeNextStr = True
                continue
        if mergeNextStr and isinstance(item, str) and normalizedSequence and isinstance(normalizedSequence[-1], str):
            normalizedSequence[-1] = normalizedSequence[-1] + item
            mergeNextStr = False
            continue
        mergeNextStr = False
        normalizedSequence.append(item)
    return normalizedSequence


def _filter_auto_language_speech_sequence(speechSequence: list[Any], *args: Any, **kwargs: Any) -> list[Any]:
    try:
        synth = synthDriverHandler.getSynth()
    except Exception:
        return speechSequence
    if getattr(synth, "name", "") != SYNTH_NAME:
        return speechSequence
    try:
        autoLanguageEnabled = bool(synth._auto_language_detection_enabled())
    except Exception:
        log.debug("Could not read Google TTS automatic language profile state.", exc_info=True)
        autoLanguageEnabled = False
    if not autoLanguageEnabled:
        return speechSequence
    speechSequence = _strip_external_lang_changes_and_coalesce(speechSequence)
    try:
        candidates = synth._auto_language_candidates()
        preferred = synth._auto_language_preferred(candidates, synth.voice) if candidates else None
    except Exception:
        candidates = []
        preferred = None
    if len(candidates) <= 1:
        singleProfileFiltered: list[Any] = []
        for item in speechSequence:
            if isinstance(item, LangChangeCommand):
                continue
            if isinstance(item, str) and item:
                singleProfileFiltered.append(language_profiles.normalize_mathematical_alphanumeric(item))
            else:
                singleProfileFiltered.append(item)
        return singleProfileFiltered
    baseLanguage = None
    try:
        baseLanguage = synth.voice
        if baseLanguage not in getattr(synth, "availableVoices", {}):
            baseLanguage = synth.catalog.language_for_voice(synth.voice)
    except Exception:
        log.debug("Could not resolve Google TTS base language for speech filtering.", exc_info=True)
    filtered: list[Any] = []
    currentAutoLanguage: str | None = None
    inCharacterMode = False
    inSpellingSequence = False
    spellingSequenceLanguage: str | None = None
    nonEmptyStrCount = sum(1 for x in speechSequence if isinstance(x, str) and x.strip())
    for idx, item in enumerate(speechSequence):
        if CharacterModeCommand is not None and isinstance(item, CharacterModeCommand):
            inCharacterMode = bool(getattr(item, "state", False))
            filtered.append(item)
            continue
        if SuppressUnicodeNormalizationCommand is not None and isinstance(item, SuppressUnicodeNormalizationCommand):
            if not bool(getattr(item, "state", False)):
                inSpellingSequence = False
                spellingSequenceLanguage = None
            filtered.append(item)
            continue
        if (EndUtteranceCommand is not None and isinstance(item, EndUtteranceCommand)) or type(
            item
        ).__name__ == "EndUtteranceCommand":
            inCharacterMode = False
            filtered.append(item)
            continue
        if isinstance(item, _GoogleTtsProfileLangCommand):
            currentAutoLanguage = item.language if isinstance(item.language, str) else None
            if item.language is None:
                inSpellingSequence = False
                spellingSequenceLanguage = None
            elif item.spelling:
                inSpellingSequence = True
                spellingSequenceLanguage = currentAutoLanguage
            else:
                inSpellingSequence = False
                spellingSequenceLanguage = None
            filtered.append(item)
            continue
        if isinstance(item, LangChangeCommand):
            googleLanguage = _google_lang_change_language(item)
            if googleLanguage is _MISSING_GOOGLE_TTS_LANGUAGE:
                continue
            currentAutoLanguage = googleLanguage if isinstance(googleLanguage, str) else None
            if getattr(item, _GOOGLE_TTS_SPELLING_LANG_ATTR, False):
                inSpellingSequence = True
                spellingSequenceLanguage = currentAutoLanguage
            elif googleLanguage is None:
                inSpellingSequence = False
                spellingSequenceLanguage = None
            filtered.append(item)
            continue
        if isinstance(item, str) and item:
            stripped = item.strip()
            if not stripped:
                filtered.append(item)
                continue
            # Multi-item utterances with CharacterModeCommand (such as UI access key announcements)
            # stay in the active utterance language rather than switching to preferredLanguage.
            if inCharacterMode and not inSpellingSequence and nonEmptyStrCount > 1 and currentAutoLanguage is not None:
                filtered.append(language_profiles.normalize_mathematical_alphanumeric(item))
                continue
            # Typing, spelling, and character navigation mode:
            # Route directly to preferred language (or matching non-preferred candidate script for a single char).
            isSingleGrapheme = language_profiles.is_single_grapheme_token(stripped)
            if preferred and (inCharacterMode or inSpellingSequence or (nonEmptyStrCount == 1 and isSingleGrapheme)):
                nonPreferredMatch = (
                    language_detector.resolve_single_grapheme_candidate(stripped, candidates, preferred)
                    if isSingleGrapheme
                    else None
                )
                if nonPreferredMatch:
                    targetLang = nonPreferredMatch
                elif inSpellingSequence and (spellingSequenceLanguage or currentAutoLanguage):
                    targetLang = spellingSequenceLanguage or currentAutoLanguage
                else:
                    targetLang = preferred or baseLanguage

                if targetLang is not None and not _same_language(currentAutoLanguage, targetLang):
                    _append_google_lang_change(filtered, targetLang)
                    currentAutoLanguage = targetLang
                filtered.append(language_profiles.normalize_mathematical_alphanumeric(item))
                continue

            if candidates and preferred:
                defaultLang = currentAutoLanguage
                if defaultLang is None and not language_profiles.has_language_words(item):
                    for nextItem in speechSequence[idx + 1 :]:
                        if isinstance(nextItem, str) and language_profiles.has_language_words(nextItem):
                            try:
                                nextSegments = language_profiles.segment_mixed_text(
                                    nextItem,
                                    candidates,
                                    preferred,
                                )
                                if nextSegments:
                                    defaultLang = nextSegments[0][1]
                            except Exception:
                                pass
                            break
                try:
                    segments = language_profiles.segment_mixed_text(
                        item,
                        candidates,
                        preferred,
                        defaultLanguage=defaultLang,
                    )
                except Exception:
                    log.debug("Could not segment mixed text for Google TTS auto-language.", exc_info=True)
                    segments = [(item, defaultLang or preferred)]

                for segText, segLang in segments:
                    targetLang = segLang or baseLanguage or preferred
                    if targetLang is not None and not _same_language(currentAutoLanguage, targetLang):
                        _append_google_lang_change(filtered, targetLang)
                        currentAutoLanguage = targetLang
                    filtered.append(segText)
                continue

            try:
                targetLanguage = _auto_detect_language_for_speech_filter(synth, item)
            except Exception:
                log.debug("Could not auto-detect Google TTS speech language.", exc_info=True)
                targetLanguage = None
            if targetLanguage is None and currentAutoLanguage is not None:
                targetLanguage = baseLanguage
            if targetLanguage is not None and not _same_language(currentAutoLanguage, targetLanguage):
                _append_google_lang_change(filtered, targetLanguage)
                currentAutoLanguage = targetLanguage
            filtered.append(language_profiles.normalize_mathematical_alphanumeric(item))
            continue
        filtered.append(item)
    return filtered


def _register_auto_language_speech_filter() -> None:
    global _autoLanguageSpeechFilterRegistered
    if _autoLanguageSpeechFilterRegistered:
        return
    speech.extensions.filter_speechSequence.register(_filter_auto_language_speech_sequence)
    try:
        # Run before NVDA language reporting so profile-enabled names follow Google detection.
        speech.extensions.filter_speechSequence.moveToEnd(_filter_auto_language_speech_sequence, last=False)
    except Exception:
        log.debug("Could not prioritize Google TTS speech sequence filter.", exc_info=True)
    _autoLanguageSpeechFilterRegistered = True


def _unregister_auto_language_speech_filter() -> None:
    global _autoLanguageSpeechFilterRegistered
    if not _autoLanguageSpeechFilterRegistered:
        return
    try:
        speech.extensions.filter_speechSequence.unregister(_filter_auto_language_speech_sequence)
    except Exception:
        log.debug("Could not unregister Google TTS auto-language speech filter.", exc_info=True)
    _autoLanguageSpeechFilterRegistered = False


_CONFIG_MISSING = object()


def _normalize_text_for_auto_profile(text: str) -> str:
    if not _nvda_unicode_normalization_enabled() or unicodedata.normalize("NFKC", text) == text:
        return language_profiles.normalize_mathematical_alphanumeric(text)
    return text


@contextlib.contextmanager
def _temporary_config_override(section: Any, updates: dict[str, Any]) -> Any:
    if section is None or not updates:
        yield
        return
    cache = getattr(section, "_cache", None)
    if isinstance(cache, dict) and hasattr(section, "profiles"):
        savedCache = {key: cache.get(key, _CONFIG_MISSING) for key in updates}
        cache.update(updates)
        try:
            yield
        finally:
            for key, prevValue in savedCache.items():
                if prevValue is _CONFIG_MISSING:
                    cache.pop(key, None)
                else:
                    cache[key] = prevValue
        return
    savedValues = {key: section.get(key, _CONFIG_MISSING) for key in updates}
    for key, value in updates.items():
        section[key] = value
    try:
        yield
    finally:
        for key, prevValue in savedValues.items():
            if prevValue is _CONFIG_MISSING:
                with contextlib.suppress(Exception):
                    del section[key]
            else:
                with contextlib.suppress(Exception):
                    section[key] = prevValue


def _patch_auto_language_voice_dictionary() -> None:
    global _originalSpeechSpeak, _originalSpeechPackageSpeak, _originalSayAllSpeak
    global _originalSayAllSpeakWithoutPauses, _originalSayAllInitialize
    global _originalSpeechProcessText, _originalSpeechPackageProcessText
    global _originalSpeechGetSpellingSpeech, _originalSpeechPackageGetSpellingSpeech
    global _originalSpeechGetSingleCharDescription, _originalSpeechPackageGetSingleCharDescription
    global _originalShortcutKeysShouldUseSpellingFunctionality
    global _originalLanguageHandlingShouldMakeLangChangeCommand
    global _originalLanguageHandlingShouldSwitchVoice
    global _originalSpeechManagerShouldSwitchVoice
    global _patchedSpeechSpeak, _patchedSpeechPackageSpeak, _patchedSayAllSpeak
    global _patchedSayAllSpeakWithoutPauses, _patchedSayAllInitialize
    global _patchedSpeechProcessText, _patchedSpeechGetSpellingSpeech
    global _patchedSpeechGetSingleCharDescription
    global _patchedShortcutKeysShouldUseSpellingFunctionality
    global _patchedLanguageHandlingShouldMakeLangChangeCommand
    global _patchedLanguageHandlingShouldSwitchVoice
    global _patchedSpeechManagerShouldSwitchVoice
    global _autoLanguagePatchActive
    if _originalSpeechProcessText is not None:
        return
    _originalSpeechSpeak = getattr(speechModule, "speak", None)
    _originalSpeechPackageSpeak = getattr(speech, "speak", None)
    _originalSayAllInitialize = getattr(sayAllModule, "initialize", None) if sayAllModule is not None else None
    _originalSpeechProcessText = speechModule.processText
    _originalSpeechPackageProcessText = getattr(speech, "processText", None)
    _originalSpeechGetSpellingSpeech = speechModule.getSpellingSpeech
    _originalSpeechPackageGetSpellingSpeech = getattr(speech, "getSpellingSpeech", None)
    _originalSpeechGetSingleCharDescription = getattr(speechModule, "getSingleCharDescription", None)
    _originalSpeechPackageGetSingleCharDescription = getattr(speech, "getSingleCharDescription", None)
    _originalShortcutKeysShouldUseSpellingFunctionality = getattr(
        shortcutKeysModule,
        "shouldUseSpellingFunctionality",
        None,
    )
    _originalLanguageHandlingShouldMakeLangChangeCommand = (
        getattr(languageHandlingModule, "shouldMakeLangChangeCommand", None)
        if languageHandlingModule is not None
        else None
    )
    _originalLanguageHandlingShouldSwitchVoice = (
        getattr(languageHandlingModule, "shouldSwitchVoice", None) if languageHandlingModule is not None else None
    )
    _originalSpeechManagerShouldSwitchVoice = (
        getattr(speechManagerModule, "shouldSwitchVoice", None) if speechManagerModule is not None else None
    )
    originalSpeak = _originalSpeechSpeak
    originalProcessText = _originalSpeechProcessText
    originalGetSpellingSpeech = _originalSpeechGetSpellingSpeech
    originalGetSingleCharDescription = _originalSpeechGetSingleCharDescription
    originalShouldUseSpellingFunctionality = _originalShortcutKeysShouldUseSpellingFunctionality
    originalShouldMakeLangChangeCommand = _originalLanguageHandlingShouldMakeLangChangeCommand
    originalShouldSwitchVoice = _originalLanguageHandlingShouldSwitchVoice
    originalManagerShouldSwitchVoice = _originalSpeechManagerShouldSwitchVoice
    patchActive = [True]
    _autoLanguagePatchActive = patchActive

    def make_speak_with_auto_profile(targetSpeak: Any) -> Any:
        def wrapped_speak_with_auto_profile(*args: Any, **kwargs: Any) -> Any:
            global _autoProfileSpeakDepth
            if targetSpeak is None:
                return None
            if not patchActive[0] or not _google_auto_language_detection_active():
                return _call_compat(targetSpeak, *args, **kwargs)
            multipleProfiles = _google_auto_language_multiple_profiles_active()
            with _speechConfigOverlayLock, contextlib.ExitStack() as stack:
                try:
                    speechConfig = config.conf["speech"]
                    speechUpdates: dict[str, Any] = {}
                    if multipleProfiles and "autoDialectSwitching" in speechConfig:
                        speechUpdates["autoDialectSwitching"] = True
                    if languageHandlingModule is None and "autoLanguageSwitching" in speechConfig:
                        speechUpdates["autoLanguageSwitching"] = multipleProfiles
                    if speechUpdates:
                        stack.enter_context(_temporary_config_override(speechConfig, speechUpdates))
                except Exception:
                    log.debug("Could not apply Google TTS auto-language speech flags.", exc_info=True)
                _autoProfileSpeakDepth += 1
                _speakPipelineState.depth = getattr(_speakPipelineState, "depth", 0) + 1
                try:
                    return _call_compat(targetSpeak, *args, **kwargs)
                finally:
                    _speakPipelineState.depth = max(0, getattr(_speakPipelineState, "depth", 1) - 1)
                    _autoProfileSpeakDepth = max(0, _autoProfileSpeakDepth - 1)

        return _wrap_nvda_callable(wrapped_speak_with_auto_profile, targetSpeak)

    _defaultSpeakWithAutoProfile = make_speak_with_auto_profile(originalSpeak)

    def speak_with_auto_profile(*args: Any, **kwargs: Any) -> Any:
        return _defaultSpeakWithAutoProfile(*args, **kwargs)

    _wrap_nvda_callable(speak_with_auto_profile, originalSpeak)

    package_speak_with_auto_profile = (
        speak_with_auto_profile
        if _originalSpeechPackageSpeak is originalSpeak
        else make_speak_with_auto_profile(_originalSpeechPackageSpeak)
    )

    def make_speak_without_pauses_with_auto_profile(targetSpeakWithoutPauses: Any) -> Any:
        def wrapped_speak_without_pauses_with_auto_profile(*args: Any, **kwargs: Any) -> bool:
            if targetSpeakWithoutPauses is None:
                return False
            if not patchActive[0]:
                return bool(_call_compat(targetSpeakWithoutPauses, *args, **kwargs))
            speechSequence = _extract_named_arg(args, kwargs, "speechSequence", 0)
            newArgs = args
            newKwargs = kwargs
            if speechSequence is not None and _google_auto_language_detection_active():
                try:
                    coalesced = _strip_external_lang_changes_and_coalesce(list(speechSequence))
                    if "speechSequence" in kwargs:
                        newKwargs = dict(kwargs, speechSequence=coalesced)
                    elif args:
                        argsList = list(args)
                        argsList[0] = coalesced
                        newArgs = tuple(argsList)
                    else:
                        newArgs = (coalesced,)
                except Exception:
                    log.debug("Could not coalesce speech sequence for SayAll.", exc_info=True)
            return bool(_call_compat(targetSpeakWithoutPauses, *newArgs, **newKwargs))

        return _wrap_nvda_callable(wrapped_speak_without_pauses_with_auto_profile, targetSpeakWithoutPauses)

    def speak_without_pauses_with_auto_profile(*args: Any, **kwargs: Any) -> bool:
        if _patchedSayAllSpeakWithoutPauses is not None:
            return bool(_patchedSayAllSpeakWithoutPauses(*args, **kwargs))
        return False

    def process_text_with_auto_voice_dictionary(*args: Any, **kwargs: Any) -> str:
        if not patchActive[0]:
            return str(_call_compat(originalProcessText, *args, **kwargs))
        locale = _extract_named_arg(args, kwargs, "locale", 0)
        text = _extract_named_arg(args, kwargs, "text", 1)

        def call_original_with_locale(
            effectiveLocale: str | None = None,
            currentArgs: tuple[Any, ...] = args,
            currentKwargs: dict[str, Any] = kwargs,
        ) -> str:
            updatedArgs, updatedKwargs = (
                _replace_text_locale(
                    currentArgs,
                    currentKwargs,
                    textIndex=1,
                    localeIndex=0,
                    locale=effectiveLocale,
                )
                if effectiveLocale is not None
                else (currentArgs, currentKwargs)
            )
            return str(_call_compat(originalProcessText, *updatedArgs, **updatedKwargs))

        try:
            if not isinstance(locale, str) or not isinstance(text, str):
                return call_original_with_locale()
            synth = synthDriverHandler.getSynth()
            if getattr(synth, "name", "") != SYNTH_NAME:
                return call_original_with_locale()
            normalizedText = _normalize_text_for_auto_profile(text)
            effectiveArgs = args
            effectiveKwargs = kwargs
            if normalizedText != text:
                text = normalizedText
                effectiveArgs, effectiveKwargs = _replace_text_locale(
                    args,
                    kwargs,
                    textIndex=1,
                    localeIndex=0,
                    text=normalizedText,
                )
            if not synth._auto_language_detection_enabled():
                return call_original_with_locale(None, effectiveArgs, effectiveKwargs)
            targetLanguage = _auto_language_for_process_text(synth, locale, text)
            effectiveLocale = _nvda_locale_for_language(targetLanguage) or _nvda_locale_for_language(locale) or locale
            targetVariant = _auto_profile_variant_for_language(synth, targetLanguage or effectiveLocale)
            currentVariant = _current_google_tts_speaker_id(synth)
            restoreVoiceDict = False
            try:
                if targetVariant and targetVariant != currentVariant:
                    restoreVoiceDict = _load_voice_dictionary_for_voice(synth, targetVariant)
                return call_original_with_locale(effectiveLocale, effectiveArgs, effectiveKwargs)
            finally:
                if restoreVoiceDict:
                    try:
                        _load_voice_dictionary_for_voice(synth, currentVariant)
                    except Exception:
                        log.debug("Could not restore Google TTS voice dictionary.", exc_info=True)
        except Exception:
            log.debug("Could not apply Google TTS auto-language voice dictionary.", exc_info=True)
            return call_original_with_locale()

    _wrap_nvda_callable(process_text_with_auto_voice_dictionary, originalProcessText)

    def _iter_spelling_or_char_desc_with_auto_profile(
        targetFunc: Any,
        logLabel: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if targetFunc is None:
            return
        if not patchActive[0]:
            yield from _call_compat(targetFunc, *args, **kwargs)
            return
        rawText = _extract_named_arg(args, kwargs, "text", 0)
        rawLocale = _extract_named_arg(args, kwargs, "locale", 1)
        text = rawText
        try:
            synth = synthDriverHandler.getSynth()
            if isinstance(text, str) and getattr(synth, "name", "") == SYNTH_NAME:
                text = _normalize_text_for_auto_profile(text)
        except Exception:
            synth = None
        rawArgs, rawKwargs = (
            _replace_text_locale(args, kwargs, textIndex=0, localeIndex=1, text=text)
            if isinstance(text, str) and text != rawText
            else (args, kwargs)
        )
        context = _auto_profile_character_context_for_text(rawLocale, text)
        if not context:
            yield from _call_compat(targetFunc, *rawArgs, **rawKwargs)
            return
        settings, effectiveLocale = context
        multipleProfiles = _google_auto_language_multiple_profiles_active()
        try:
            if synth is None:
                synth = synthDriverHandler.getSynth()
            spellingLanguage = _auto_language_for_spelling_text(synth, text) or effectiveLocale
        except Exception:
            spellingLanguage = effectiveLocale
        effectiveArgs, effectiveKwargs = _replace_text_locale(
            rawArgs,
            rawKwargs,
            textIndex=0,
            localeIndex=1,
            locale=effectiveLocale if effectiveLocale != rawLocale else _UNSET,
        )
        items: list[Any] | None = None
        with _speechConfigOverlayLock:
            speechConfig: Any = None
            synthConfig: Any = None
            speechUpdates: dict[str, Any] = {}
            try:
                speechConfig = config.conf["speech"]
                synthConfig = speechConfig[SYNTH_NAME]
                if "autoDialectSwitching" in speechConfig:
                    speechUpdates["autoDialectSwitching"] = True
                if languageHandlingModule is None and "autoLanguageSwitching" in speechConfig:
                    speechUpdates["autoLanguageSwitching"] = multipleProfiles
            except Exception:
                log.debug("Could not apply Google TTS auto-language %s settings.", logLabel, exc_info=True)
            else:
                with (
                    _temporary_config_override(speechConfig, speechUpdates),
                    _temporary_config_override(synthConfig, dict(settings)),
                ):
                    items = list(_call_compat(targetFunc, *effectiveArgs, **effectiveKwargs))
        if items is None:
            yield from _call_compat(targetFunc, *rawArgs, **rawKwargs)
            return
        trailingEndUtterances: list[Any] = []
        while items and (
            (EndUtteranceCommand is not None and isinstance(items[-1], EndUtteranceCommand))
            or type(items[-1]).__name__ == "EndUtteranceCommand"
        ):
            trailingEndUtterances.append(items.pop())
        trailingEndUtterances.reverse()
        emittedLangCommand = False
        if multipleProfiles and spellingLanguage:
            yield _google_lang_change_command(spellingLanguage, spelling=True)
            yield _GoogleTtsProfileLangCommand(spellingLanguage, spelling=True)
            emittedLangCommand = True
        for item in items:
            if isinstance(item, LangChangeCommand):
                if multipleProfiles and spellingLanguage and not emittedLangCommand:
                    yield _google_lang_change_command(spellingLanguage, spelling=True)
                    yield _GoogleTtsProfileLangCommand(spellingLanguage, spelling=True)
                    emittedLangCommand = True
                continue
            yield item
        if emittedLangCommand:
            yield _google_lang_change_command(None)
            yield _GoogleTtsProfileLangCommand(None)
        yield from trailingEndUtterances

    def get_spelling_speech_with_auto_profile(*args: Any, **kwargs: Any) -> Any:
        yield from _iter_spelling_or_char_desc_with_auto_profile(
            originalGetSpellingSpeech,
            "spelling",
            *args,
            **kwargs,
        )

    _wrap_nvda_callable(get_spelling_speech_with_auto_profile, originalGetSpellingSpeech)

    def get_single_char_description_with_auto_profile(*args: Any, **kwargs: Any) -> Any:
        yield from _iter_spelling_or_char_desc_with_auto_profile(
            originalGetSingleCharDescription,
            "character description",
            *args,
            **kwargs,
        )

    _wrap_nvda_callable(get_single_char_description_with_auto_profile, originalGetSingleCharDescription)

    def should_use_spelling_functionality_with_auto_profile(*args: Any, **kwargs: Any) -> bool:
        settings = _single_auto_profile_character_settings() if patchActive[0] else None
        if settings is not None:
            if CharacterModeCommand is not None:
                try:
                    synth = synthDriverHandler.getSynth()
                    supportedCommands = getattr(synth, "supportedCommands", None)
                    if supportedCommands is not None and CharacterModeCommand not in supportedCommands:
                        return False
                except Exception:
                    pass
            return bool(settings.get("useSpellingFunctionality", True))
        if originalShouldUseSpellingFunctionality is not None:
            return bool(_call_compat(originalShouldUseSpellingFunctionality, *args, **kwargs))
        return True

    _wrap_nvda_callable(
        should_use_spelling_functionality_with_auto_profile,
        originalShouldUseSpellingFunctionality,
    )

    def should_make_lang_change_command_with_auto_profile(*args: Any, **kwargs: Any) -> bool:
        if patchActive[0] and _google_auto_language_detection_active():
            return getattr(_speakPipelineState, "depth", 0) > 0 and _google_auto_language_multiple_profiles_active()
        if originalShouldMakeLangChangeCommand is not None:
            return bool(_call_compat(originalShouldMakeLangChangeCommand, *args, **kwargs))
        return False

    _wrap_nvda_callable(
        should_make_lang_change_command_with_auto_profile,
        originalShouldMakeLangChangeCommand,
    )

    def should_switch_voice_with_auto_profile(*args: Any, **kwargs: Any) -> bool:
        if patchActive[0] and _google_auto_language_detection_active():
            return _google_auto_language_multiple_profiles_active()
        if originalShouldSwitchVoice is not None:
            return bool(_call_compat(originalShouldSwitchVoice, *args, **kwargs))
        if originalManagerShouldSwitchVoice is not None:
            return bool(_call_compat(originalManagerShouldSwitchVoice, *args, **kwargs))
        return False

    _wrap_nvda_callable(
        should_switch_voice_with_auto_profile,
        originalShouldSwitchVoice or originalManagerShouldSwitchVoice,
    )
    if originalManagerShouldSwitchVoice is None or originalManagerShouldSwitchVoice is originalShouldSwitchVoice:
        manager_should_switch_voice_with_auto_profile = should_switch_voice_with_auto_profile
    else:

        def manager_should_switch_voice_with_auto_profile(*args: Any, **kwargs: Any) -> bool:
            if patchActive[0] and _google_auto_language_detection_active():
                return _google_auto_language_multiple_profiles_active()
            return bool(_call_compat(originalManagerShouldSwitchVoice, *args, **kwargs))

        _wrap_nvda_callable(manager_should_switch_voice_with_auto_profile, originalManagerShouldSwitchVoice)

    def _patch_say_all_handler() -> None:
        global _originalSayAllSpeak, _originalSayAllSpeakWithoutPauses
        global _patchedSayAllSpeak, _patchedSayAllSpeakWithoutPauses
        sayAllSpeechWithoutPauses = (
            getattr(getattr(sayAllModule, "SayAllHandler", None), "speechWithoutPausesInstance", None)
            if sayAllModule is not None
            else None
        )
        if sayAllSpeechWithoutPauses is None:
            return
        currentSpeak = getattr(sayAllSpeechWithoutPauses, "speak", None)
        if currentSpeak is not None and currentSpeak is not _patchedSayAllSpeak:
            _originalSayAllSpeak = currentSpeak
            _patchedSayAllSpeak = (
                speak_with_auto_profile if currentSpeak is originalSpeak else make_speak_with_auto_profile(currentSpeak)
            )
            sayAllSpeechWithoutPauses.speak = _patchedSayAllSpeak
        currentSwp = getattr(sayAllSpeechWithoutPauses, "speakWithoutPauses", None)
        if currentSwp is not None and currentSwp is not _patchedSayAllSpeakWithoutPauses:
            _originalSayAllSpeakWithoutPauses = currentSwp
            _patchedSayAllSpeakWithoutPauses = make_speak_without_pauses_with_auto_profile(currentSwp)
            sayAllSpeechWithoutPauses.speakWithoutPauses = _patchedSayAllSpeakWithoutPauses

    _patchedSpeechSpeak = speak_with_auto_profile
    _patchedSpeechPackageSpeak = package_speak_with_auto_profile
    _patch_say_all_handler()
    _patchedSpeechProcessText = process_text_with_auto_voice_dictionary
    _patchedSpeechGetSpellingSpeech = get_spelling_speech_with_auto_profile
    _patchedSpeechGetSingleCharDescription = get_single_char_description_with_auto_profile
    _patchedShortcutKeysShouldUseSpellingFunctionality = should_use_spelling_functionality_with_auto_profile
    _patchedLanguageHandlingShouldMakeLangChangeCommand = should_make_lang_change_command_with_auto_profile
    _patchedLanguageHandlingShouldSwitchVoice = should_switch_voice_with_auto_profile
    _patchedSpeechManagerShouldSwitchVoice = manager_should_switch_voice_with_auto_profile
    if originalSpeak is not None:
        speechModule.speak = speak_with_auto_profile
    if _originalSpeechPackageSpeak is not None:
        speech.speak = package_speak_with_auto_profile
    if sayAllModule is not None and _originalSayAllInitialize is not None:

        def initialize_say_all_with_auto_profile(*args: Any, **kwargs: Any) -> Any:
            result = _call_compat(_originalSayAllInitialize, *args, **kwargs)
            if patchActive[0]:
                _patch_say_all_handler()
            return result

        _wrap_nvda_callable(initialize_say_all_with_auto_profile, _originalSayAllInitialize)
        _patchedSayAllInitialize = initialize_say_all_with_auto_profile
        sayAllModule.initialize = initialize_say_all_with_auto_profile
    speechModule.processText = process_text_with_auto_voice_dictionary
    if _originalSpeechPackageProcessText is not None:
        speech.processText = process_text_with_auto_voice_dictionary
    speechModule.getSpellingSpeech = get_spelling_speech_with_auto_profile
    if _originalSpeechPackageGetSpellingSpeech is not None:
        speech.getSpellingSpeech = get_spelling_speech_with_auto_profile
    if originalGetSingleCharDescription is not None:
        speechModule.getSingleCharDescription = get_single_char_description_with_auto_profile
    if _originalSpeechPackageGetSingleCharDescription is not None:
        speech.getSingleCharDescription = get_single_char_description_with_auto_profile
    if originalShouldUseSpellingFunctionality is not None:
        shortcutKeysModule.shouldUseSpellingFunctionality = should_use_spelling_functionality_with_auto_profile
    if languageHandlingModule is not None:
        if originalShouldMakeLangChangeCommand is not None:
            languageHandlingModule.shouldMakeLangChangeCommand = should_make_lang_change_command_with_auto_profile
        if originalShouldSwitchVoice is not None:
            languageHandlingModule.shouldSwitchVoice = should_switch_voice_with_auto_profile
    if speechManagerModule is not None and originalManagerShouldSwitchVoice is not None:
        speechManagerModule.shouldSwitchVoice = manager_should_switch_voice_with_auto_profile


def _unpatch_auto_language_voice_dictionary() -> None:
    global _originalSpeechSpeak, _originalSpeechPackageSpeak, _originalSayAllSpeak
    global _originalSayAllSpeakWithoutPauses, _originalSayAllInitialize
    global _originalSpeechProcessText, _originalSpeechPackageProcessText
    global _originalSpeechGetSpellingSpeech, _originalSpeechPackageGetSpellingSpeech
    global _originalSpeechGetSingleCharDescription, _originalSpeechPackageGetSingleCharDescription
    global _originalShortcutKeysShouldUseSpellingFunctionality
    global _originalLanguageHandlingShouldMakeLangChangeCommand
    global _originalLanguageHandlingShouldSwitchVoice
    global _originalSpeechManagerShouldSwitchVoice
    global _patchedSpeechSpeak, _patchedSpeechPackageSpeak, _patchedSayAllSpeak
    global _patchedSayAllSpeakWithoutPauses, _patchedSayAllInitialize
    global _patchedSpeechProcessText, _patchedSpeechGetSpellingSpeech
    global _patchedSpeechGetSingleCharDescription
    global _patchedShortcutKeysShouldUseSpellingFunctionality
    global _patchedLanguageHandlingShouldMakeLangChangeCommand
    global _patchedLanguageHandlingShouldSwitchVoice
    global _patchedSpeechManagerShouldSwitchVoice
    global _autoLanguagePatchActive
    if _originalSpeechProcessText is None:
        return
    if _autoLanguagePatchActive is not None:
        _autoLanguagePatchActive[0] = False
        _autoLanguagePatchActive = None
    if _originalSpeechSpeak is not None and getattr(speechModule, "speak", None) is _patchedSpeechSpeak:
        speechModule.speak = _originalSpeechSpeak
    if _originalSpeechPackageSpeak is not None and getattr(speech, "speak", None) is _patchedSpeechPackageSpeak:
        speech.speak = _originalSpeechPackageSpeak
    sayAllSpeechWithoutPauses = (
        getattr(getattr(sayAllModule, "SayAllHandler", None), "speechWithoutPausesInstance", None)
        if sayAllModule is not None
        else None
    )
    if (
        sayAllSpeechWithoutPauses is not None
        and _originalSayAllSpeak is not None
        and getattr(sayAllSpeechWithoutPauses, "speak", None) is _patchedSayAllSpeak
    ):
        sayAllSpeechWithoutPauses.speak = _originalSayAllSpeak
    if (
        sayAllSpeechWithoutPauses is not None
        and _originalSayAllSpeakWithoutPauses is not None
        and getattr(sayAllSpeechWithoutPauses, "speakWithoutPauses", None) is _patchedSayAllSpeakWithoutPauses
    ):
        sayAllSpeechWithoutPauses.speakWithoutPauses = _originalSayAllSpeakWithoutPauses
    if (
        sayAllModule is not None
        and _originalSayAllInitialize is not None
        and getattr(sayAllModule, "initialize", None) is _patchedSayAllInitialize
    ):
        sayAllModule.initialize = _originalSayAllInitialize
    if getattr(speechModule, "processText", None) is _patchedSpeechProcessText:
        speechModule.processText = _originalSpeechProcessText
    if (
        _originalSpeechPackageProcessText is not None
        and getattr(speech, "processText", None) is _patchedSpeechProcessText
    ):
        speech.processText = _originalSpeechPackageProcessText
    if getattr(speechModule, "getSpellingSpeech", None) is _patchedSpeechGetSpellingSpeech:
        speechModule.getSpellingSpeech = _originalSpeechGetSpellingSpeech
    if (
        _originalSpeechPackageGetSpellingSpeech is not None
        and getattr(speech, "getSpellingSpeech", None) is _patchedSpeechGetSpellingSpeech
    ):
        speech.getSpellingSpeech = _originalSpeechPackageGetSpellingSpeech
    if (
        _originalSpeechGetSingleCharDescription is not None
        and getattr(speechModule, "getSingleCharDescription", None) is _patchedSpeechGetSingleCharDescription
    ):
        speechModule.getSingleCharDescription = _originalSpeechGetSingleCharDescription
    if (
        _originalSpeechPackageGetSingleCharDescription is not None
        and getattr(speech, "getSingleCharDescription", None) is _patchedSpeechGetSingleCharDescription
    ):
        speech.getSingleCharDescription = _originalSpeechPackageGetSingleCharDescription
    if (
        _originalShortcutKeysShouldUseSpellingFunctionality is not None
        and getattr(shortcutKeysModule, "shouldUseSpellingFunctionality", None)
        is _patchedShortcutKeysShouldUseSpellingFunctionality
    ):
        shortcutKeysModule.shouldUseSpellingFunctionality = _originalShortcutKeysShouldUseSpellingFunctionality
    if languageHandlingModule is not None:
        if (
            _originalLanguageHandlingShouldMakeLangChangeCommand is not None
            and getattr(languageHandlingModule, "shouldMakeLangChangeCommand", None)
            is _patchedLanguageHandlingShouldMakeLangChangeCommand
        ):
            languageHandlingModule.shouldMakeLangChangeCommand = _originalLanguageHandlingShouldMakeLangChangeCommand
        if (
            _originalLanguageHandlingShouldSwitchVoice is not None
            and getattr(languageHandlingModule, "shouldSwitchVoice", None) is _patchedLanguageHandlingShouldSwitchVoice
        ):
            languageHandlingModule.shouldSwitchVoice = _originalLanguageHandlingShouldSwitchVoice
    if (
        speechManagerModule is not None
        and _originalSpeechManagerShouldSwitchVoice is not None
        and getattr(speechManagerModule, "shouldSwitchVoice", None) is _patchedSpeechManagerShouldSwitchVoice
    ):
        speechManagerModule.shouldSwitchVoice = _originalSpeechManagerShouldSwitchVoice
    _originalSpeechSpeak = None
    _originalSpeechPackageSpeak = None
    _originalSayAllSpeak = None
    _originalSayAllSpeakWithoutPauses = None
    _originalSayAllInitialize = None
    _originalSpeechProcessText = None
    _originalSpeechPackageProcessText = None
    _originalSpeechGetSpellingSpeech = None
    _originalSpeechPackageGetSpellingSpeech = None
    _originalSpeechGetSingleCharDescription = None
    _originalSpeechPackageGetSingleCharDescription = None
    _originalShortcutKeysShouldUseSpellingFunctionality = None
    _originalLanguageHandlingShouldMakeLangChangeCommand = None
    _originalLanguageHandlingShouldSwitchVoice = None
    _originalSpeechManagerShouldSwitchVoice = None
    _patchedSpeechSpeak = None
    _patchedSpeechPackageSpeak = None
    _patchedSayAllSpeak = None
    _patchedSayAllSpeakWithoutPauses = None
    _patchedSayAllInitialize = None
    _patchedSpeechProcessText = None
    _patchedSpeechGetSpellingSpeech = None
    _patchedSpeechGetSingleCharDescription = None
    _patchedShortcutKeysShouldUseSpellingFunctionality = None
    _patchedLanguageHandlingShouldMakeLangChangeCommand = None
    _patchedLanguageHandlingShouldSwitchVoice = None
    _patchedSpeechManagerShouldSwitchVoice = None


def _close_voice_manager() -> None:
    global _dialog
    if _dialog is None:
        return
    try:
        prepare = getattr(_dialog, "prepare_for_shutdown", None)
        if callable(prepare):
            prepare()
        _dialog.Destroy()
    except RuntimeError:
        pass
    finally:
        _dialog = None


def _open_google_tts_settings() -> None:
    if is_secure_mode():
        return
    mainFrame = getattr(gui, "mainFrame", None)
    settingsDialogCls = getattr(getattr(gui, "settingsDialogs", None), "NVDASettingsDialog", None)
    if (
        mainFrame is not None
        and callable(getattr(mainFrame, "popupSettingsDialog", None))
        and settingsDialogCls is not None
    ):
        mainFrame.popupSettingsDialog(settingsDialogCls, GoogleTtsSettingsPanel)


def _refresh_standby_runtime(reason: str) -> None:
    try:
        if getattr(synthDriverHandler.getSynth(), "name", "") == SYNTH_NAME:
            standby.note_synth_active()
        standby.refresh_async(reason)
    except Exception:
        log.debug("Could not refresh Google TTS standby browser runtime.", exc_info=True)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("Google TTS For NVDA")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _call_compat(super().__init__, *args, **kwargs)
        ensure_config_spec()
        self.voiceManagerMenuItem: wx.MenuItem | None = None
        self._startupUpdateCheckRegistered = False
        if not is_secure_mode():
            standby.initialize()
            _patch_synth_selection()
            _patch_read_only_text_setting()
            _patch_voice_dictionary_dialog()
            _patch_google_tts_voice_dictionary_loading()
            _patch_auto_language_voice_dictionary()
            _register_auto_language_speech_filter()
            try:
                updater.cleanup_update_files()
            except Exception:
                log.debug("Could not clean temporary Google TTS For NVDA update files.", exc_info=True)
            try:
                import core

                core.postNvdaStartup.register(self._on_post_nvda_startup)
                self._startupUpdateCheckRegistered = True
            except Exception:
                log.debug("Could not register Google TTS For NVDA automatic update check.", exc_info=True)
            settingsDialogCls = getattr(getattr(gui, "settingsDialogs", None), "NVDASettingsDialog", None)
            categoryClasses = getattr(settingsDialogCls, "categoryClasses", None)
            if isinstance(categoryClasses, list) and GoogleTtsSettingsPanel not in categoryClasses:
                categoryClasses.append(GoogleTtsSettingsPanel)
            mainFrame = getattr(gui, "mainFrame", None)
            sysTrayIcon = getattr(mainFrame, "sysTrayIcon", None) if mainFrame is not None else None
            toolsMenu = getattr(sysTrayIcon, "toolsMenu", None) if sysTrayIcon is not None else None
            if sysTrayIcon is not None and toolsMenu is not None:
                with contextlib.suppress(RuntimeError, AttributeError, TypeError):
                    self.voiceManagerMenuItem = toolsMenu.Append(
                        wx.ID_ANY,
                        _("Google TTS Voice Manager..."),
                        _("Download or remove Google TTS For NVDA voice packages"),
                    )
                    if self.voiceManagerMenuItem is not None:
                        sysTrayIcon.Bind(wx.EVT_MENU, self.on_open_voice_manager, self.voiceManagerMenuItem)

    def _on_post_nvda_startup(self, *args: Any, **kwargs: Any) -> None:
        try:
            updateGui.start_automatic_update_check()
        except Exception:
            log.debug("Could not start Google TTS For NVDA automatic update check.", exc_info=True)
        _refresh_standby_runtime("NVDA startup")

    def terminate(self, *args: Any, **kwargs: Any) -> None:
        _close_voice_manager()
        with contextlib.suppress(Exception):
            settingsModule.cancel_pending_runtime_change()
        try:
            standby.terminate()
        except Exception:
            log.debug("Could not terminate Google TTS standby browser runtime.", exc_info=True)
        if self._startupUpdateCheckRegistered:
            try:
                import core

                core.postNvdaStartup.unregister(self._on_post_nvda_startup)
            except Exception:
                log.debug("Could not unregister Google TTS For NVDA automatic update check.", exc_info=True)
            self._startupUpdateCheckRegistered = False
        settingsDialogCls = getattr(getattr(gui, "settingsDialogs", None), "NVDASettingsDialog", None)
        categoryClasses = getattr(settingsDialogCls, "categoryClasses", None)
        if isinstance(categoryClasses, list):
            with contextlib.suppress(ValueError):
                categoryClasses.remove(GoogleTtsSettingsPanel)
        if self.voiceManagerMenuItem is not None:
            mainFrame = getattr(gui, "mainFrame", None)
            sysTrayIcon = getattr(mainFrame, "sysTrayIcon", None) if mainFrame is not None else None
            toolsMenu = getattr(sysTrayIcon, "toolsMenu", None) if sysTrayIcon is not None else None
            getId = getattr(self.voiceManagerMenuItem, "GetId", None)
            menuItemId = getId() if callable(getId) else getattr(self.voiceManagerMenuItem, "Id", None)
            if sysTrayIcon is not None:
                with contextlib.suppress(RuntimeError, AttributeError, TypeError):
                    sysTrayIcon.Unbind(wx.EVT_MENU, source=self.voiceManagerMenuItem)
            if menuItemId is not None and toolsMenu is not None:
                with contextlib.suppress(RuntimeError, AttributeError, TypeError):
                    findItem = getattr(toolsMenu, "FindItemById", None)
                    if not callable(findItem) or findItem(menuItemId) is not None:
                        destroyItem = getattr(toolsMenu, "DestroyItem", None)
                        if callable(destroyItem):
                            destroyItem(self.voiceManagerMenuItem)
                        else:
                            toolsMenu.Remove(menuItemId)
            self.voiceManagerMenuItem = None
        _unpatch_synth_selection()
        _unpatch_read_only_text_setting()
        _unpatch_voice_dictionary_dialog()
        _unpatch_google_tts_voice_dictionary_loading()
        _unregister_auto_language_speech_filter()
        _unpatch_auto_language_voice_dictionary()
        _call_compat(super().terminate, *args, **kwargs)

    def on_open_voice_manager(self, evt: Any = None, *args: Any, **kwargs: Any) -> None:
        _open_voice_manager()

    def script_openVoiceManager(self, gesture: Any = None, *args: Any, **kwargs: Any) -> None:
        _open_voice_manager()

    script_openVoiceManager.__doc__ = _("Opens the Google TTS Voice Manager.")

    def script_openSettings(self, gesture: Any = None, *args: Any, **kwargs: Any) -> None:
        _open_google_tts_settings()

    script_openSettings.__doc__ = _("Opens the Google TTS For NVDA settings.")

    __gestures = {
        "kb:NVDA+control+shift+g": "openVoiceManager",
    }
