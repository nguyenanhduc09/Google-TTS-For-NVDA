# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from typing import Any

import addonHandler
import config
import globalPluginHandler
import globalVars
import gui
import synthDriverHandler
import wx
from logHandler import log

from synthDrivers.googleTtsForNvda.bridge import CONFIG_BROWSER_RUNTIME, CONFIG_SECTION, DEFAULT_BROWSER_RUNTIME
from synthDrivers.googleTtsForNvda.catalog import EngineLibraryError, VoiceCatalog
from synthDrivers.googleTtsForNvda import voice_store

from .settings import GoogleTtsSettingsPanel
from .voiceManager import VoiceManagerDialog


addonHandler.initTranslation()

config.conf.spec[CONFIG_SECTION] = {
	CONFIG_BROWSER_RUNTIME: f"string(default={DEFAULT_BROWSER_RUNTIME})",
}

SYNTH_NAME = "googleTtsForNvda"
_dialog: VoiceManagerDialog | None = None
_originalSetSynth: Any | None = None
_originalSettingsDialogSetSynth: Any | None = None
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
		super().terminate()

	def on_open_voice_manager(self, evt: Any) -> None:
		_open_voice_manager()

	def script_openVoiceManager(self, gesture: Any) -> None:
		_open_voice_manager()

	script_openVoiceManager.__doc__ = _("Opens the Google TTS Voice Manager.")

	__gestures = {
		"kb:NVDA+control+shift+g": "openVoiceManager",
	}
