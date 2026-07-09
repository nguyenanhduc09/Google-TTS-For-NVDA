# -*- coding: utf-8 -*-
from __future__ import annotations

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


def _has_usable_google_tts_voices() -> bool:
	try:
		fullCatalog = VoiceCatalog.load()
		installedPackages = voice_store.installed_packages(fullCatalog)
		if not installedPackages:
			return False
		return bool(VoiceCatalog(installedPackages).speakers)
	except EngineLibraryError:
		raise
	except Exception:
		log.exception("Could not check installed Google TTS voice packages.", exc_info=True)
		return False


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


def _show_missing_voices_prompt() -> None:
	global _missingVoicesPromptActive
	if _missingVoicesPromptActive:
		return
	_missingVoicesPromptActive = True
	try:
		answer = gui.messageBox(
			_(
				"No Google TTS For NVDA voices are installed.\n\n"
				"Press OK to open Google TTS Voice Manager and download a voice package.\n"
				"Press Cancel to keep using your current synthesizer.\n\n"
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
	name: str | None,
	isFallback: bool = False,
	*,
	_leftToTry: list[str] | None = None,
) -> bool:
	# Keep the current synthesizer active so NVDA can speak the prompt instead of
	# showing its generic "could not load synthesizer" error first.
	if (
		name == SYNTH_NAME
		and not isFallback
	):
		try:
			hasVoices = _has_usable_google_tts_voices()
		except EngineLibraryError as exc:
			wx.CallAfter(_show_engine_library_error, exc)
			return True
		if not hasVoices:
			wx.CallAfter(_show_missing_voices_prompt)
			return True
	if _originalSetSynth is None:
		return False
	return _originalSetSynth(name, isFallback=isFallback, _leftToTry=_leftToTry)


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
				_("Download and remove Google TTS For NVDA voice packages"),
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
