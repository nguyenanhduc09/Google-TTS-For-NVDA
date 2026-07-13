# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import os
from typing import Any

import addonHandler
import config
import globalPluginHandler
import globalVars
import gui
from gui import guiHelper
import speech.extensions
from speech import speech as speechModule
from speech.commands import LangChangeCommand
import speechDictHandler
import synthDriverHandler
import wx
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
)
from synthDrivers.googleTtsForNvda.catalog import EngineLibraryError, VoiceCatalog
from synthDrivers.googleTtsForNvda import voice_store

from .settings import GoogleTtsSettingsPanel
from .voiceManager import VoiceManagerDialog


addonHandler.initTranslation()

config.conf.spec[CONFIG_SECTION] = {
	CONFIG_AUTO_LANGUAGE_CANDIDATES: f"string(default={DEFAULT_AUTO_LANGUAGE_CANDIDATES})",
	CONFIG_AUTO_LANGUAGE_DETECTION: f"boolean(default={str(DEFAULT_AUTO_LANGUAGE_DETECTION).lower()})",
	CONFIG_AUTO_LANGUAGE_PREFERRED: f"string(default={DEFAULT_AUTO_LANGUAGE_PREFERRED})",
	CONFIG_AUTO_LANGUAGE_PROFILES: f"string(default={DEFAULT_AUTO_LANGUAGE_PROFILES})",
	CONFIG_BROWSER_RUNTIME: f"string(default={DEFAULT_BROWSER_RUNTIME})",
}

SYNTH_NAME = "googleTtsForNvda"
_AUTO_LANGUAGE_NOTICE_ID = "notice"
_dialog: VoiceManagerDialog | None = None
_originalSetSynth: Any | None = None
_originalSettingsDialogSetSynth: Any | None = None
_originalAutoSettingsGetSettingMaker: Any | None = None
_originalAutoSettingsUpdateValueForControl: Any | None = None
_originalSpeechProcessText: Any | None = None
_originalPopupSettingsDialog: Any | None = None
_autoLanguageSpeechFilterRegistered = False
_missingVoicesPromptActive = False


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
	setattr(self, f"{setting.id}Edit", edit)
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


def _patch_read_only_text_setting() -> None:
	global _originalAutoSettingsGetSettingMaker, _originalAutoSettingsUpdateValueForControl
	if _originalAutoSettingsGetSettingMaker is not None:
		return
	autoSettingsMixin = getattr(gui.settingsDialogs, "AutoSettingsMixin", None)
	if autoSettingsMixin is None:
		return
	_originalAutoSettingsGetSettingMaker = autoSettingsMixin._getSettingMaker
	_originalAutoSettingsUpdateValueForControl = autoSettingsMixin._updateValueForControl

	def _get_setting_maker(self: Any, setting: Any) -> Any:
		if _is_google_tts_read_only_setting(setting):
			def _make_control(setting: Any, settingsStorage: Any) -> wx.BoxSizer:
				if not _is_google_tts_read_only_setting(setting, settingsStorage):
					return _originalAutoSettingsGetSettingMaker(self, setting)(setting, settingsStorage)
				return _make_read_only_text_setting_control(self, setting, settingsStorage)

			return _make_control
		return _originalAutoSettingsGetSettingMaker(self, setting)

	def _update_value_for_control(self: Any, setting: Any, settingsStorage: Any) -> None:
		if _is_google_tts_read_only_setting(setting, settingsStorage):
			try:
				if setting.id not in getattr(self, "sizerDict", {}):
					return
				self.settingsSizer.Show(self.sizerDict[setting.id])
				getattr(self, f"{setting.id}Edit").SetValue(str(getattr(settingsStorage, setting.id, "") or ""))
			except Exception:
				log.debug("Could not update Google TTS read-only speech setting.", exc_info=True)
			return
		return _originalAutoSettingsUpdateValueForControl(self, setting, settingsStorage)

	autoSettingsMixin._getSettingMaker = _get_setting_maker
	autoSettingsMixin._updateValueForControl = _update_value_for_control


def _unpatch_read_only_text_setting() -> None:
	global _originalAutoSettingsGetSettingMaker, _originalAutoSettingsUpdateValueForControl
	if _originalAutoSettingsGetSettingMaker is None:
		return
	autoSettingsMixin = getattr(gui.settingsDialogs, "AutoSettingsMixin", None)
	if autoSettingsMixin is not None:
		autoSettingsMixin._getSettingMaker = _originalAutoSettingsGetSettingMaker
		if _originalAutoSettingsUpdateValueForControl is not None:
			autoSettingsMixin._updateValueForControl = _originalAutoSettingsUpdateValueForControl
	_originalAutoSettingsGetSettingMaker = None
	_originalAutoSettingsUpdateValueForControl = None


def _google_auto_language_detection_active() -> bool:
	try:
		synth = synthDriverHandler.getSynth()
		return getattr(synth, "name", "") == SYNTH_NAME and synth._auto_language_detection_enabled()
	except Exception:
		return False


def _show_voice_dictionary_auto_language_message() -> None:
	gui.messageBox(
		_(
			"Voice dictionary preferences are unavailable while automatic language detection is enabled.\n\n"
			"Google TTS For NVDA may switch between several profile voices while speaking, so NVDA cannot "
			"know which single voice dictionary to edit.\n\n"
			"Open the Google TTS For NVDA category in NVDA Settings and turn off automatic language detection, "
			"then open Voice dictionary again."
		),
		_("Google TTS For NVDA"),
		wx.OK | wx.ICON_INFORMATION,
		gui.mainFrame,
	)


def _patch_voice_dictionary_dialog() -> None:
	global _originalPopupSettingsDialog
	if _originalPopupSettingsDialog is not None:
		return
	mainFrame = getattr(gui, "mainFrame", None)
	if mainFrame is None or not hasattr(mainFrame, "popupSettingsDialog"):
		return
	_originalPopupSettingsDialog = mainFrame.popupSettingsDialog

	def popup_settings_dialog(dialog: Any, *args: Any, **kwargs: Any) -> Any:
		if dialog is getattr(gui, "VoiceDictionaryDialog", None) and _google_auto_language_detection_active():
			_show_voice_dictionary_auto_language_message()
			return None
		return _originalPopupSettingsDialog(dialog, *args, **kwargs)

	mainFrame.popupSettingsDialog = popup_settings_dialog


def _unpatch_voice_dictionary_dialog() -> None:
	global _originalPopupSettingsDialog
	if _originalPopupSettingsDialog is None:
		return
	mainFrame = getattr(gui, "mainFrame", None)
	if mainFrame is not None:
		mainFrame.popupSettingsDialog = _originalPopupSettingsDialog
	_originalPopupSettingsDialog = None


def _same_language(left: str | None, right: str | None) -> bool:
	return str(left or "").replace("_", "-").lower() == str(right or "").replace("_", "-").lower()


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
	for candidate in candidates:
		if _same_language(candidate, locale):
			return candidate
	localeRoot = str(locale or "").replace("_", "-").split("-", 1)[0].lower()
	for candidate in candidates:
		if synth._language_root(candidate) == localeRoot:
			return candidate
	return None


def _auto_detect_language_for_speech_filter(synth: Any, text: str) -> str | None:
	candidates = synth._auto_language_candidates()
	if len(candidates) < 2:
		return None
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


def _auto_profile_voice_for_language(synth: Any, language: str | None) -> str | None:
	candidates = synth._auto_language_candidates()
	if not language or not candidates:
		return None
	languageKey = synth._normalize_language(language)
	root = synth._language_root(language)
	targetLanguage = ""
	for candidate in candidates:
		if synth._normalize_language(candidate) == languageKey:
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


class _VoiceDictionarySynthProxy:
	"""Expose another voice to NVDA's voice dictionary loader without changing the live synth."""

	def __init__(self, synth: Any, voice: str) -> None:
		self._synth = synth
		self.name = getattr(synth, "name", "")
		self.availableVoices = getattr(synth, "availableVoices", {})
		self.voice = voice

	def isSupported(self, setting: str) -> bool:
		if setting == "voice":
			return self.voice in self.availableVoices and self._synth.isSupported(setting)
		return self._synth.isSupported(setting)

	def __getattr__(self, name: str) -> Any:
		return getattr(self._synth, name)


def _load_voice_dictionary_for_voice(synth: Any, voice: str) -> bool:
	availableVoices = getattr(synth, "availableVoices", {})
	if not voice or voice not in availableVoices:
		return False
	speechDictHandler.loadVoiceDict(_VoiceDictionarySynthProxy(synth, voice))
	return True


def _filter_auto_language_speech_sequence(speechSequence: list[Any]) -> list[Any]:
	try:
		synth = synthDriverHandler.getSynth()
		if getattr(synth, "name", "") != SYNTH_NAME or not synth._auto_language_detection_enabled():
			return speechSequence
		baseLanguage = synth.catalog.language_for_voice(synth.voice)
	except Exception:
		return speechSequence
	filtered: list[Any] = []
	currentAutoLanguage: str | None = None
	explicitLanguageActive = False
	for item in speechSequence:
		if isinstance(item, LangChangeCommand):
			currentAutoLanguage = getattr(item, "lang", None)
			filtered.append(LangChangeCommand(_nvda_locale_for_language(currentAutoLanguage)))
			explicitLanguageActive = bool(currentAutoLanguage)
			continue
		if isinstance(item, str) and item and not explicitLanguageActive:
			targetLanguage = _auto_detect_language_for_speech_filter(synth, item)
			if targetLanguage is None and currentAutoLanguage is not None:
				targetLanguage = baseLanguage
			if targetLanguage is not None and not _same_language(currentAutoLanguage, targetLanguage):
				filtered.append(LangChangeCommand(_nvda_locale_for_language(targetLanguage)))
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
	global _originalSpeechProcessText
	if _originalSpeechProcessText is not None:
		return
	_originalSpeechProcessText = speechModule.processText

	def process_text_with_auto_voice_dictionary(
		locale: str,
		text: str,
		symbolLevel: Any,
		normalize: bool = False,
	) -> str:
		try:
			synth = synthDriverHandler.getSynth()
			if getattr(synth, "name", "") != SYNTH_NAME or not synth._auto_language_detection_enabled():
				return _originalSpeechProcessText(locale, text, symbolLevel, normalize)
			targetLanguage = _auto_language_for_process_text(synth, locale, text)
			effectiveLocale = _nvda_locale_for_language(targetLanguage) or _nvda_locale_for_language(locale) or locale
			targetVoice = _auto_profile_voice_for_language(synth, targetLanguage or effectiveLocale)
			restoreVoiceDict = False
			try:
				if targetVoice and targetVoice != getattr(synth, "voice", ""):
					restoreVoiceDict = _load_voice_dictionary_for_voice(synth, targetVoice)
				return _originalSpeechProcessText(effectiveLocale, text, symbolLevel, normalize)
			finally:
				if restoreVoiceDict:
					speechDictHandler.loadVoiceDict(synth)
		except Exception:
			log.debug("Could not apply Google TTS auto-language voice dictionary.", exc_info=True)
			return _originalSpeechProcessText(locale, text, symbolLevel, normalize)

	speechModule.processText = process_text_with_auto_voice_dictionary


def _unpatch_auto_language_voice_dictionary() -> None:
	global _originalSpeechProcessText
	if _originalSpeechProcessText is None:
		return
	speechModule.processText = _originalSpeechProcessText
	_originalSpeechProcessText = None


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


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = _("Google TTS For NVDA")

	def __init__(self) -> None:
		super().__init__()
		self.voiceManagerMenuItem: wx.MenuItem | None = None
		if not globalVars.appArgs.secure:
			_patch_synth_selection()
			_patch_read_only_text_setting()
			_patch_voice_dictionary_dialog()
			_patch_auto_language_voice_dictionary()
			_register_auto_language_speech_filter()
			if GoogleTtsSettingsPanel not in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
				gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(GoogleTtsSettingsPanel)
			self.voiceManagerMenuItem = gui.mainFrame.sysTrayIcon.toolsMenu.Append(
				wx.ID_ANY,
				_("Google TTS Voice Manager..."),
				_("Download or remove Google TTS For NVDA voice packages"),
			)
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_open_voice_manager, self.voiceManagerMenuItem)

	def terminate(self) -> None:
		_close_voice_manager()
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
		_unregister_auto_language_speech_filter()
		_unpatch_auto_language_voice_dictionary()
		super().terminate()

	def on_open_voice_manager(self, evt: Any) -> None:
		_open_voice_manager()

	def script_openVoiceManager(self, gesture: Any) -> None:
		_open_voice_manager()

	script_openVoiceManager.__doc__ = _("Opens the Google TTS Voice Manager.")

	__gestures = {
		"kb:NVDA+control+shift+g": "openVoiceManager",
	}
