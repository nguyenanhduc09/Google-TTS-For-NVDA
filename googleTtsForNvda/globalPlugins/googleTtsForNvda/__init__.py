# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import os
import threading
import webbrowser
from typing import Any

import addonHandler
import config
import globalPluginHandler
import globalVars
import gui
from gui import guiHelper
import speech.extensions
import speech.shortcutKeys as shortcutKeysModule
from speech import speech as speechModule
from speech.commands import LangChangeCommand
import speechDictHandler
import synthDriverHandler
import wx
from autoSettingsUtils.driverSetting import BooleanDriverSetting, NumericDriverSetting
from logHandler import log

from synthDrivers.googleTtsForNvda.bridge import (
	CONFIG_AUTO_LANGUAGE_CANDIDATES,
	CONFIG_AUTO_LANGUAGE_DETECTION,
	CONFIG_AUTO_LANGUAGE_PREFERRED,
	CONFIG_AUTO_LANGUAGE_PROFILES,
	CONFIG_BROWSER_RUNTIME,
	CONFIG_SECTION,
	DEFAULT_AUTO_LANGUAGE_CANDIDATES,
	DEFAULT_AUTO_LANGUAGE_DETECTION,
	DEFAULT_AUTO_LANGUAGE_PREFERRED,
	DEFAULT_AUTO_LANGUAGE_PROFILES,
	DEFAULT_BROWSER_RUNTIME,
	edge_webview2_blocks_effective_runtime,
)
from synthDrivers.googleTtsForNvda.catalog import EngineLibraryError, VoiceCatalog
from synthDrivers.googleTtsForNvda import voice_store

from . import updater, updateGui
from .settings import GoogleTtsSettingsPanel
from .uiUtils import bind_read_only_text_focus_announcement
from .voiceManager import VoiceManagerDialog


addonHandler.initTranslation()

config.conf.spec[CONFIG_SECTION] = {
	CONFIG_AUTO_LANGUAGE_CANDIDATES: f"string(default={DEFAULT_AUTO_LANGUAGE_CANDIDATES})",
	CONFIG_AUTO_LANGUAGE_DETECTION: f"boolean(default={str(DEFAULT_AUTO_LANGUAGE_DETECTION).lower()})",
	CONFIG_AUTO_LANGUAGE_PREFERRED: f"string(default={DEFAULT_AUTO_LANGUAGE_PREFERRED})",
	CONFIG_AUTO_LANGUAGE_PROFILES: f"string(default={DEFAULT_AUTO_LANGUAGE_PROFILES})",
	updateGui.CONFIG_AUTO_UPDATE_CHECK: f"boolean(default={str(updateGui.DEFAULT_AUTO_UPDATE_CHECK).lower()})",
	CONFIG_BROWSER_RUNTIME: f"string(default={DEFAULT_BROWSER_RUNTIME})",
}

SYNTH_NAME = "googleTtsForNvda"
_AUTO_LANGUAGE_NOTICE_ID = "notice"
_dialog: VoiceManagerDialog | None = None
_originalSetSynth: Any | None = None
_originalSettingsDialogSetSynth: Any | None = None
_originalAutoSettingsGetSettingMaker: Any | None = None
_originalAutoSettingsUpdateValueForControl: Any | None = None
_originalAutoSettingsOnDiscard: Any | None = None
_originalAutoSettingsRefreshGui: Any | None = None
_originalVoiceSettingsMakeSettings: Any | None = None
_originalSpeechProcessText: Any | None = None
_originalSpeechGetSpellingSpeech: Any | None = None
_originalShortcutKeysShouldUseSpellingFunctionality: Any | None = None
_originalSpeechDictLoadVoiceDict: Any | None = None
_originalPopupSettingsDialog: Any | None = None
_patchedAutoSettingsGetSettingMaker: Any | None = None
_patchedAutoSettingsUpdateValueForControl: Any | None = None
_patchedAutoSettingsOnDiscard: Any | None = None
_patchedAutoSettingsRefreshGui: Any | None = None
_patchedVoiceSettingsMakeSettings: Any | None = None
_patchedSpeechProcessText: Any | None = None
_patchedSpeechGetSpellingSpeech: Any | None = None
_patchedShortcutKeysShouldUseSpellingFunctionality: Any | None = None
_patchedSpeechDictLoadVoiceDict: Any | None = None
_patchedPopupSettingsDialog: Any | None = None
_autoLanguageSpeechFilterRegistered = False
_missingVoicesPromptActive = False
_edgeWebView2PromptActive = False
_speechConfigOverlayLock = threading.RLock()
_EDGE_WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
_EDGE_WEBVIEW2_DOWNLOAD_PAGE_URL = "https://developer.microsoft.com/microsoft-edge/webview2"


def _call_set_synth_compat(
	setSynth: Any,
	name: str | None,
	isFallback: bool = False,
	_leftToTry: list[str] | None = None,
) -> bool:
	try:
		signature = inspect.signature(setSynth)
	except (TypeError, ValueError):
		try:
			return setSynth(name, isFallback, _leftToTry)
		except TypeError as exc:
			if "_leftToTry" not in str(exc):
				raise
		try:
			return setSynth(name, isFallback)
		except TypeError as exc:
			if "isFallback" not in str(exc):
				raise
		return setSynth(name)

	parameters = signature.parameters
	acceptsVarargs = any(
		parameter.kind == inspect.Parameter.VAR_POSITIONAL
		for parameter in parameters.values()
	)
	acceptsKwargs = any(
		parameter.kind == inspect.Parameter.VAR_KEYWORD
		for parameter in parameters.values()
	)
	positionalArgs: list[Any] = [name]
	kwargs: dict[str, Any] = {}
	for parameterName, value in (
		("isFallback", isFallback),
		("_leftToTry", _leftToTry),
	):
		if acceptsVarargs and parameterName not in parameters and not acceptsKwargs:
			positionalArgs.append(value)
			continue
		if acceptsKwargs:
			kwargs[parameterName] = value
			continue
		parameter = parameters.get(parameterName)
		if parameter is None:
			continue
		if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
			positionalArgs.append(value)
		else:
			kwargs[parameterName] = value
	return setSynth(*positionalArgs, **kwargs)


def _normalize_set_synth_args(
	args: tuple[Any, ...],
	kwargs: dict[str, Any],
) -> tuple[str | None, bool, list[str] | None]:
	kwargs = dict(kwargs)
	name = None
	if args:
		name = args[0]
	elif "name" in kwargs:
		name = kwargs.pop("name")
	elif "synthName" in kwargs:
		name = kwargs.pop("synthName")
	else:
		raise TypeError("setSynth() missing required argument: 'name'")
	if len(args) > 3:
		raise TypeError(f"setSynth() takes at most 3 positional arguments ({len(args)} given)")
	isFallback = False
	_leftToTry = None
	if len(args) >= 2:
		if "isFallback" in kwargs:
			raise TypeError("setSynth() got multiple values for argument 'isFallback'")
		isFallback = args[1]
	if len(args) >= 3:
		if "_leftToTry" in kwargs:
			raise TypeError("setSynth() got multiple values for argument '_leftToTry'")
		_leftToTry = args[2]
	for key in kwargs:
		if key not in {"isFallback", "_leftToTry"}:
			raise TypeError(f"setSynth() got an unexpected keyword argument '{key}'")
	if "isFallback" in kwargs:
		isFallback = kwargs["isFallback"]
	if "_leftToTry" in kwargs:
		_leftToTry = kwargs["_leftToTry"]
	return name, isFallback, _leftToTry


def _clear_dialog_reference(dialog: VoiceManagerDialog) -> None:
	global _dialog
	if _dialog is dialog:
		_dialog = None


def _open_voice_manager(initialPage: str = "installed") -> None:
	global _dialog
	if _dialog is not None:
		try:
			if _dialog.IsShown():
				if initialPage == "download":
					_dialog.show_download_tab()
				_dialog.Raise()
				_dialog.focus_default_control()
				return
		except RuntimeError:
			_dialog = None
	gui.mainFrame.prePopup()
	try:
		try:
			_dialog = VoiceManagerDialog(gui.mainFrame, _clear_dialog_reference, initialPage=initialPage)
		except EngineLibraryError as exc:
			_show_engine_library_error(exc)
			return
		_dialog.Show()
	finally:
		gui.mainFrame.postPopup()


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


def _engine_library_error_message(error: EngineLibraryError) -> str:
	if error.kind == "unsupportedVersion":
		found = ", ".join(error.foundVersions) if error.foundVersions else _("another version")
		return _(
			"Google TTS For NVDA could not be loaded because the WASM TTS Engine version is not supported.\n\n"
			"This add-on supports WASM TTS Engine version {supported}, but found: {found}.\n\n"
			"Install a Google TTS For NVDA package that includes the supported WASM TTS Engine."
		).format(supported=error.supportedVersion, found=found)
	if error.kind == "missing":
		return _(
			"Google TTS For NVDA could not be loaded because the WASM TTS Engine library is missing.\n\n"
			"Reinstall Google TTS For NVDA with the included WASM TTS Engine library."
		)
	if error.kind == "incomplete":
		return _(
			"Google TTS For NVDA could not be loaded because the WASM TTS Engine library is incomplete.\n\n"
			"Reinstall Google TTS For NVDA with the complete WASM TTS Engine library."
		)
	return _(
		"Google TTS For NVDA could not be loaded because the WASM TTS Engine voice catalog could not be read.\n\n"
		"Reinstall Google TTS For NVDA with a supported WASM TTS Engine library."
	)


def _show_engine_library_error(error: EngineLibraryError) -> None:
	log.error("Google TTS WASM TTS Engine error: %s", error.technicalDetail)
	gui.messageBox(
		_engine_library_error_message(error),
		_("Google TTS For NVDA"),
		wx.OK | wx.ICON_ERROR,
		gui.mainFrame,
	)


def _show_missing_voices_prompt(message: str | None = None) -> None:
	global _missingVoicesPromptActive
	if _missingVoicesPromptActive:
		return
	_missingVoicesPromptActive = True
	try:
		answer = gui.messageBox(
			message or _(
				"No Google TTS For NVDA voices are installed.\n\n"
				"Press OK to open Google TTS Voice Manager and download a voice package.\n"
				"Press Cancel to keep using your current synthesizer for now.\n\n"
				"You can also open Voice Manager later from NVDA Menu > Tools > "
				"Google TTS Voice Manager, or press NVDA+Ctrl+Shift+G."
			),
			_("Google TTS For NVDA"),
			wx.OK | wx.CANCEL | wx.ICON_INFORMATION,
			gui.mainFrame,
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
	gui.mainFrame.prePopup()
	try:
		dialog = wx.Dialog(gui.mainFrame, title=_("Google TTS For NVDA"))
		try:
			mainSizer = wx.BoxSizer(wx.VERTICAL)
			helper = guiHelper.BoxSizerHelper(dialog, sizer=mainSizer)
			message = wx.StaticText(
				dialog,
				label=_("The download page could not be opened. Please visit this address manually:"),
			)
			message.Wrap(520)
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
					gui.messageBox(
						_("The link was copied to the clipboard."),
						_("Google TTS For NVDA"),
						wx.OK | wx.ICON_INFORMATION,
						dialog,
					)
				else:
					gui.messageBox(
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
		gui.mainFrame.postPopup()


def show_edge_webview2_prompt() -> None:
	global _edgeWebView2PromptActive
	if _edgeWebView2PromptActive:
		return
	_edgeWebView2PromptActive = True
	try:
		gui.mainFrame.prePopup()
		result = wx.ID_CANCEL
		try:
			dialog = wx.Dialog(gui.mainFrame, title=_("Microsoft Edge WebView2 Runtime is needed"))
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
				message.Wrap(520)
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
			gui.mainFrame.postPopup()
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
	name, isFallback, _leftToTry = _normalize_set_synth_args(args, kwargs)
	# Keep the current synthesizer active so NVDA can speak the prompt instead of
	# showing its generic "could not load synthesizer" error first.
	if (
		name == SYNTH_NAME
		and not isFallback
	):
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
	if _originalSetSynth is None:
		return False
	return _call_set_synth_compat(_originalSetSynth, name, isFallback, _leftToTry)


def _patch_synth_selection() -> None:
	global _originalSetSynth, _originalSettingsDialogSetSynth
	if _originalSetSynth is not None:
		return
	_originalSetSynth = synthDriverHandler.setSynth
	synthDriverHandler.setSynth = _set_synth_with_google_tts_voice_prompt
	settingsDialogs = getattr(gui, "settingsDialogs", None)
	if settingsDialogs is not None and hasattr(settingsDialogs, "setSynth"):
		_originalSettingsDialogSetSynth = settingsDialogs.setSynth
		settingsDialogs.setSynth = _set_synth_with_google_tts_voice_prompt


def _unpatch_synth_selection() -> None:
	global _originalSetSynth, _originalSettingsDialogSetSynth
	if _originalSetSynth is None:
		return
	synthDriverHandler.setSynth = _originalSetSynth
	_originalSetSynth = None
	settingsDialogs = getattr(gui, "settingsDialogs", None)
	if settingsDialogs is not None and _originalSettingsDialogSetSynth is not None:
		settingsDialogs.setSynth = _originalSettingsDialogSetSynth
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
		self.bindHelpEvent(getattr(self, "_getSettingControlHelpId")(setting.id), edit)
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
	if settingsStorage is not None and getattr(settingsStorage, "name", "") != SYNTH_NAME:
		return False
	return True


def _hide_google_tts_auto_profile_speech_controls(panel: Any) -> None:
	if not _google_auto_language_detection_active():
		return
	for controlName in (
		"capPitchChangeEdit",
		"sayCapForCapsCheckBox",
		"beepForCapsCheckBox",
		"useSpellingFunctionalityCheckBox",
	):
		control = getattr(panel, controlName, None)
		if control is None:
			continue
		try:
			if controlName == "capPitchChangeEdit":
				sizer = control.GetContainingSizer()
				if sizer is not None:
					sizer.ShowItems(False)
					continue
			control.Hide()
			control.Disable()
		except Exception:
			log.debug("Could not hide Google TTS auto-profile speech setting.", exc_info=True)
	try:
		panel.Layout()
		parent = panel.GetParent()
		if parent is not None:
			parent.Layout()
	except Exception:
		log.debug("Could not relayout Google TTS Speech Settings controls.", exc_info=True)


def _patch_read_only_text_setting() -> None:
	global _originalAutoSettingsGetSettingMaker, _originalAutoSettingsUpdateValueForControl, _originalAutoSettingsOnDiscard
	global _originalAutoSettingsRefreshGui
	global _originalVoiceSettingsMakeSettings
	global _patchedAutoSettingsGetSettingMaker, _patchedAutoSettingsUpdateValueForControl, _patchedAutoSettingsOnDiscard
	global _patchedAutoSettingsRefreshGui
	global _patchedVoiceSettingsMakeSettings
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

	def _get_setting_maker(self: Any, setting: Any, *args: Any, **kwargs: Any) -> Any:
		if _is_google_tts_read_only_setting(setting):
			def _make_control(
				setting: Any,
				settingsStorage: Any,
				*controlArgs: Any,
				**controlKwargs: Any,
			) -> wx.BoxSizer:
				if not _is_google_tts_read_only_setting(setting, settingsStorage):
					return originalGetSettingMaker(self, setting, *args, **kwargs)(
						setting,
						settingsStorage,
						*controlArgs,
						**controlKwargs,
					)
				return _make_read_only_text_setting_control(self, setting, settingsStorage)

			return _make_control
		return originalGetSettingMaker(self, setting, *args, **kwargs)

	def _update_value_for_control(
		self: Any,
		setting: Any,
		settingsStorage: Any = None,
		*args: Any,
		**kwargs: Any,
	) -> None:
		originalKwargs = dict(kwargs)
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
		if getattr(settingsStorage, "name", "") == SYNTH_NAME and getattr(setting, "id", "") == "variant":
			try:
				options = list(getattr(settingsStorage, "availableVariants").values())
				control = getattr(self, "variantList", None)
				if control is not None:
					currentOptions = getattr(self, "_variants", [])
					if [option.id for option in currentOptions] != [option.id for option in options]:
						setattr(self, "_variants", options)
						control.Clear()
						for option in options:
							control.Append(option.displayName)
			except Exception:
				log.debug("Could not refresh Google TTS variant choices.", exc_info=True)
		return originalUpdateValueForControl(self, setting, settingsStorage, *args, **originalKwargs)

	def _on_discard(self: Any, *args: Any, **kwargs: Any) -> None:
		try:
			settingsInst = self.getSettings()
		except Exception:
			return originalOnDiscard(self, *args, **kwargs)
		if getattr(settingsInst, "name", "") != SYNTH_NAME:
			return originalOnDiscard(self, *args, **kwargs)
		for setting in getattr(settingsInst, "supportedSettings", ()):
			if isinstance(setting, (NumericDriverSetting, BooleanDriverSetting)):
				continue
			control = getattr(self, f"{setting.id}List", None)
			if control is None:
				continue
			try:
				control.Unbind(wx.EVT_CHOICE)
			except Exception:
				log.debug("Could not unbind Google TTS speech setting control.", exc_info=True)
		settingsInst.loadSettings()

	def _refresh_gui(self: Any, *args: Any, **kwargs: Any) -> None:
		try:
			return originalRefreshGui(self, *args, **kwargs)
		except RuntimeError as exc:
			if "has been deleted" not in str(exc):
				raise
			log.debug("Ignoring refresh for destroyed NVDA auto settings panel.", exc_info=True)

	def _voice_make_settings(self: Any, settingsSizer: wx.Sizer, *args: Any, **kwargs: Any) -> None:
		originalVoiceMakeSettings(self, settingsSizer, *args, **kwargs)
		_hide_google_tts_auto_profile_speech_controls(self)

	_patchedAutoSettingsGetSettingMaker = _get_setting_maker
	_patchedAutoSettingsUpdateValueForControl = _update_value_for_control
	_patchedAutoSettingsOnDiscard = _on_discard
	_patchedAutoSettingsRefreshGui = _refresh_gui
	autoSettingsMixin._getSettingMaker = _get_setting_maker
	autoSettingsMixin._updateValueForControl = _update_value_for_control
	autoSettingsMixin.onDiscard = _on_discard
	autoSettingsMixin.refreshGui = _refresh_gui
	if voiceSettingsPanel is not None and originalVoiceMakeSettings is not None:
		_patchedVoiceSettingsMakeSettings = _voice_make_settings
		voiceSettingsPanel.makeSettings = _voice_make_settings


def _unpatch_read_only_text_setting() -> None:
	global _originalAutoSettingsGetSettingMaker, _originalAutoSettingsUpdateValueForControl, _originalAutoSettingsOnDiscard
	global _originalAutoSettingsRefreshGui
	global _originalVoiceSettingsMakeSettings
	global _patchedAutoSettingsGetSettingMaker, _patchedAutoSettingsUpdateValueForControl, _patchedAutoSettingsOnDiscard
	global _patchedAutoSettingsRefreshGui
	global _patchedVoiceSettingsMakeSettings
	if _originalAutoSettingsGetSettingMaker is None:
		return
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


def _show_voice_dictionary_auto_language_message() -> None:
	gui.messageBox(
		_(
			"Voice dictionary preferences are unavailable while automatic language profiles are enabled.\n\n"
			"Google TTS For NVDA may switch between several profile variants while speaking, so NVDA cannot "
			"know which single variant voice dictionary to edit.\n\n"
			"Open the Google TTS For NVDA category in NVDA Settings and turn off automatic language profiles, "
			"then open Voice dictionary again."
		),
		_("Google TTS For NVDA"),
		wx.OK | wx.ICON_INFORMATION,
		gui.mainFrame,
	)


def _patch_voice_dictionary_dialog() -> None:
	global _originalPopupSettingsDialog, _patchedPopupSettingsDialog
	if _originalPopupSettingsDialog is not None:
		return
	mainFrame = getattr(gui, "mainFrame", None)
	if mainFrame is None or not hasattr(mainFrame, "popupSettingsDialog"):
		return
	_originalPopupSettingsDialog = mainFrame.popupSettingsDialog
	originalPopupSettingsDialog = _originalPopupSettingsDialog

	def popup_settings_dialog(dialog: Any, *args: Any, **kwargs: Any) -> Any:
		if dialog is getattr(gui, "VoiceDictionaryDialog", None) and _google_auto_language_detection_active():
			_show_voice_dictionary_auto_language_message()
			return None
		return originalPopupSettingsDialog(dialog, *args, **kwargs)

	_patchedPopupSettingsDialog = popup_settings_dialog
	mainFrame.popupSettingsDialog = popup_settings_dialog


def _unpatch_voice_dictionary_dialog() -> None:
	global _originalPopupSettingsDialog, _patchedPopupSettingsDialog
	if _originalPopupSettingsDialog is None:
		return
	mainFrame = getattr(gui, "mainFrame", None)
	if mainFrame is not None and getattr(mainFrame, "popupSettingsDialog", None) is _patchedPopupSettingsDialog:
		mainFrame.popupSettingsDialog = _originalPopupSettingsDialog
	_originalPopupSettingsDialog = None
	_patchedPopupSettingsDialog = None


def _normalize_language_key(language: str | None) -> str:
	return str(language or "").replace("_", "-").lower()


def _language_match_keys(language: str | None) -> set[str]:
	key = _normalize_language_key(language)
	if not key:
		return set()
	aliases = {key}
	aliasMap = {
		"cmn-cn": {"zh-cn"},
		"zh-cn": {"cmn-cn"},
		"cmn-tw": {"zh-tw"},
		"zh-tw": {"cmn-tw"},
		"yue-hk": {"zh-hk"},
		"zh-hk": {"yue-hk"},
		"zh": {"cmn-cn", "cmn-tw", "yue-hk"},
		"fil-ph": {"tl", "fil"},
		"tl": {"fil-ph", "fil"},
		"ar-xa": {"ar"},
		"ar": {"ar-xa"},
	}
	aliases.update(aliasMap.get(key, set()))
	if key.startswith("fil-"):
		aliases.update({"fil", "tl"})
	return aliases


def _same_language(left: str | None, right: str | None) -> bool:
	leftKeys = _language_match_keys(left)
	rightKeys = _language_match_keys(right)
	return bool(leftKeys and rightKeys and leftKeys.intersection(rightKeys))


def _google_lang_change_command(language: str | None) -> LangChangeCommand:
	command = LangChangeCommand(_nvda_locale_for_language(language))
	try:
		setattr(command, "googleTtsForNvdaLanguage", language)
	except Exception:
		log.debug("Could not preserve Google TTS language code on LangChangeCommand.", exc_info=True)
	return command


def _normalize_nvda_locale_for_language(language: str | None) -> str | None:
	if not language:
		return language
	languageText = str(language)
	languageMap = {
		"cmn-CN": "zh_CN",
		"cmn-TW": "zh_TW",
		"yue-HK": "zh_HK",
		"ar-XA": "ar",
		"fil-PH": "tl",
	}
	if languageText in languageMap:
		return languageMap[languageText]
	lowerLanguage = languageText.lower()
	if lowerLanguage.startswith("cmn"):
		return "zh_CN"
	if lowerLanguage.startswith("yue"):
		return "zh_HK"
	return languageText.replace("-", "_")


def _nvda_locale_exists(locale: str) -> bool:
	try:
		return os.path.isdir(os.path.join(globalVars.appDir, "locale", locale))
	except Exception:
		return False


def _nvda_locale_for_language(language: str | None) -> str | None:
	locale = _normalize_nvda_locale_for_language(language)
	if not locale:
		return locale
	if _nvda_locale_exists(locale):
		return locale
	rootLocale = locale.split("_", 1)[0]
	if rootLocale != locale and _nvda_locale_exists(rootLocale):
		return rootLocale
	return "en"


def _nvda_uses_lang_change_commands() -> bool:
	try:
		if bool(config.conf["speech"]["autoLanguageSwitching"]):
			return True
	except Exception:
		pass
	try:
		return bool(config.conf["speech"]["reportLanguage"])
	except Exception:
		return False


def _auto_language_candidate_for_locale(synth: Any, locale: str | None, candidates: list[str]) -> str | None:
	if not locale or not candidates:
		return None
	localeKeys = _language_match_keys(locale)
	for candidate in candidates:
		if _language_match_keys(candidate).intersection(localeKeys):
			return candidate
	localeRoot = str(locale or "").replace("_", "-").split("-", 1)[0].lower()
	for candidate in candidates:
		if synth._language_root(candidate) == localeRoot:
			return candidate
	return None


def _auto_detect_language_for_speech_filter(synth: Any, text: str) -> str | None:
	candidates = synth._auto_language_candidates()
	if not candidates:
		return None
	if len(candidates) == 1:
		return candidates[0]
	detected = synth._detect_auto_language(text, candidates)
	if detected is not None:
		return detected
	return synth._auto_language_preferred(candidates, synth.voice)


def _auto_language_for_process_text(synth: Any, locale: str, text: str) -> str | None:
	candidates = synth._auto_language_candidates()
	if not candidates:
		return None
	candidateForLocale = _auto_language_candidate_for_locale(synth, locale, candidates)
	if _nvda_uses_lang_change_commands() and candidateForLocale:
		return candidateForLocale
	if len(candidates) >= 2:
		detected = synth._detect_auto_language(text, candidates)
		if detected is not None:
			return detected
		return synth._auto_language_preferred(candidates, synth.voice)
	return candidateForLocale or candidates[0]


def _auto_profile_variant_for_language(synth: Any, language: str | None) -> str | None:
	candidates = synth._auto_language_candidates()
	if not language or not candidates:
		return None
	languageKeys = _language_match_keys(language)
	root = synth._language_root(language)
	targetLanguage = ""
	for candidate in candidates:
		if _language_match_keys(candidate).intersection(languageKeys):
			targetLanguage = candidate
			break
	if not targetLanguage:
		for candidate in candidates:
			if synth._language_root(candidate) == root:
				targetLanguage = candidate
				break
	if not targetLanguage:
		return None
	profile = synth._auto_language_profile_for_language(targetLanguage)
	voice = str(profile.get("voice") or "")
	if synth._voice_matches_language(voice, targetLanguage):
		return voice
	return synth._voice_for_language(targetLanguage, synth.voice)


def _profile_int(value: Any, default: int, minimum: int = 0, maximum: int = 100) -> int:
	try:
		return max(minimum, min(maximum, int(value)))
	except (TypeError, ValueError):
		return max(minimum, min(maximum, int(default)))


def _profile_bool(value: Any, default: bool = False) -> bool:
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	if value is None:
		return default
	return bool(value)


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
		"capPitchChange": _profile_int(profile.get("capPitchChange"), synthConfig["capPitchChange"], -100, 100),
		"sayCapForCapitals": _profile_bool(profile.get("sayCapForCapitals"), synthConfig["sayCapForCapitals"]),
		"beepForCapitals": _profile_bool(profile.get("beepForCapitals"), synthConfig["beepForCapitals"]),
		"useSpellingFunctionality": _profile_bool(
			profile.get("useSpellingFunctionality"),
			synthConfig["useSpellingFunctionality"],
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
		targetLanguage = _auto_language_for_process_text(synth, locale, text)
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
		if len(candidates) != 1:
			return None
		return _auto_profile_character_settings_for_language(synth, candidates[0])
	except Exception:
		log.debug("Could not resolve single Google TTS auto-language character profile.", exc_info=True)
		return None


class _VoiceDictionarySynthProxy:
	"""Expose a speaker variant to NVDA's voice dictionary loader without changing the live synth."""

	def __init__(self, synth: Any, voice: str) -> None:
		self._synth = synth
		self.name = getattr(synth, "name", "")
		speakerVoiceInfos = getattr(synth, "_speaker_voice_infos", None)
		self.availableVoices = speakerVoiceInfos() if callable(speakerVoiceInfos) else getattr(synth, "availableVoices", {})
		self.voice = voice

	def isSupported(self, setting: str) -> bool:
		if setting == "voice":
			return self.voice in self.availableVoices
		return self._synth.isSupported(setting)

	def __getattr__(self, name: str) -> Any:
		return getattr(self._synth, name)


def _load_voice_dictionary_for_voice(synth: Any, voice: str) -> bool:
	speakerVoiceInfos = getattr(synth, "_speaker_voice_infos", None)
	availableVoices = speakerVoiceInfos() if callable(speakerVoiceInfos) else getattr(synth, "availableVoices", {})
	if not voice or voice not in availableVoices:
		return False
	loadVoiceDict = _originalSpeechDictLoadVoiceDict or getattr(speechDictHandler, "loadVoiceDict", None)
	if not callable(loadVoiceDict):
		return False
	loadVoiceDict(_VoiceDictionarySynthProxy(synth, voice))
	return True


def _current_google_tts_speaker_id(synth: Any) -> str:
	return str(getattr(synth, "variant", "") or getattr(synth, "voice", "") or "")


def _patch_google_tts_voice_dictionary_loading() -> None:
	global _originalSpeechDictLoadVoiceDict, _patchedSpeechDictLoadVoiceDict
	if _originalSpeechDictLoadVoiceDict is not None:
		return
	loadVoiceDict = getattr(speechDictHandler, "loadVoiceDict", None)
	if loadVoiceDict is None:
		log.debugWarning("NVDA speechDictHandler.loadVoiceDict is unavailable; Google TTS voice dictionary patch disabled.")
		return
	_originalSpeechDictLoadVoiceDict = loadVoiceDict
	originalLoadVoiceDict = _originalSpeechDictLoadVoiceDict

	def load_voice_dictionary_for_google_tts_variant(synth: Any, *args: Any, **kwargs: Any) -> None:
		try:
			if getattr(synth, "name", "") == SYNTH_NAME:
				voice = _current_google_tts_speaker_id(synth)
				if voice and _load_voice_dictionary_for_voice(synth, voice):
					return
		except Exception:
			log.debug("Could not load Google TTS voice dictionary for the selected variant.", exc_info=True)
		originalLoadVoiceDict(synth, *args, **kwargs)

	_patchedSpeechDictLoadVoiceDict = load_voice_dictionary_for_google_tts_variant
	speechDictHandler.loadVoiceDict = load_voice_dictionary_for_google_tts_variant


def _unpatch_google_tts_voice_dictionary_loading() -> None:
	global _originalSpeechDictLoadVoiceDict, _patchedSpeechDictLoadVoiceDict
	if _originalSpeechDictLoadVoiceDict is None:
		return
	if getattr(speechDictHandler, "loadVoiceDict", None) is _patchedSpeechDictLoadVoiceDict:
		speechDictHandler.loadVoiceDict = _originalSpeechDictLoadVoiceDict
	_originalSpeechDictLoadVoiceDict = None
	_patchedSpeechDictLoadVoiceDict = None


def _filter_auto_language_speech_sequence(speechSequence: list[Any], *args: Any, **kwargs: Any) -> list[Any]:
	try:
		synth = synthDriverHandler.getSynth()
		if getattr(synth, "name", "") != SYNTH_NAME or not synth._auto_language_detection_enabled():
			return speechSequence
		baseLanguage = synth.voice
		if baseLanguage not in getattr(synth, "availableVoices", {}):
			baseLanguage = synth.catalog.language_for_voice(synth.voice)
	except Exception:
		return speechSequence
	filtered: list[Any] = []
	currentAutoLanguage: str | None = None
	explicitLanguageActive = False
	for item in speechSequence:
		if isinstance(item, LangChangeCommand):
			currentAutoLanguage = getattr(item, "googleTtsForNvdaLanguage", None) or getattr(item, "lang", None)
			filtered.append(_google_lang_change_command(currentAutoLanguage))
			explicitLanguageActive = bool(currentAutoLanguage)
			continue
		if isinstance(item, str) and item and not explicitLanguageActive:
			targetLanguage = _auto_detect_language_for_speech_filter(synth, item)
			if targetLanguage is None and currentAutoLanguage is not None:
				targetLanguage = baseLanguage
			if targetLanguage is not None and not _same_language(currentAutoLanguage, targetLanguage):
				filtered.append(_google_lang_change_command(targetLanguage))
				currentAutoLanguage = targetLanguage
		filtered.append(item)
	return filtered


def _register_auto_language_speech_filter() -> None:
	global _autoLanguageSpeechFilterRegistered
	if _autoLanguageSpeechFilterRegistered:
		return
	speech.extensions.filter_speechSequence.register(_filter_auto_language_speech_sequence)
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


def _patch_auto_language_voice_dictionary() -> None:
	global _originalSpeechProcessText, _originalSpeechGetSpellingSpeech
	global _originalShortcutKeysShouldUseSpellingFunctionality
	global _patchedSpeechProcessText, _patchedSpeechGetSpellingSpeech
	global _patchedShortcutKeysShouldUseSpellingFunctionality
	if _originalSpeechProcessText is not None:
		return
	_originalSpeechProcessText = speechModule.processText
	_originalSpeechGetSpellingSpeech = speechModule.getSpellingSpeech
	_originalShortcutKeysShouldUseSpellingFunctionality = getattr(
		shortcutKeysModule,
		"shouldUseSpellingFunctionality",
		None,
	)
	originalProcessText = _originalSpeechProcessText
	originalGetSpellingSpeech = _originalSpeechGetSpellingSpeech
	originalShouldUseSpellingFunctionality = _originalShortcutKeysShouldUseSpellingFunctionality

	def process_text_with_auto_voice_dictionary(*args: Any, **kwargs: Any) -> str:
		argsList = list(args)
		locale = kwargs.get("locale") if "locale" in kwargs else (argsList[0] if argsList else None)
		text = kwargs.get("text") if "text" in kwargs else (argsList[1] if len(argsList) > 1 else None)

		def call_original_with_locale(effectiveLocale: str | None = None) -> str:
			updatedArgs = list(args)
			updatedKwargs = dict(kwargs)
			if effectiveLocale is not None:
				if "locale" in updatedKwargs:
					updatedKwargs["locale"] = effectiveLocale
				elif updatedArgs:
					updatedArgs[0] = effectiveLocale
				else:
					updatedKwargs["locale"] = effectiveLocale
			return originalProcessText(*updatedArgs, **updatedKwargs)

		try:
			if not isinstance(locale, str) or not isinstance(text, str):
				return call_original_with_locale()
			synth = synthDriverHandler.getSynth()
			if getattr(synth, "name", "") != SYNTH_NAME or not synth._auto_language_detection_enabled():
				return call_original_with_locale()
			targetLanguage = _auto_language_for_process_text(synth, locale, text)
			effectiveLocale = _nvda_locale_for_language(targetLanguage) or _nvda_locale_for_language(locale) or locale
			targetVariant = _auto_profile_variant_for_language(synth, targetLanguage or effectiveLocale)
			currentVariant = _current_google_tts_speaker_id(synth)
			restoreVoiceDict = False
			try:
				if targetVariant and targetVariant != currentVariant:
					restoreVoiceDict = _load_voice_dictionary_for_voice(synth, targetVariant)
				return call_original_with_locale(effectiveLocale)
			finally:
				if restoreVoiceDict:
					try:
						_load_voice_dictionary_for_voice(synth, currentVariant)
					except Exception:
						log.debug("Could not restore Google TTS voice dictionary.", exc_info=True)
		except Exception:
			log.debug("Could not apply Google TTS auto-language voice dictionary.", exc_info=True)
			return call_original_with_locale()

	def get_spelling_speech_with_auto_profile(
		text: str,
		locale: str | None = None,
		useCharacterDescriptions: bool = False,
		*args: Any,
		**kwargs: Any,
	) -> Any:
		context = _auto_profile_character_context_for_text(locale, text)
		if not context:
			yield from originalGetSpellingSpeech(text, locale, useCharacterDescriptions, *args, **kwargs)
			return
		settings, effectiveLocale = context
		with _speechConfigOverlayLock:
			try:
				synthConfig = config.conf["speech"][SYNTH_NAME]
				originalSettings = {
					"capPitchChange": synthConfig["capPitchChange"],
					"sayCapForCapitals": synthConfig["sayCapForCapitals"],
					"beepForCapitals": synthConfig["beepForCapitals"],
					"useSpellingFunctionality": synthConfig["useSpellingFunctionality"],
				}
			except Exception:
				log.debug("Could not apply Google TTS auto-language spelling settings.", exc_info=True)
				yield from originalGetSpellingSpeech(text, locale, useCharacterDescriptions, *args, **kwargs)
				return
			for key, value in settings.items():
				synthConfig[key] = value
			try:
				yield from originalGetSpellingSpeech(
					text,
					effectiveLocale,
					useCharacterDescriptions,
					*args,
					**kwargs,
				)
			finally:
				for key, value in originalSettings.items():
					synthConfig[key] = value

	def should_use_spelling_functionality_with_auto_profile(*args: Any, **kwargs: Any) -> bool:
		settings = _single_auto_profile_character_settings()
		if settings is not None:
			return bool(settings.get("useSpellingFunctionality", True))
		if originalShouldUseSpellingFunctionality is not None:
			return bool(originalShouldUseSpellingFunctionality(*args, **kwargs))
		return True

	_patchedSpeechProcessText = process_text_with_auto_voice_dictionary
	_patchedSpeechGetSpellingSpeech = get_spelling_speech_with_auto_profile
	_patchedShortcutKeysShouldUseSpellingFunctionality = should_use_spelling_functionality_with_auto_profile
	speechModule.processText = process_text_with_auto_voice_dictionary
	speechModule.getSpellingSpeech = get_spelling_speech_with_auto_profile
	if originalShouldUseSpellingFunctionality is not None:
		shortcutKeysModule.shouldUseSpellingFunctionality = should_use_spelling_functionality_with_auto_profile


def _unpatch_auto_language_voice_dictionary() -> None:
	global _originalSpeechProcessText, _originalSpeechGetSpellingSpeech
	global _originalShortcutKeysShouldUseSpellingFunctionality
	global _patchedSpeechProcessText, _patchedSpeechGetSpellingSpeech
	global _patchedShortcutKeysShouldUseSpellingFunctionality
	if _originalSpeechProcessText is None:
		return
	if getattr(speechModule, "processText", None) is _patchedSpeechProcessText:
		speechModule.processText = _originalSpeechProcessText
	if getattr(speechModule, "getSpellingSpeech", None) is _patchedSpeechGetSpellingSpeech:
		speechModule.getSpellingSpeech = _originalSpeechGetSpellingSpeech
	if (
		_originalShortcutKeysShouldUseSpellingFunctionality is not None
		and getattr(shortcutKeysModule, "shouldUseSpellingFunctionality", None)
		is _patchedShortcutKeysShouldUseSpellingFunctionality
	):
		shortcutKeysModule.shouldUseSpellingFunctionality = _originalShortcutKeysShouldUseSpellingFunctionality
	_originalSpeechProcessText = None
	_originalSpeechGetSpellingSpeech = None
	_originalShortcutKeysShouldUseSpellingFunctionality = None
	_patchedSpeechProcessText = None
	_patchedSpeechGetSpellingSpeech = None
	_patchedShortcutKeysShouldUseSpellingFunctionality = None


def _close_voice_manager() -> None:
	global _dialog
	if _dialog is None:
		return
	try:
		_dialog.Destroy()
	except RuntimeError:
		pass
	finally:
		_dialog = None


def _open_google_tts_settings() -> None:
	gui.mainFrame.popupSettingsDialog(gui.settingsDialogs.NVDASettingsDialog, GoogleTtsSettingsPanel)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = _("Google TTS For NVDA")

	def __init__(self) -> None:
		super().__init__()
		self.voiceManagerMenuItem: wx.MenuItem | None = None
		self._startupUpdateCheckRegistered = False
		if not globalVars.appArgs.secure:
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
			if GoogleTtsSettingsPanel not in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
				gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(GoogleTtsSettingsPanel)
			self.voiceManagerMenuItem = gui.mainFrame.sysTrayIcon.toolsMenu.Append(
				wx.ID_ANY,
				_("Google TTS Voice Manager..."),
				_("Download or remove Google TTS For NVDA voice packages"),
			)
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_open_voice_manager, self.voiceManagerMenuItem)

	def _on_post_nvda_startup(self, *args: Any, **kwargs: Any) -> None:
		try:
			updateGui.start_automatic_update_check()
		except Exception:
			log.debug("Could not start Google TTS For NVDA automatic update check.", exc_info=True)

	def terminate(self, *args: Any, **kwargs: Any) -> None:
		_close_voice_manager()
		if self._startupUpdateCheckRegistered:
			try:
				import core

				core.postNvdaStartup.unregister(self._on_post_nvda_startup)
			except Exception:
				log.debug("Could not unregister Google TTS For NVDA automatic update check.", exc_info=True)
			self._startupUpdateCheckRegistered = False
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(GoogleTtsSettingsPanel)
		except ValueError:
			pass
		if self.voiceManagerMenuItem is not None:
			try:
				gui.mainFrame.sysTrayIcon.Unbind(wx.EVT_MENU, source=self.voiceManagerMenuItem)
			except RuntimeError:
				pass
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self.voiceManagerMenuItem.Id)
			except RuntimeError:
				pass
		_unpatch_synth_selection()
		_unpatch_read_only_text_setting()
		_unpatch_voice_dictionary_dialog()
		_unpatch_google_tts_voice_dictionary_loading()
		_unregister_auto_language_speech_filter()
		_unpatch_auto_language_voice_dictionary()
		super().terminate(*args, **kwargs)

	def on_open_voice_manager(self, evt: Any, *args: Any, **kwargs: Any) -> None:
		_open_voice_manager()

	def script_openVoiceManager(self, gesture: Any, *args: Any, **kwargs: Any) -> None:
		_open_voice_manager()

	script_openVoiceManager.__doc__ = _("Opens the Google TTS Voice Manager.")

	def script_openSettings(self, gesture: Any, *args: Any, **kwargs: Any) -> None:
		_open_google_tts_settings()

	script_openSettings.__doc__ = _("Opens the Google TTS For NVDA settings.")

	__gestures = {
		"kb:NVDA+control+shift+g": "openVoiceManager",
	}
