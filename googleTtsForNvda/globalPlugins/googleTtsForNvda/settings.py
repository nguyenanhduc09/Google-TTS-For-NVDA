# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import OrderedDict
import json

import addonHandler
import config
import globalVars
import gui
from gui import guiHelper, nvdaControls
from gui.settingsDialogs import SettingsPanel
import languageHandler
from logHandler import log
import synthDriverHandler
import ui
import wx

from synthDrivers.googleTtsForNvda import bridge as browserBridge
from synthDrivers.googleTtsForNvda.catalog import Speaker, VoiceCatalog
from synthDrivers.googleTtsForNvda import voice_store
from . import updateGui
from .uiUtils import bind_read_only_text_focus_announcement, resize_read_only_text_for_content
from .voiceManager import get_language_display_name, _visible_language_sort_key


addonHandler.initTranslation()

SYNTH_NAME = "googleTtsForNvda"
_pendingRuntimeChange: str | None = None


def _normalize_language_code(language: str | None) -> str:
	return str(language or "").strip().replace("_", "-")


def _parse_language_codes(value: str | None) -> list[str]:
	codes: list[str] = []
	seen: set[str] = set()
	for rawCode in str(value or "").split(","):
		code = _normalize_language_code(rawCode)
		key = code.lower()
		if not code or key in seen:
			continue
		codes.append(code)
		seen.add(key)
	return codes


def _format_language_choice(language: str, count: int | None = None) -> str:
	languageName = get_language_display_name(language)
	if count is None:
		return languageName
	return _("{language} ({count} voices)").format(language=languageName, count=count)


def _nvda_synth_setting_name(settingFactory: object, fallback: str) -> str:
	try:
		setting = settingFactory()
		for attribute in ("displayName", "_displayName", "name"):
			value = getattr(setting, attribute, "")
			if value:
				return str(value).replace("&", "")
	except Exception:
		pass
	return fallback


def _nvda_label(message: str) -> str:
	try:
		translationRef = getattr(languageHandler, "installedTranslation", None)
		translation = translationRef() if translationRef is not None else None
		if translation is not None:
			return str(translation.gettext(message))
	except Exception:
		log.debug("Could not use NVDA translation for setting label.", exc_info=True)
	return message


def _bind_slider_page_keys(slider: wx.Slider) -> None:
	def on_char_hook(evt: wx.KeyEvent) -> None:
		keyCode = evt.GetKeyCode()
		if keyCode not in (wx.WXK_PAGEUP, wx.WXK_PAGEDOWN):
			evt.Skip()
			return
		delta = 10 if keyCode == wx.WXK_PAGEUP else -10
		value = max(slider.GetMin(), min(slider.GetMax(), slider.GetValue() + delta))
		if value != slider.GetValue():
			slider.SetValue(value)
			slider.GetEventHandler().ProcessEvent(
				wx.CommandEvent(wx.EVT_SLIDER.typeId, slider.GetId()),
			)

	slider.Bind(wx.EVT_CHAR_HOOK, on_char_hook)


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
			raise RuntimeError(_("Select Synthesizer dialog class was not found."))
		gui.mainFrame.popupSettingsDialog(dialogClass)
		return True
	except Exception as exc:
		log.error("Could not open Select Synthesizer dialog: %s", exc)
		gui.messageBox(
			_("The Select Synthesizer dialog could not be opened."),
			_("Google TTS For NVDA"),
			wx.OK | wx.ICON_ERROR,
			parent or gui.mainFrame,
		)
		return False


def _save_browser_runtime(runtime: str) -> None:
	saved = browserBridge.set_configured_browser_runtime(runtime)
	ui.message(
		_("Google TTS For NVDA will use {runtime} as its Chromium browser runtime.").format(
			runtime=_runtime_label(saved),
		),
	)


def _schedule_runtime_change_after_synth_switch(runtime: str, parent: wx.Window | None = None) -> None:
	global _pendingRuntimeChange
	_pendingRuntimeChange = runtime
	ui.message(_("Waiting for you to switch away from Google TTS For NVDA before changing the Chromium browser runtime."))

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
		ui.message(_("The Chromium browser runtime was left unchanged because Google TTS For NVDA is still the current synthesizer."))
		return
	wx.CallLater(500, _apply_runtime_after_synth_switch, runtime, attempts + 1)


class _SettingsGroup:
	def __init__(self, parent: wx.Window, parentHelper: guiHelper.BoxSizerHelper, label: str) -> None:
		self.sizer = wx.StaticBoxSizer(wx.VERTICAL, parent, label=label)
		self.box = self.sizer.GetStaticBox()
		self.box.SetName(label)
		self.helper = guiHelper.BoxSizerHelper(parent, sizer=self.sizer)
		parentHelper.addItem(self.helper)

	def addLabeledControl(self, labelText: str, wxCtrlClass: type, **kwargs: object) -> wx.Window:
		return self.helper.addLabeledControl(labelText, wxCtrlClass, **kwargs)

	def addCheckBox(self, label: str) -> wx.CheckBox:
		return self.helper.addItem(wx.CheckBox(self.box, label=label))

	def addButton(self, label: str) -> wx.Button:
		return self.helper.addItem(wx.Button(self.box, label=label))


class GoogleTtsSettingsPanel(SettingsPanel):
	title = _("Google TTS For NVDA")

	def _refresh_runtime_snapshot(self, runtime: str | None = None) -> None:
		self._runtimeSnapshot = browserBridge.browser_runtime_snapshot(runtime)
		self._availability = self._runtimeSnapshot["availability"]
		self._browserExecutableAvailability = self._runtimeSnapshot["executableAvailability"]
		self._edgeWebView2Available = self._runtimeSnapshot["edgeWebView2Available"]
		self._effectiveRuntime = self._runtimeSnapshot["effectiveRuntime"]

	def _add_settings_group(
		self,
		parentHelper: guiHelper.BoxSizerHelper,
		label: str,
	) -> _SettingsGroup:
		return _SettingsGroup(self, parentHelper, label)

	def _refresh_settings_layout(self) -> None:
		self._settingsSizer.Layout()
		self.Layout()
		self._sendLayoutUpdatedEvent()

	def makeSettings(self, settingsSizer: wx.Sizer, *args: object, **kwargs: object) -> None:
		self._settingsSizer = settingsSizer
		self._runtimeValues = list(browserBridge.BROWSER_RUNTIMES)
		self._refresh_runtime_snapshot()
		self._savedRuntime = self._runtimeSnapshot["selectedRuntime"]
		self._speakersByLanguage = self._installed_speakers_by_language()
		self._languageValues = list(self._speakersByLanguage)
		self._languageCounts = {
			language: len(speakers)
			for language, speakers in self._speakersByLanguage.items()
		}
		self._speechDefaults = self._current_speech_defaults()
		self._savedAutoLanguageDetection = self._configured_auto_language_detection()
		self._savedAutoLanguagePreferred = self._configured_auto_language_preferred()
		self._savedAutoLanguageCandidates = self._configured_auto_language_candidates()
		self._autoLanguageProfiles = self._configured_auto_language_profiles()
		self._ensure_auto_language_profiles()
		self._loadingAutoLanguageProfile = False
		self._voiceSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.VoiceSetting, "Voice")
		self._variantSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.VariantSetting, "Variant")
		self._rateSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.RateSetting, "Rate")
		self._rateBoostSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.RateBoostSetting, "Rate boost")
		self._pitchSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.PitchSetting, "Pitch")
		self._volumeSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.VolumeSetting, "Volume")
		self._capPitchSettingName = _nvda_label("Capital pitch change percentage")
		self._sayCapSettingName = _nvda_label("Say &cap before capitals")
		self._beepCapsSettingName = _nvda_label("&Beep for capitals")
		self._spellingSettingName = _nvda_label("Use &spelling functionality if supported")
		self._preferredLanguageValues = self._enabled_auto_language_candidates()

		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		runtimeGroup = self._add_settings_group(helper, _("Chromium browser runtime"))
		choices = [self._format_runtime_choice(runtime) for runtime in self._runtimeValues]
		self.runtimeChoice = runtimeGroup.addLabeledControl(
			_("Chromium browser &runtime:"),
			wx.Choice,
			choices=choices,
		)
		self.runtimeChoice.SetSelection(self._runtimeValues.index(self._savedRuntime))
		self.runtimeChoice.SetName(_("Chromium browser runtime"))
		self.runtimeChoice.Bind(wx.EVT_CHOICE, self.on_runtime_choice_changed)
		self.effectiveRuntimeText = runtimeGroup.addLabeledControl(
			_("Chromium browser runtime status") + ":",
			wx.TextCtrl,
			value=self._effective_runtime_message(),
			style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
		)
		self.effectiveRuntimeText.SetName(_("Chromium browser runtime status"))
		bind_read_only_text_focus_announcement(self.effectiveRuntimeText, minLines=2, maxLines=5)

		autoLanguageGroup = self._add_settings_group(helper, _("Automatic language profile"))
		self._autoLanguageSizer = autoLanguageGroup.sizer
		self.autoLanguageCheck = autoLanguageGroup.addCheckBox(_("&Use automatic language profiles"))
		self.autoLanguageCheck.SetName(_("Use automatic language profiles"))
		self.autoLanguageCheck.SetValue(self._savedAutoLanguageDetection and bool(self._languageValues))
		self.autoLanguageCheck.Bind(wx.EVT_CHECKBOX, self.on_auto_language_detection_changed)
		languageChoices = [
			_format_language_choice(language, self._languageCounts[language])
			for language in self._languageValues
		]
		preferredLanguageChoices = [
			_format_language_choice(language, self._languageCounts[language])
			for language in self._preferredLanguageValues
		]
		self.preferredLanguageChoice = autoLanguageGroup.addLabeledControl(
			_("Preferred profile &language:"),
			wx.Choice,
			choices=preferredLanguageChoices,
		)
		self.preferredLanguageChoice.SetName(_("Preferred profile language"))
		self._select_preferred_auto_language()
		self.autoProfileLanguageChoice = autoLanguageGroup.addLabeledControl(
			_("Automatic language &profile:"),
			wx.Choice,
			choices=languageChoices,
		)
		self.autoProfileLanguageChoice.SetName(_("Automatic language profile"))
		if self._languageValues:
			self.autoProfileLanguageChoice.SetSelection(0)
		self._selectedAutoLanguageProfileIndex = self.autoProfileLanguageChoice.GetSelection()
		self.autoProfileLanguageChoice.Bind(wx.EVT_CHOICE, self.on_auto_language_profile_changed)
		self.autoProfileEnabledCheck = autoLanguageGroup.addCheckBox(_("&Use this language profile"))
		self.autoProfileEnabledCheck.SetName(_("Use this language profile"))
		self.autoProfileEnabledCheck.Bind(wx.EVT_CHECKBOX, self.on_auto_language_profile_enabled_changed)
		self.autoProfileVoiceChoice = autoLanguageGroup.addLabeledControl(
			f"{self._variantSettingName}:",
			wx.Choice,
			choices=[],
		)
		self.autoProfileVoiceChoice.SetName(self._variantSettingName)
		self.autoProfileRateSlider = autoLanguageGroup.addLabeledControl(
			f"{self._rateSettingName}:",
			wx.Slider,
			value=50,
			minValue=0,
			maxValue=100,
		)
		self.autoProfileRateSlider.SetName(self._rateSettingName)
		_bind_slider_page_keys(self.autoProfileRateSlider)
		self.autoProfileRateBoostCheck = autoLanguageGroup.addCheckBox(self._rateBoostSettingName)
		self.autoProfileRateBoostCheck.SetName(self._rateBoostSettingName)
		self.autoProfilePitchSlider = autoLanguageGroup.addLabeledControl(
			f"{self._pitchSettingName}:",
			wx.Slider,
			value=50,
			minValue=0,
			maxValue=100,
		)
		self.autoProfilePitchSlider.SetName(self._pitchSettingName)
		_bind_slider_page_keys(self.autoProfilePitchSlider)
		self.autoProfileVolumeSlider = autoLanguageGroup.addLabeledControl(
			f"{self._volumeSettingName}:",
			wx.Slider,
			value=100,
			minValue=0,
			maxValue=100,
		)
		self.autoProfileVolumeSlider.SetName(self._volumeSettingName)
		_bind_slider_page_keys(self.autoProfileVolumeSlider)
		self.autoProfileCapPitchEdit = autoLanguageGroup.addLabeledControl(
			f"{self._capPitchSettingName}:",
			nvdaControls.SelectOnFocusSpinCtrl,
			min=-100,
			max=100,
			initial=30,
		)
		self.autoProfileCapPitchEdit.SetName(self._capPitchSettingName)
		self.autoProfileSayCapCheck = autoLanguageGroup.addCheckBox(self._sayCapSettingName)
		self.autoProfileSayCapCheck.SetName(self._sayCapSettingName.replace("&", ""))
		self.autoProfileBeepCapsCheck = autoLanguageGroup.addCheckBox(self._beepCapsSettingName)
		self.autoProfileBeepCapsCheck.SetName(self._beepCapsSettingName.replace("&", ""))
		self.autoProfileSpellingCheck = autoLanguageGroup.addCheckBox(self._spellingSettingName)
		self.autoProfileSpellingCheck.SetName(self._spellingSettingName.replace("&", ""))
		self._autoProfileValueControls = (
			self.autoProfileVoiceChoice,
			self.autoProfileRateSlider,
			self.autoProfileRateBoostCheck,
			self.autoProfilePitchSlider,
			self.autoProfileVolumeSlider,
			self.autoProfileCapPitchEdit,
			self.autoProfileSayCapCheck,
			self.autoProfileBeepCapsCheck,
			self.autoProfileSpellingCheck,
		)
		self._autoProfileValueLayoutItems = []
		for control in self._autoProfileValueControls:
			sizer = control.GetContainingSizer()
			self._autoProfileValueLayoutItems.append(
				sizer if sizer is not None and sizer is not self._autoLanguageSizer else control,
			)
		self._load_selected_auto_language_profile()
		self.autoLanguageStatusText = autoLanguageGroup.addLabeledControl(
			_("Automatic language profiles status") + ":",
			wx.TextCtrl,
			value=self._auto_language_status_message(),
			style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
		)
		self.autoLanguageStatusText.SetName(_("Automatic language profiles status"))
		bind_read_only_text_focus_announcement(self.autoLanguageStatusText, minLines=2, maxLines=5)

		self._unregisterUpdateStatusListener = None
		self._restoreFocusAfterUpdateCheck = False
		updatesGroup = self._add_settings_group(helper, _("Updates"))
		self.autoUpdateCheck = updatesGroup.addCheckBox(_("&Automatically check for add-on updates when NVDA starts"))
		self.autoUpdateCheck.SetName(_("Automatically check for add-on updates when NVDA starts"))
		self.autoUpdateCheck.SetValue(updateGui.automatic_update_check_enabled())
		self.autoUpdateCheck.Bind(wx.EVT_CHECKBOX, self.on_auto_update_check_changed)
		self.updateFeatureStatusText = updatesGroup.addLabeledControl(
			_("Add-on update status") + ":",
			wx.TextCtrl,
			value=updateGui.update_status_message(self.autoUpdateCheck.GetValue()),
			style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
		)
		self.updateFeatureStatusText.SetName(_("Add-on update status"))
		bind_read_only_text_focus_announcement(self.updateFeatureStatusText, minLines=2, maxLines=5)
		self.checkUpdatesButton = updatesGroup.addButton(_("&Check for updates..."))
		self.checkUpdatesButton.Bind(wx.EVT_BUTTON, self.on_check_for_updates)
		self._unregisterUpdateStatusListener = updateGui.register_update_status_listener(self._refresh_update_controls)
		self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
		self._refresh_update_controls()

		self._refresh_auto_language_controls()
		settingsSizer.Fit(self)

	def postInit(self) -> None:
		self.runtimeChoice.SetFocus()

	def _selected_runtime_choice(self) -> str:
		selection = self.runtimeChoice.GetSelection()
		if selection < 0:
			return self._savedRuntime
		return self._runtimeValues[selection]

	def onSave(self, *args: object, **kwargs: object) -> None:
		selectedRuntime = self._selected_runtime_choice()
		self._store_selected_auto_language_profile(self._selectedAutoLanguageProfileIndex)
		self._save_auto_language_settings()
		updateGui.set_automatic_update_check_enabled(self.autoUpdateCheck.GetValue())
		self._refresh_runtime_snapshot(self._savedRuntime)
		if selectedRuntime == self._savedRuntime:
			return
		if not self._availability.get(selectedRuntime, False):
			if (
				selectedRuntime == browserBridge.BROWSER_RUNTIME_EDGE
				and self._browserExecutableAvailability.get(selectedRuntime, False)
				and not self._edgeWebView2Available
			):
				ui.message(
					_(
						"Microsoft Edge was found, but Microsoft Edge WebView2 Runtime was not found. "
						"Keeping the current Google TTS For NVDA Chromium browser runtime setting."
					),
				)
				self._select_saved_runtime()
				self._refresh_runtime_status(self._savedRuntime)
				return
			ui.message(
				_("{runtime} was not found. Keeping the current Google TTS For NVDA Chromium browser runtime setting.").format(
					runtime=_runtime_label(selectedRuntime),
				),
			)
			self._select_saved_runtime()
			self._refresh_runtime_status(self._savedRuntime)
			return

		effectiveRuntime = self._effectiveRuntime
		if _is_google_synth_current() and selectedRuntime != effectiveRuntime:
			answer = gui.messageBox(
				_(
					"Google TTS For NVDA is using the selected Chromium browser runtime now. "
					"To change it safely, switch to another synthesizer first.\n\n"
					"Choose OK to open Select Synthesizer. "
					"Choose Cancel to keep the current Chromium browser runtime."
				),
				_("Google TTS For NVDA"),
				wx.OK | wx.CANCEL | wx.ICON_WARNING,
				self,
			)
			if answer == wx.OK or answer == getattr(wx, "ID_OK", wx.OK):
				_schedule_runtime_change_after_synth_switch(selectedRuntime, self)
			self._select_saved_runtime()
			self._refresh_runtime_status(self._savedRuntime)
			return

		_save_browser_runtime(selectedRuntime)
		self._savedRuntime = selectedRuntime
		self._refresh_runtime_status(self._savedRuntime)

	def on_runtime_choice_changed(self, evt: wx.CommandEvent) -> None:
		self._refresh_runtime_status(self._selected_runtime_choice())

	def _refresh_runtime_status(self, runtime: str) -> None:
		self._refresh_runtime_snapshot(runtime)
		self.effectiveRuntimeText.SetValue(self._effective_runtime_message())
		resize_read_only_text_for_content(self.effectiveRuntimeText, minLines=2, maxLines=5)
		self._refresh_settings_layout()

	def on_check_for_updates(self, evt: wx.CommandEvent) -> None:
		self._restoreFocusAfterUpdateCheck = True
		if updateGui.start_manual_update_check(self):
			self._refresh_update_controls()
		else:
			self._restoreFocusAfterUpdateCheck = False

	def on_auto_update_check_changed(self, evt: wx.CommandEvent) -> None:
		self._refresh_update_controls()

	def _refresh_update_controls(self) -> None:
		try:
			autoCheckEnabled = self.autoUpdateCheck.GetValue()
			updateInProgress = updateGui.update_check_in_progress()
			self.updateFeatureStatusText.SetValue(updateGui.update_status_message(autoCheckEnabled))
			resize_read_only_text_for_content(self.updateFeatureStatusText, minLines=2, maxLines=5)
			if updateInProgress and self.checkUpdatesButton.HasFocus():
				self.updateFeatureStatusText.SetFocus()
			self.checkUpdatesButton.Enable(not updateInProgress)
			self._refresh_settings_layout()
			if not updateInProgress and self._restoreFocusAfterUpdateCheck:
				wx.CallAfter(self._restore_update_check_focus)
		except (AttributeError, RuntimeError):
			return

	def _restore_update_check_focus(self) -> None:
		self._restoreFocusAfterUpdateCheck = False
		try:
			if self.checkUpdatesButton.IsEnabled() and self.checkUpdatesButton.IsShown():
				self.checkUpdatesButton.SetFocus()
		except RuntimeError:
			return

	def _on_destroy(self, evt: wx.WindowDestroyEvent) -> None:
		if evt.GetEventObject() is self and self._unregisterUpdateStatusListener is not None:
			self._unregisterUpdateStatusListener()
			self._unregisterUpdateStatusListener = None
		evt.Skip()

	def _format_runtime_choice(self, runtime: str) -> str:
		if runtime == browserBridge.BROWSER_RUNTIME_EDGE and self._browserExecutableAvailability.get(runtime, False):
			status = _("Available") if self._edgeWebView2Available else _("WebView2 Runtime missing")
		else:
			status = _("Available") if self._availability.get(runtime, False) else _("Unavailable")
		return _("{runtime} ({status})").format(runtime=_runtime_label(runtime), status=status)

	def _effective_runtime_message(self) -> str:
		selectedRuntime = self._runtimeSnapshot["selectedRuntime"]
		selectedLabel = _runtime_label(selectedRuntime)
		selectedExecutableStatus = _("found") if self._browserExecutableAvailability.get(selectedRuntime, False) else _("not found")
		if selectedRuntime == browserBridge.BROWSER_RUNTIME_EDGE:
			webView2Status = _("found") if self._edgeWebView2Available else _("not found")
			selectedMessage = _(
				"Selected Chromium browser runtime: {runtime}. Microsoft Edge: {edgeStatus}. "
				"Microsoft Edge WebView2 Runtime: {webView2Status}."
			).format(
				runtime=selectedLabel,
				edgeStatus=selectedExecutableStatus,
				webView2Status=webView2Status,
			)
		else:
			selectedMessage = _(
				"Selected Chromium browser runtime: {runtime}. {browser}: {browserStatus}."
			).format(runtime=selectedLabel, browser=selectedLabel, browserStatus=selectedExecutableStatus)
		if self._effectiveRuntime is None:
			return _("{selectedStatus} No supported Chromium browser runtime was found.").format(selectedStatus=selectedMessage)
		if self._effectiveRuntime == selectedRuntime:
			return _("{selectedStatus} Active Chromium browser runtime: {runtime}.").format(
				selectedStatus=selectedMessage,
				runtime=_runtime_label(self._effectiveRuntime),
			)
		return _("{selectedStatus} Falling back to active Chromium browser runtime: {runtime}.").format(
			selectedStatus=selectedMessage,
			runtime=_runtime_label(self._effectiveRuntime),
		)

	def _select_saved_runtime(self) -> None:
		self.runtimeChoice.SetSelection(self._runtimeValues.index(self._savedRuntime))

	def on_auto_language_detection_changed(self, evt: wx.CommandEvent) -> None:
		self._refresh_auto_language_controls()

	def on_auto_language_profile_changed(self, evt: wx.CommandEvent) -> None:
		if self._loadingAutoLanguageProfile:
			return
		self._store_selected_auto_language_profile(self._selectedAutoLanguageProfileIndex)
		self._load_selected_auto_language_profile()
		self._selectedAutoLanguageProfileIndex = self.autoProfileLanguageChoice.GetSelection()
		self._refresh_auto_language_controls()

	def on_auto_language_profile_enabled_changed(self, evt: wx.CommandEvent) -> None:
		if self._loadingAutoLanguageProfile:
			return
		self._store_selected_auto_language_profile(self._selectedAutoLanguageProfileIndex)
		self._refresh_preferred_language_choices()
		self._refresh_auto_language_controls()

	def _installed_speakers_by_language(self) -> "OrderedDict[str, list[Speaker]]":
		try:
			currentSynth = synthDriverHandler.getSynth()
			if getattr(currentSynth, "name", "") == SYNTH_NAME and hasattr(currentSynth, "catalog"):
				speakers = list(currentSynth.catalog.speakers)
			else:
				fullCatalog = VoiceCatalog.load()
				speakers = VoiceCatalog(voice_store.installed_packages(fullCatalog)).speakers
		except Exception:
			log.debug("Could not read installed Google TTS languages for settings.", exc_info=True)
			return OrderedDict()
		grouped: dict[str, list[Speaker]] = {}
		for speaker in speakers:
			language = _normalize_language_code(speaker.language)
			if not language:
				continue
			grouped.setdefault(language, []).append(speaker)
		return OrderedDict(
			sorted(
				grouped.items(),
				key=lambda item: _visible_language_sort_key(get_language_display_name(item[0])),
			),
		)

	def _current_speech_defaults(self) -> dict[str, object]:
		defaults: dict[str, object] = {
			"voice": "",
			"rate": 50,
			"rateBoost": False,
			"pitch": 50,
			"volume": 100,
			"capPitchChange": 30,
			"sayCapForCapitals": False,
			"beepForCapitals": False,
			"useSpellingFunctionality": True,
		}
		try:
			currentSynth = synthDriverHandler.getSynth()
			if getattr(currentSynth, "name", "") != SYNTH_NAME:
				return defaults
			defaults["voice"] = str(getattr(currentSynth, "variant", "") or getattr(currentSynth, "voice", "") or "")
			defaults["rate"] = max(0, min(100, int(getattr(currentSynth, "rate", 50))))
			defaults["rateBoost"] = bool(getattr(currentSynth, "rateBoost", False))
			defaults["pitch"] = max(0, min(100, int(getattr(currentSynth, "pitch", 50))))
			defaults["volume"] = max(0, min(100, int(getattr(currentSynth, "volume", 100))))
			synthConfig = config.conf["speech"][SYNTH_NAME]
			defaults["capPitchChange"] = self._profile_cap_pitch(synthConfig.get("capPitchChange"), 30)
			defaults["sayCapForCapitals"] = self._profile_bool(synthConfig.get("sayCapForCapitals"), False)
			defaults["beepForCapitals"] = self._profile_bool(synthConfig.get("beepForCapitals"), False)
			defaults["useSpellingFunctionality"] = self._profile_bool(synthConfig.get("useSpellingFunctionality"), True)
		except Exception:
			log.debug("Could not read current Google TTS speech settings.", exc_info=True)
		return defaults

	def _configured_auto_language_detection(self) -> bool:
		try:
			value = config.conf[browserBridge.CONFIG_SECTION][browserBridge.CONFIG_AUTO_LANGUAGE_DETECTION]
		except Exception:
			return browserBridge.DEFAULT_AUTO_LANGUAGE_DETECTION
		if isinstance(value, str):
			return value.strip().lower() in ("1", "true", "yes", "on")
		return bool(value)

	def _configured_auto_language_preferred(self) -> str:
		try:
			return _normalize_language_code(
				config.conf[browserBridge.CONFIG_SECTION][browserBridge.CONFIG_AUTO_LANGUAGE_PREFERRED],
			)
		except Exception:
			return browserBridge.DEFAULT_AUTO_LANGUAGE_PREFERRED

	def _configured_auto_language_candidates(self) -> list[str]:
		try:
			rawValue = config.conf[browserBridge.CONFIG_SECTION][browserBridge.CONFIG_AUTO_LANGUAGE_CANDIDATES]
		except Exception:
			rawValue = browserBridge.DEFAULT_AUTO_LANGUAGE_CANDIDATES
		return _parse_language_codes(rawValue)

	def _configured_auto_language_profiles(self) -> dict[str, dict[str, object]]:
		try:
			rawValue = config.conf[browserBridge.CONFIG_SECTION][browserBridge.CONFIG_AUTO_LANGUAGE_PROFILES]
		except Exception:
			rawValue = browserBridge.DEFAULT_AUTO_LANGUAGE_PROFILES
		try:
			parsed = json.loads(str(rawValue or "{}"))
		except (TypeError, ValueError):
			return {}
		if not isinstance(parsed, dict):
			return {}
		profiles: dict[str, dict[str, object]] = {}
		for rawLanguage, rawProfile in parsed.items():
			language = _normalize_language_code(rawLanguage)
			if not language or not isinstance(rawProfile, dict):
				continue
			profiles[language] = dict(rawProfile)
		return profiles

	def _selected_preferred_auto_language(self) -> str:
		index = self.preferredLanguageChoice.GetSelection()
		if 0 <= index < len(self._preferredLanguageValues):
			return self._preferredLanguageValues[index]
		return ""

	def _select_preferred_auto_language(self, preferred: str | None = None) -> None:
		if not self._preferredLanguageValues:
			self.preferredLanguageChoice.SetSelection(wx.NOT_FOUND)
			return
		if preferred is None:
			preferred = self._savedAutoLanguagePreferred
		if preferred not in self._preferredLanguageValues:
			try:
				currentSynth = synthDriverHandler.getSynth()
				if getattr(currentSynth, "name", "") == SYNTH_NAME and hasattr(currentSynth, "catalog"):
					currentVoice = str(getattr(currentSynth, "voice", "") or "")
					if currentVoice in getattr(currentSynth, "availableVoices", {}):
						preferred = currentVoice
					else:
						preferred = currentSynth.catalog.language_for_voice(currentVoice)
			except Exception:
				preferred = ""
		if preferred not in self._preferredLanguageValues:
			preferred = self._preferredLanguageValues[0]
		self.preferredLanguageChoice.SetSelection(self._preferredLanguageValues.index(preferred))

	def _refresh_preferred_language_choices(self) -> None:
		currentPreferred = self._selected_preferred_auto_language()
		self._preferredLanguageValues = self._enabled_auto_language_candidates()
		self.preferredLanguageChoice.Clear()
		for language in self._preferredLanguageValues:
			self.preferredLanguageChoice.Append(_format_language_choice(language, self._languageCounts[language]))
		self._select_preferred_auto_language(currentPreferred)

	def _ensure_auto_language_profiles(self) -> None:
		candidates = {language.lower() for language in self._savedAutoLanguageCandidates}
		for language in self._languageValues:
			profile = dict(self._autoLanguageProfiles.get(language, {}))
			if not profile:
				languageKey = language.lower()
				for configuredLanguage, configuredProfile in self._autoLanguageProfiles.items():
					if configuredLanguage.lower() == languageKey:
						profile = dict(configuredProfile)
						break
			profile.setdefault("enabled", language.lower() in candidates)
			profile["voice"] = self._valid_profile_variant(language, profile.get("voice"))
			profile["rate"] = self._profile_int(profile.get("rate"), int(self._speechDefaults["rate"]))
			profile["rateBoost"] = self._profile_bool(profile.get("rateBoost"), bool(self._speechDefaults["rateBoost"]))
			profile["pitch"] = self._profile_int(profile.get("pitch"), int(self._speechDefaults["pitch"]))
			profile["volume"] = self._profile_int(profile.get("volume"), int(self._speechDefaults["volume"]))
			profile["capPitchChange"] = self._profile_cap_pitch(
				profile.get("capPitchChange"),
				int(self._speechDefaults["capPitchChange"]),
			)
			profile["sayCapForCapitals"] = self._profile_bool(
				profile.get("sayCapForCapitals"),
				bool(self._speechDefaults["sayCapForCapitals"]),
			)
			profile["beepForCapitals"] = self._profile_bool(
				profile.get("beepForCapitals"),
				bool(self._speechDefaults["beepForCapitals"]),
			)
			profile["useSpellingFunctionality"] = self._profile_bool(
				profile.get("useSpellingFunctionality"),
				bool(self._speechDefaults["useSpellingFunctionality"]),
			)
			self._autoLanguageProfiles[language] = profile

	def _format_voice_choice(self, speaker: Speaker) -> str:
		return _("{voice} ({package})").format(voice=speaker.name, package=speaker.packageId)

	def _default_voice_for_language(self, language: str) -> str:
		currentVoice = str(self._speechDefaults.get("voice") or "")
		for speaker in self._speakersByLanguage.get(language, []):
			if speaker.id == currentVoice:
				return speaker.id
		speakers = self._speakersByLanguage.get(language, [])
		return speakers[0].id if speakers else ""

	def _valid_profile_variant(self, language: str, voice: object) -> str:
		voiceId = str(voice or "")
		for speaker in self._speakersByLanguage.get(language, []):
			if speaker.id == voiceId:
				return voiceId
		return self._default_voice_for_language(language)

	def _profile_int(self, value: object, default: int) -> int:
		try:
			return max(0, min(100, int(value)))
		except (TypeError, ValueError):
			return max(0, min(100, int(default)))

	def _profile_cap_pitch(self, value: object, default: int) -> int:
		try:
			return max(-100, min(100, int(value)))
		except (TypeError, ValueError):
			return max(-100, min(100, int(default)))

	def _profile_bool(self, value: object, default: bool = False) -> bool:
		if isinstance(value, str):
			return value.strip().lower() in ("1", "true", "yes", "on")
		if value is None:
			return default
		return bool(value)

	def _selected_auto_language_profile(self, index: int | None = None) -> str:
		if index is None:
			index = self.autoProfileLanguageChoice.GetSelection()
		if 0 <= index < len(self._languageValues):
			return self._languageValues[index]
		return ""

	def _load_selected_auto_language_profile(self) -> None:
		language = self._selected_auto_language_profile()
		if not language:
			return
		profile = self._autoLanguageProfiles[language]
		speakers = self._speakersByLanguage.get(language, [])
		self._loadingAutoLanguageProfile = True
		try:
			self.autoProfileVoiceChoice.Clear()
			for speaker in speakers:
				self.autoProfileVoiceChoice.Append(self._format_voice_choice(speaker))
			voice = self._valid_profile_variant(language, profile.get("voice"))
			voiceIndex = next((index for index, speaker in enumerate(speakers) if speaker.id == voice), wx.NOT_FOUND)
			self.autoProfileEnabledCheck.SetValue(bool(profile.get("enabled", False)))
			self.autoProfileVoiceChoice.SetSelection(voiceIndex)
			self.autoProfileRateSlider.SetValue(self._profile_int(profile.get("rate"), 50))
			self.autoProfileRateBoostCheck.SetValue(self._profile_bool(profile.get("rateBoost")))
			self.autoProfilePitchSlider.SetValue(self._profile_int(profile.get("pitch"), 50))
			self.autoProfileVolumeSlider.SetValue(self._profile_int(profile.get("volume"), 100))
			self.autoProfileCapPitchEdit.SetValue(self._profile_cap_pitch(profile.get("capPitchChange"), 30))
			self.autoProfileSayCapCheck.SetValue(self._profile_bool(profile.get("sayCapForCapitals")))
			self.autoProfileBeepCapsCheck.SetValue(self._profile_bool(profile.get("beepForCapitals")))
			self.autoProfileSpellingCheck.SetValue(self._profile_bool(profile.get("useSpellingFunctionality"), True))
		finally:
			self._loadingAutoLanguageProfile = False
		self._refresh_auto_language_profile_value_controls()

	def _store_selected_auto_language_profile(self, index: int | None = None) -> None:
		language = self._selected_auto_language_profile(index)
		if not language:
			return
		speakers = self._speakersByLanguage.get(language, [])
		voiceIndex = self.autoProfileVoiceChoice.GetSelection()
		voice = speakers[voiceIndex].id if 0 <= voiceIndex < len(speakers) else self._default_voice_for_language(language)
		self._autoLanguageProfiles[language] = {
			"enabled": bool(self.autoProfileEnabledCheck.GetValue()),
			"voice": voice,
			"rate": self._profile_int(self.autoProfileRateSlider.GetValue(), 50),
			"rateBoost": bool(self.autoProfileRateBoostCheck.GetValue()),
			"pitch": self._profile_int(self.autoProfilePitchSlider.GetValue(), 50),
			"volume": self._profile_int(self.autoProfileVolumeSlider.GetValue(), 100),
			"capPitchChange": self._profile_cap_pitch(self.autoProfileCapPitchEdit.Value, 30),
			"sayCapForCapitals": bool(self.autoProfileSayCapCheck.GetValue()),
			"beepForCapitals": bool(self.autoProfileBeepCapsCheck.GetValue()),
			"useSpellingFunctionality": bool(self.autoProfileSpellingCheck.GetValue()),
		}

	def _checked_auto_language_candidates(self) -> list[str]:
		return self._enabled_auto_language_candidates()

	def _enabled_auto_language_candidates(self) -> list[str]:
		return [
			language
			for language in self._languageValues
			if bool(self._autoLanguageProfiles.get(language, {}).get("enabled", False))
		]

	def _auto_language_status_message(self) -> str:
		if not self._languageValues:
			return _("Install at least one language voice package to use automatic language profiles.")
		if not self.autoLanguageCheck.GetValue():
			return _(
				"Automatic language profiles are off. "
				"Google TTS uses NVDA's normal Speech Settings for voice, variant, rate, pitch, volume, capitals, and spelling."
			)
		if not self._enabled_auto_language_candidates():
			return _("Select at least one language profile to use automatic language profiles.")
		return _(
			"Automatic language profiles use the selected installed language profiles. "
			"If only one language is selected, that profile is used for every sentence."
		)

	def _refresh_auto_language_controls(self) -> None:
		available = bool(self._languageValues)
		enabled = available and self.autoLanguageCheck.GetValue()
		profileEnabled = enabled and self.autoProfileEnabledCheck.GetValue()
		self.autoLanguageCheck.Enable(available)
		self.preferredLanguageChoice.Enable(enabled and bool(self._preferredLanguageValues))
		self.autoProfileLanguageChoice.Enable(enabled)
		self.autoProfileEnabledCheck.Enable(enabled)
		self.autoProfileVoiceChoice.Enable(profileEnabled)
		self.autoProfileRateSlider.Enable(profileEnabled)
		self.autoProfileRateBoostCheck.Enable(profileEnabled)
		self.autoProfilePitchSlider.Enable(profileEnabled)
		self.autoProfileVolumeSlider.Enable(profileEnabled)
		self.autoProfileCapPitchEdit.Enable(profileEnabled)
		self.autoProfileSayCapCheck.Enable(profileEnabled)
		self.autoProfileBeepCapsCheck.Enable(profileEnabled)
		self.autoProfileSpellingCheck.Enable(profileEnabled)
		self._refresh_auto_language_profile_value_controls()
		self.autoLanguageStatusText.SetValue(self._auto_language_status_message())
		resize_read_only_text_for_content(self.autoLanguageStatusText, minLines=2, maxLines=5)
		self._refresh_settings_layout()
		if not available:
			self.autoLanguageCheck.SetValue(False)

	def _refresh_auto_language_profile_value_controls(self) -> None:
		if not hasattr(self, "_autoProfileValueLayoutItems"):
			return
		show = (
			bool(self._languageValues)
			and self.autoLanguageCheck.GetValue()
			and self.autoProfileEnabledCheck.GetValue()
		)
		for item in self._autoProfileValueLayoutItems:
			self._autoLanguageSizer.Show(item, show, recursive=True)
		self._autoLanguageSizer.Layout()

	def _save_auto_language_settings(self) -> None:
		wasEnabled = self._configured_auto_language_detection()
		enabled = self.autoLanguageCheck.GetValue() and bool(self._languageValues)
		candidates = self._checked_auto_language_candidates()
		preferredIndex = self.preferredLanguageChoice.GetSelection()
		preferred = self._preferredLanguageValues[preferredIndex] if 0 <= preferredIndex < len(self._preferredLanguageValues) else ""
		if preferred not in candidates:
			preferred = candidates[0] if candidates else ""
		if enabled and not candidates:
			enabled = False
			ui.message(_("Automatic language profiles were disabled because no languages are selected."))
		profiles = {
			language: {
				"enabled": bool(profile.get("enabled", False)),
				"voice": str(profile.get("voice") or ""),
				"rate": self._profile_int(profile.get("rate"), 50),
				"rateBoost": bool(profile.get("rateBoost", False)),
				"pitch": self._profile_int(profile.get("pitch"), 50),
				"volume": self._profile_int(profile.get("volume"), 100),
				"capPitchChange": self._profile_cap_pitch(profile.get("capPitchChange"), 30),
				"sayCapForCapitals": bool(profile.get("sayCapForCapitals", False)),
				"beepForCapitals": bool(profile.get("beepForCapitals", False)),
				"useSpellingFunctionality": bool(profile.get("useSpellingFunctionality", True)),
			}
			for language, profile in self._autoLanguageProfiles.items()
			if language in self._languageValues
		}
		try:
			section = config.conf[browserBridge.CONFIG_SECTION]
			section[browserBridge.CONFIG_AUTO_LANGUAGE_DETECTION] = enabled
			section[browserBridge.CONFIG_AUTO_LANGUAGE_PREFERRED] = preferred
			section[browserBridge.CONFIG_AUTO_LANGUAGE_CANDIDATES] = ",".join(candidates)
			section[browserBridge.CONFIG_AUTO_LANGUAGE_PROFILES] = json.dumps(profiles, ensure_ascii=False, sort_keys=True)
			self._refresh_synth_settings_ring(reloadSpeechSettings=wasEnabled and not enabled)
			self._savedAutoLanguageDetection = enabled
		except Exception:
			log.debug("Could not save Google TTS automatic language profile settings.", exc_info=True)

	def _refresh_synth_settings_ring(self, reloadSpeechSettings: bool = False) -> None:
		try:
			currentSynth = synthDriverHandler.getSynth()
			settingsRing = getattr(globalVars, "settingsRing", None)
			if getattr(currentSynth, "name", "") != SYNTH_NAME:
				return
			if reloadSpeechSettings:
				# Turning automatic language profiles off makes the normal synth
				# settings visible again; reload their saved values into the live
				# synth before rebuilding the settings ring.
				currentSynth.loadSettings(onlyChanged=True)
			if settingsRing is not None:
				settingsRing.updateSupportedSettings(currentSynth)
			warmCurrentVoice = getattr(currentSynth, "_warm_current_voice_async", None)
			if callable(warmCurrentVoice):
				warmCurrentVoice()
		except Exception:
			log.debug("Could not refresh Google TTS supported speech settings.", exc_info=True)
