# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
import math
import threading
import weakref
from typing import Callable

import addonHandler
import config
import gui
from gui import guiHelper
import languageHandler
from logHandler import log
import ui
import wx

from synthDrivers.googleTtsForNvda.bridge import CONFIG_SECTION
from . import updater


# Keep NVDA's translator for generic core strings before installing the add-on translation.
_nvda_gettext = getattr(builtins, "_", lambda message: message)
addonHandler.initTranslation()

CONFIG_AUTO_UPDATE_CHECK = "autoUpdateCheckOnStartup"
DEFAULT_AUTO_UPDATE_CHECK = False
_CHECK_MODE_MANUAL = "manual"
_CHECK_MODE_AUTOMATIC = "automatic"


def _nvda_translate(message: str) -> str:
	try:
		return _nvda_gettext(message)
	except Exception:
		return message


def _from_dip(window: wx.Window, value: int) -> int:
	try:
		return int(window.FromDIP(value))
	except Exception:
		return value


def _estimate_wrapped_line_count(control: wx.TextCtrl, text: str, width: int) -> int:
	try:
		charWidth = max(1, int(control.GetTextExtent("M")[0]))
	except Exception:
		charWidth = _from_dip(control, 8)
	availableChars = max(12, width // max(1, charWidth))
	lines = 0
	for line in (text or "").splitlines() or [""]:
		lines += max(1, math.ceil(len(line) / availableChars))
	return lines


def _estimate_text_width(control: wx.TextCtrl, text: str) -> int:
	widths: list[int] = []
	for line in (text or "").splitlines() or [""]:
		try:
			widths.append(int(control.GetTextExtent(line)[0]))
		except Exception:
			widths.append(len(line) * _from_dip(control, 8))
	return max(widths or [0]) + _from_dip(control, 28)


def _max_read_only_text_width(control: wx.TextCtrl) -> int:
	defaultMaxWidth = _from_dip(control, 760)
	try:
		displayIndex = wx.Display.GetFromWindow(control)
		if displayIndex < 0:
			displayIndex = 0
		displayWidth = wx.Display(displayIndex).GetClientArea().GetWidth()
	except Exception:
		return defaultMaxWidth
	return min(defaultMaxWidth, max(_from_dip(control, 420), int(displayWidth * 0.75)))


def _read_only_text_target_width(control: wx.TextCtrl, text: str, width: int | None) -> int:
	if width is not None:
		return _from_dip(control, width)
	contentWidth = _estimate_text_width(control, text)
	minWidth = _from_dip(control, 360)
	maxWidth = _max_read_only_text_width(control)
	targetWidth = max(contentWidth, minWidth)
	return min(maxWidth, targetWidth)


def _resize_read_only_text_for_content(
	control: wx.TextCtrl,
	minLines: int = 2,
	maxLines: int = 6,
	width: int | None = None,
) -> None:
	text = control.GetValue()
	targetWidth = _read_only_text_target_width(control, text, width)
	lineCount = _estimate_wrapped_line_count(control, text, targetWidth)
	lineCount = max(minLines, min(maxLines, lineCount))
	try:
		lineHeight = max(1, int(control.GetCharHeight()))
	except Exception:
		lineHeight = _from_dip(control, 16)
	height = lineCount * lineHeight + _from_dip(control, 14)
	control.SetMinSize((targetWidth, height))
	try:
		control.InvalidateBestSize()
	except Exception:
		pass


def _bind_read_only_text_focus_announcement(
	control: wx.TextCtrl,
	minLines: int = 2,
	maxLines: int = 6,
	width: int | None = None,
) -> None:
	_resize_read_only_text_for_content(control, minLines=minLines, maxLines=maxLines, width=width)


def _window_is_alive(window: wx.Window | None) -> bool:
	if window is None:
		return False
	try:
		window.GetId()
		return True
	except RuntimeError:
		return False


def _dialog_parent(preferredParent: wx.Window | None = None) -> wx.Window | None:
	if _window_is_alive(preferredParent):
		try:
			parent = preferredParent.GetTopLevelParent()
			if _window_is_alive(parent):
				return parent
		except RuntimeError:
			pass
		return preferredParent
	mainFrame = getattr(gui, "mainFrame", None)
	if _window_is_alive(mainFrame):
		return mainFrame
	return None


def _pre_popup() -> None:
	mainFrame = getattr(gui, "mainFrame", None)
	try:
		if mainFrame is not None:
			mainFrame.prePopup()
	except RuntimeError:
		pass


def _post_popup() -> None:
	mainFrame = getattr(gui, "mainFrame", None)
	try:
		if mainFrame is not None:
			mainFrame.postPopup()
	except RuntimeError:
		pass


def _message_box(message: str, style: int, parent: wx.Window | None = None) -> int:
	dialogParent = _dialog_parent(parent)
	return gui.messageBox(
		message,
		_("Google TTS For NVDA update"),
		style,
		dialogParent,
	)


def automatic_update_check_enabled() -> bool:
	try:
		return bool(config.conf[CONFIG_SECTION][CONFIG_AUTO_UPDATE_CHECK])
	except Exception:
		log.debug("Could not read Google TTS For NVDA automatic update check setting.", exc_info=True)
		return DEFAULT_AUTO_UPDATE_CHECK


def set_automatic_update_check_enabled(enabled: bool) -> None:
	try:
		config.conf[CONFIG_SECTION][CONFIG_AUTO_UPDATE_CHECK] = bool(enabled)
	except Exception:
		log.debug("Could not save Google TTS For NVDA automatic update check setting.", exc_info=True)


def _current_version_for_status() -> str:
	try:
		return updater.current_version()
	except updater.UpdateError:
		return _nvda_translate("unknown")


def update_check_in_progress() -> bool:
	with _updateCheckLock:
		return _updateCheckInProgress


def update_status_message(autoCheckEnabled: bool | None = None) -> str:
	version = _current_version_for_status()
	with _updateCheckLock:
		inProgress = _updateCheckInProgress
		mode = _updateCheckMode
	if inProgress:
		if mode == _CHECK_MODE_AUTOMATIC:
			return _(
				"Current Google TTS For NVDA version: {version}. "
				"An automatic update check is running in the background. "
				"Manual checking will be available when it finishes."
			).format(version=version)
		return _("Current Google TTS For NVDA version: {version}. Checking for updates now.").format(
			version=version,
		)
	if autoCheckEnabled is None:
		autoCheckEnabled = automatic_update_check_enabled()
	if autoCheckEnabled:
		return _(
			"Current Google TTS For NVDA version: {version}. "
			"Automatic update checks are on. Google TTS For NVDA will check once the next time NVDA starts. "
			"You can also check manually."
		).format(version=version)
	return _(
		"Current Google TTS For NVDA version: {version}. "
		"Automatic update checks are off. Google TTS For NVDA will not check the next time NVDA starts. "
		"You can still check manually."
	).format(version=version)


_updateStatusListeners: list[weakref.ReferenceType] = []


def register_update_status_listener(listener: object) -> Callable[[], None]:
	try:
		listenerRef = weakref.WeakMethod(listener)
	except TypeError:
		listenerRef = weakref.ref(listener)
	_updateStatusListeners.append(listenerRef)

	def unregister() -> None:
		try:
			_updateStatusListeners.remove(listenerRef)
		except ValueError:
			pass

	return unregister


def _notify_update_status_changed() -> None:
	for listenerRef in list(_updateStatusListeners):
		listener = listenerRef()
		if listener is None:
			try:
				_updateStatusListeners.remove(listenerRef)
			except ValueError:
				pass
			continue
		wx.CallAfter(listener)


class _UpdateAvailableDialog(wx.Dialog):
	def __init__(self, parent: wx.Window, result: updater.UpdateCheckResult) -> None:
		super().__init__(parent, title=_("Google TTS For NVDA update available"))
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, sizer=mainSizer)
		message = wx.StaticText(
			self,
			label=_(
				"Google TTS For NVDA {version} is available. You are using {currentVersion}.\n\n"
				"Choose Yes to download and install this update."
			).format(version=result.update.version, currentVersion=result.currentVersion),
		)
		message.Wrap(560)
		helper.addItem(message, flag=wx.EXPAND)
		notes = result.update.releaseNotes or _("No change log is available for this update.")
		information = _(
			"Download size: {size}. Minimum NVDA version: {minimumVersion}. Last tested NVDA version: {lastTestedVersion}.\n\n"
			"Changes in this version:\n{changes}"
		).format(
			size=updater.format_size(result.update.size),
			minimumVersion=result.update.minimumNVDAVersion,
			lastTestedVersion=result.update.lastTestedNVDAVersion,
			changes=notes,
		)
		updateInformation = helper.addLabeledControl(
			_("Update information") + ":",
			wx.TextCtrl,
			value=information,
			style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
		)
		updateInformation.SetName(_("Update information"))
		_bind_read_only_text_focus_announcement(updateInformation, minLines=5, maxLines=15)
		self._updateInformation = updateInformation
		buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
		yesButton = wx.Button(self, id=wx.ID_YES)
		noButton = wx.Button(self, id=wx.ID_NO)
		buttonSizer.Add(yesButton, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.RIGHT)
		buttonSizer.Add(noButton)
		mainSizer.Add(buttonSizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.ALIGN_RIGHT)
		self.SetSizerAndFit(mainSizer)
		self.SetEscapeId(wx.ID_NO)
		yesButton.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_YES))
		noButton.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_NO))
		wx.CallAfter(self._focus_update_information)

	def _focus_update_information(self) -> None:
		try:
			self._updateInformation.SetInsertionPoint(0)
			self._updateInformation.SetFocus()
		except RuntimeError:
			pass


class _UpdateDownloadDialog(wx.Dialog):
	def __init__(self, parent: wx.Window, result: updater.UpdateCheckResult) -> None:
		super().__init__(parent, title=_("Downloading Google TTS For NVDA update"))
		self.cancelEvent = threading.Event()
		self.result: object | None = None
		self._finished = False
		self._lastUpdateProgressAnnouncement = 0
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, sizer=mainSizer)
		message = wx.StaticText(
			self,
			label=_("Downloading Google TTS For NVDA {version}.").format(version=result.update.version),
		)
		message.Wrap(520)
		helper.addItem(message, flag=wx.EXPAND)
		self.statusText = helper.addLabeledControl(
			_("Download status") + ":",
			wx.TextCtrl,
			value=_("Preparing the update download..."),
			style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
		)
		self.statusText.SetName(_("Download status"))
		_bind_read_only_text_focus_announcement(self.statusText, minLines=2, maxLines=5, width=520)
		self.gauge = wx.Gauge(self, range=100)
		mainSizer.Add(self.gauge, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND)
		buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
		self.cancelButton = wx.Button(self, id=wx.ID_CANCEL)
		buttonSizer.Add(self.cancelButton)
		mainSizer.Add(buttonSizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.ALIGN_RIGHT)
		self.SetSizerAndFit(mainSizer)
		self.cancelButton.Bind(wx.EVT_BUTTON, self._on_cancel)
		self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
		self.Bind(wx.EVT_CLOSE, self._on_close)
		wx.CallAfter(self._focus_status)

	def _focus_status(self) -> None:
		try:
			self.statusText.SetInsertionPoint(0)
			self.statusText.SetFocus()
		except RuntimeError:
			pass

	def _set_status(self, message: str) -> None:
		try:
			self.statusText.SetValue(message)
			_resize_read_only_text_for_content(self.statusText, minLines=2, maxLines=5, width=520)
			self.Layout()
			self.Fit()
		except RuntimeError:
			pass

	def update_progress(self, version: str, received: int, total: int) -> None:
		if self._finished or self.cancelEvent.is_set():
			return
		try:
			percent = int((received * 100) / max(1, total))
			percent = max(0, min(100, percent))
			self.gauge.SetValue(percent)
		except RuntimeError:
			return
		message = _("Downloading Google TTS For NVDA {version}: {percent}% ({received} of {total}).").format(
			version=version,
			percent=percent,
			received=updater.format_size(received),
			total=updater.format_size(total),
		)
		self._set_status(message)
		announce = False
		if percent >= 100:
			announce = True
		elif percent >= self._lastUpdateProgressAnnouncement + 25:
			self._lastUpdateProgressAnnouncement = (percent // 25) * 25
			announce = True
		if announce:
			ui.message(message)

	def _cancel_download(self) -> None:
		if self._finished or self.cancelEvent.is_set():
			return
		self.cancelEvent.set()
		try:
			self.cancelButton.Enable(False)
		except RuntimeError:
			pass
		message = _("Cancelling Google TTS For NVDA update download...")
		self._set_status(message)
		ui.message(message)

	def _on_cancel(self, evt: wx.CommandEvent) -> None:
		self._cancel_download()

	def _on_char_hook(self, evt: wx.KeyEvent) -> None:
		if evt.GetKeyCode() == wx.WXK_ESCAPE:
			self._cancel_download()
			return
		evt.Skip()

	def _on_close(self, evt: wx.CloseEvent) -> None:
		if self._finished:
			evt.Skip()
			return
		self._cancel_download()
		if evt.CanVeto():
			evt.Veto()

	def finish(self, result: object) -> None:
		if self._finished:
			return
		self.result = result
		self._finished = True
		try:
			self.cancelButton.Enable(False)
			if self.IsModal():
				self.EndModal(wx.ID_OK if isinstance(result, updater.DownloadedUpdate) else wx.ID_CANCEL)
			else:
				self.Close()
		except RuntimeError:
			pass


class _UpdateCheckController:
	def __init__(self, mode: str, parent: wx.Window | None = None) -> None:
		self.mode = mode
		try:
			self._parentRef = weakref.ref(parent) if parent is not None else None
		except TypeError:
			self._parentRef = lambda: parent

	@property
	def manual(self) -> bool:
		return self.mode == _CHECK_MODE_MANUAL

	def _parent(self) -> wx.Window | None:
		parent = self._parentRef() if self._parentRef is not None else None
		return _dialog_parent(parent)

	def start(self) -> None:
		if self.manual:
			ui.message(_("Checking for Google TTS For NVDA updates..."))
		locale = languageHandler.getLanguage()

		def run() -> None:
			try:
				result = updater.check_for_update(locale)
			except Exception as exc:
				result = exc
			wx.CallAfter(self._finish_check, result)

		threading.Thread(target=run, name="googleTtsForNvda.updateCheck", daemon=True).start()

	def _finish_check(self, result: object) -> None:
		if isinstance(result, BaseException):
			if self.manual:
				log.warning(
					"Google TTS For NVDA update check failed: %s",
					result,
					exc_info=(type(result), result, result.__traceback__),
				)
				_message_box(
					_("Could not check for Google TTS For NVDA updates. Check your internet connection and try again."),
					wx.OK | wx.ICON_ERROR,
					self._parent(),
				)
			else:
				log.debug(
					"Automatic Google TTS For NVDA update check failed.",
					exc_info=(type(result), result, result.__traceback__),
				)
			updater.cleanup_update_files()
			_finish_update_check()
			return
		if not isinstance(result, updater.UpdateCheckResult):
			if self.manual:
				_message_box(
					_("Could not check for Google TTS For NVDA updates. The update response was not understood."),
					wx.OK | wx.ICON_ERROR,
					self._parent(),
				)
			else:
				log.warning("Automatic Google TTS For NVDA update check returned an unexpected result.")
			updater.cleanup_update_files()
			_finish_update_check()
			return
		if not result.available:
			updater.remove_update_manifest(result)
			if self.manual:
				_message_box(
					_("Google TTS For NVDA is up to date. Current version: {version}.").format(
						version=result.currentVersion,
					),
					wx.OK | wx.ICON_INFORMATION,
					self._parent(),
				)
			_finish_update_check()
			return
		parent = self._parent()
		if parent is None:
			updater.remove_update_manifest(result)
			_finish_update_check()
			return
		try:
			answer = self._show_update_available_dialog(parent, result)
		except Exception:
			log.debug("Could not show the Google TTS For NVDA update prompt.", exc_info=True)
			updater.remove_update_manifest(result)
			_finish_update_check()
			return
		if answer == wx.ID_YES:
			self._start_download(result)
			return
		updater.remove_update_manifest(result)
		_finish_update_check()

	def _show_update_available_dialog(self, parent: wx.Window, result: updater.UpdateCheckResult) -> int:
		_pre_popup()
		try:
			dialog = _UpdateAvailableDialog(parent, result)
			try:
				return dialog.ShowModal()
			finally:
				dialog.Destroy()
		finally:
			_post_popup()

	def _start_download(self, updateCheckResult: updater.UpdateCheckResult) -> None:
		parent = self._parent()
		if parent is None:
			updater.cleanup_update_files()
			_finish_update_check()
			return
		if getattr(config, "isAppX", False):
			updater.remove_update_manifest(updateCheckResult)
			_finish_update_check()
			_message_box(
				_("Google TTS For NVDA updates cannot be installed in the Windows Store version of NVDA."),
				wx.OK | wx.ICON_ERROR,
				parent,
			)
			return
		try:
			result = self._download_update_with_dialog(parent, updateCheckResult)
		except Exception as exc:
			result = exc
		self._finish_download(result, updateCheckResult, parent)

	def _download_update_with_dialog(self, parent: wx.Window, result: updater.UpdateCheckResult) -> object:
		dialog = _UpdateDownloadDialog(parent, result)

		def run() -> None:
			lastProgressBucket = -1

			def progress(received: int, total: int) -> None:
				nonlocal lastProgressBucket
				percent = int((received * 100) / max(1, total))
				progressBucket = percent // 5
				if progressBucket == lastProgressBucket and received < total:
					return
				lastProgressBucket = progressBucket
				wx.CallAfter(dialog.update_progress, result.update.version, received, total)

			try:
				downloadedUpdate = updater.download_update(
					result.update,
					progress=progress,
					cancel_requested=dialog.cancelEvent.is_set,
				)
			except Exception as exc:
				downloadedUpdate = exc
			wx.CallAfter(dialog.finish, downloadedUpdate)

		threading.Thread(target=run, name="googleTtsForNvda.updateDownload", daemon=True).start()
		_pre_popup()
		try:
			dialog.ShowModal()
			return dialog.result
		finally:
			if dialog.result is None:
				dialog.cancelEvent.set()
			dialog.Destroy()
			_post_popup()

	def _finish_download(
		self,
		result: object,
		updateCheckResult: updater.UpdateCheckResult,
		parent: wx.Window,
	) -> None:
		if isinstance(result, updater.UpdateCancelled):
			updater.cleanup_update_files()
			_finish_update_check()
			return
		if isinstance(result, BaseException):
			updater.remove_update_manifest(updateCheckResult)
			log.warning(
				"Google TTS For NVDA update download failed: %s",
				result,
				exc_info=(type(result), result, result.__traceback__),
			)
			_finish_update_check()
			_message_box(
				_("Could not download the Google TTS For NVDA update. Check your internet connection and try again."),
				wx.OK | wx.ICON_ERROR,
				parent,
			)
			return
		if not isinstance(result, updater.DownloadedUpdate):
			updater.cleanup_update_files()
			log.warning("Google TTS For NVDA update download returned an unexpected result.")
			_finish_update_check()
			_message_box(
				_("Could not download the Google TTS For NVDA update. The download result was not understood."),
				wx.OK | wx.ICON_ERROR,
				parent,
			)
			return
		updater.remove_update_manifest(updateCheckResult)
		installError = False
		try:
			from gui import addonGui

			installed = addonGui.installAddon(parent, str(result.path))
		except Exception as exc:
			log.error("Could not install downloaded Google TTS For NVDA update: %s", exc, exc_info=True)
			installError = True
			installed = False
			_message_box(
				_("The update was downloaded, but NVDA could not start the add-on installer."),
				wx.OK | wx.ICON_ERROR,
				parent,
			)
		finally:
			updater.remove_downloaded_update(result)
			_finish_update_check()
		if installError:
			return
		if installed:
			try:
				wx.CallAfter(addonGui.promptUserForRestart)
			except Exception:
				log.debug("Could not show NVDA restart prompt after Google TTS For NVDA update.", exc_info=True)
		else:
			ui.message(_("Google TTS For NVDA update installation was cancelled or did not complete."))


_updateCheckLock = threading.Lock()
_updateCheckInProgress = False
_updateCheckMode: str | None = None


def _begin_update_check(mode: str) -> bool:
	global _updateCheckInProgress, _updateCheckMode
	with _updateCheckLock:
		if _updateCheckInProgress:
			return False
		_updateCheckInProgress = True
		_updateCheckMode = mode
	_notify_update_status_changed()
	return True


def _finish_update_check() -> None:
	global _updateCheckInProgress, _updateCheckMode
	with _updateCheckLock:
		_updateCheckInProgress = False
		_updateCheckMode = None
	_notify_update_status_changed()


def _start_update_check(mode: str, parent: wx.Window | None = None) -> bool:
	if mode == _CHECK_MODE_AUTOMATIC and not automatic_update_check_enabled():
		return False
	if not _begin_update_check(mode):
		if mode == _CHECK_MODE_MANUAL:
			ui.message(_("Google TTS For NVDA is already checking for updates."))
		return False
	controller = _UpdateCheckController(mode, parent)
	controller.start()
	return True


def start_manual_update_check(parent: wx.Window | None = None) -> bool:
	return _start_update_check(_CHECK_MODE_MANUAL, parent)


def start_automatic_update_check() -> bool:
	return _start_update_check(_CHECK_MODE_AUTOMATIC)
