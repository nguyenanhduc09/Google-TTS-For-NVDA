# -*- coding: utf-8 -*-
from __future__ import annotations

import addonHandler
import gui
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log
import synthDriverHandler
import ui
import wx

from synthDrivers.googleTtsForNvda import bridge as browserBridge


addonHandler.initTranslation()

SYNTH_NAME = "googleTtsForNvda"
_pendingRuntimeChange: str | None = None


def _runtime_label(runtime: str) -> str:
	return browserBridge.BROWSER_RUNTIME_LABELS.get(runtime, runtime)


def _is_google_synth_current() -> bool:
	try:
		return getattr(synthDriverHandler.getSynth(), "name", "") == SYNTH_NAME
	except Exception:
		return False


def _open_synthesizer_dialog(parent: wx.Window | None = None) -> bool:
	try:
		from gui import settingsDialogs

		dialogClass = getattr(settingsDialogs, "SynthesizerSelectionDialog", None)
		if dialogClass is None:
			dialogClass = getattr(settingsDialogs, "SynthesizerDialog", None)
		if dialogClass is None:
			raise RuntimeError("Select Synthesizer dialog class was not found.")
		gui.mainFrame.popupSettingsDialog(dialogClass)
		return True
	except Exception as exc:
		log.error("Could not open Select Synthesizer dialog: %s", exc)
		gui.messageBox(
			_("Could not open the Select Synthesizer dialog."),
			_("Google TTS For NVDA"),
			wx.OK | wx.ICON_ERROR,
			parent or gui.mainFrame,
		)
		return False


def _save_browser_runtime(runtime: str) -> None:
	saved = browserBridge.set_configured_browser_runtime(runtime)
	ui.message(
		_("Google TTS For NVDA browser runtime set to {runtime}.").format(
			runtime=_runtime_label(saved),
		),
	)


def _schedule_runtime_change_after_synth_switch(runtime: str, parent: wx.Window | None = None) -> None:
	global _pendingRuntimeChange
	_pendingRuntimeChange = runtime
	ui.message(_("Waiting for you to switch from Google TTS For NVDA to another synthesizer before changing the browser runtime."))

	def open_dialog_and_wait() -> None:
		if _open_synthesizer_dialog(parent):
			wx.CallLater(500, _apply_runtime_after_synth_switch, runtime, 0)
		else:
			_clear_pending_runtime_change(runtime)

	wx.CallAfter(open_dialog_and_wait)


def _clear_pending_runtime_change(runtime: str) -> None:
	global _pendingRuntimeChange
	if _pendingRuntimeChange == runtime:
		_pendingRuntimeChange = None


def _apply_runtime_after_synth_switch(runtime: str, attempts: int) -> None:
	global _pendingRuntimeChange
	if _pendingRuntimeChange != runtime:
		return
	if not _is_google_synth_current():
		_pendingRuntimeChange = None
		_save_browser_runtime(runtime)
		return
	if attempts >= 600:
		_pendingRuntimeChange = None
		ui.message(_("The browser runtime was not changed because Google TTS For NVDA is still the current synthesizer."))
		return
	wx.CallLater(500, _apply_runtime_after_synth_switch, runtime, attempts + 1)


class GoogleTtsSettingsPanel(SettingsPanel):
	title = _("Google TTS For NVDA")

	def makeSettings(self, settingsSizer: wx.Sizer) -> None:
		self._availability = browserBridge.browser_availability()
		self._runtimeValues = list(browserBridge.BROWSER_RUNTIMES)
		self._savedRuntime = browserBridge.configured_browser_runtime()
		self._effectiveRuntime = browserBridge.effective_browser_runtime(self._savedRuntime)

		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		choices = [self._format_runtime_choice(runtime) for runtime in self._runtimeValues]
		self.runtimeChoice = helper.addLabeledControl(
			_("Browser &runtime:"),
			wx.Choice,
			choices=choices,
		)
		self.runtimeChoice.SetSelection(self._runtimeValues.index(self._savedRuntime))
		self.runtimeChoice.SetName(_("Browser runtime"))
		self.effectiveRuntimeText = helper.addItem(wx.StaticText(self, label=self._effective_runtime_message()))
		settingsSizer.Fit(self)

	def postInit(self) -> None:
		self.runtimeChoice.SetFocus()

	def onSave(self) -> None:
		selection = self.runtimeChoice.GetSelection()
		if selection < 0:
			return
		selectedRuntime = self._runtimeValues[selection]
		if selectedRuntime == self._savedRuntime:
			return
		if not self._availability.get(selectedRuntime, False):
			ui.message(
				_("{runtime} was not found. Keeping the current Google TTS For NVDA browser runtime setting.").format(
					runtime=_runtime_label(selectedRuntime),
				),
			)
			self._select_saved_runtime()
			return

		effectiveRuntime = browserBridge.effective_browser_runtime(self._savedRuntime)
		if _is_google_synth_current() and selectedRuntime != effectiveRuntime:
			answer = gui.messageBox(
				_(
					"Google TTS For NVDA is currently using the browser runtime. "
					"Choose OK to open Select Synthesizer and switch to another synthesizer before changing the browser runtime. "
					"Choose Cancel to keep the current browser runtime."
				),
				_("Google TTS For NVDA"),
				wx.OK | wx.CANCEL | wx.ICON_WARNING,
				self,
			)
			if answer == wx.OK or answer == getattr(wx, "ID_OK", wx.OK):
				_schedule_runtime_change_after_synth_switch(selectedRuntime, self)
			self._select_saved_runtime()
			return

		_save_browser_runtime(selectedRuntime)
		self._savedRuntime = selectedRuntime
		self._effectiveRuntime = browserBridge.effective_browser_runtime(self._savedRuntime)
		self.effectiveRuntimeText.SetLabel(self._effective_runtime_message())

	def _format_runtime_choice(self, runtime: str) -> str:
		status = _("Available") if self._availability.get(runtime, False) else _("Unavailable")
		return _("{runtime} ({status})").format(runtime=_runtime_label(runtime), status=status)

	def _effective_runtime_message(self) -> str:
		if self._effectiveRuntime is None:
			return _("No supported browser was found.")
		return _("Active browser runtime: {runtime}").format(runtime=_runtime_label(self._effectiveRuntime))

	def _select_saved_runtime(self) -> None:
		self.runtimeChoice.SetSelection(self._runtimeValues.index(self._savedRuntime))
