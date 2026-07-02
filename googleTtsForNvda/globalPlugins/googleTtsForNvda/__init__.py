# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import addonHandler
import globalPluginHandler
import globalVars
import gui
import wx

from .voiceManager import VoiceManagerDialog


addonHandler.initTranslation()

_dialog: VoiceManagerDialog | None = None


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
		_dialog = VoiceManagerDialog(gui.mainFrame, _clear_dialog_reference, initialPage=initialPage)
		_dialog.Show()
	finally:
		gui.mainFrame.postPopup()


def open_voice_manager_download_tab() -> None:
	_open_voice_manager("download")


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
			self.voiceManagerMenuItem = gui.mainFrame.sysTrayIcon.toolsMenu.Append(
				wx.ID_ANY,
				_("Google TTS voice manager..."),
				_("Download and remove Google TTS voice packages"),
			)
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_open_voice_manager, self.voiceManagerMenuItem)

	def terminate(self) -> None:
		_close_voice_manager()
		if self.voiceManagerMenuItem is not None:
			try:
				gui.mainFrame.sysTrayIcon.Unbind(wx.EVT_MENU, source=self.voiceManagerMenuItem)
			except RuntimeError:
				pass
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self.voiceManagerMenuItem.Id)
			except RuntimeError:
				pass
		super().terminate()

	def on_open_voice_manager(self, evt: Any) -> None:
		_open_voice_manager()

	def script_openVoiceManager(self, gesture: Any) -> None:
		_open_voice_manager()

	script_openVoiceManager.__doc__ = _("Opens the Google TTS voice manager.")

	__gestures = {
		"kb:NVDA+control+shift+g": "openVoiceManager",
	}
