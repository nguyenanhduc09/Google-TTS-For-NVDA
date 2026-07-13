# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import OrderedDict
import json

import addonHandler
import config
import globalVars
import gui
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log
import synthDriverHandler
import ui
import wx

from synthDrivers.googleTtsForNvda import bridge as browserBridge
from synthDrivers.googleTtsForNvda.catalog import Speaker, VoiceCatalog
from synthDrivers.googleTtsForNvda import voice_store
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


def _focusable_status_text(parent: wx.Window, message: str, name: str) -> wx.TextCtrl:
	control = wx.TextCtrl(
		parent,
		value=message,
		style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
	)
	control.SetName(name)
	return control


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
		_("Google TTS For NVDA will use {runtime} as its browser runtime.").format(
			runtime=_runtime_label(saved),
		),
	)


def _schedule_runtime_change_after_synth_switch(runtime: str, parent: wx.Window | None = None) -> None:
	global _pendingRuntimeChange
	_pendingRuntimeChange = runtime
	ui.message(_("Waiting for you to switch away from Google TTS For NVDA before changing the browser runtime."))

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
		ui.message(_("The browser runtime was left unchanged because Google TTS For NVDA is still the current synthesizer."))
		return
	wx.CallLater(500, _apply_runtime_after_synth_switch, runtime, attempts + 1)


class GoogleTtsSettingsPanel(SettingsPanel):
	title = _("Google TTS For NVDA")

	def makeSettings(self, settingsSizer: wx.Sizer) -> None:
		self._settingsSizer = settingsSizer
		self._availability = browserBridge.browser_availability()
		self._runtimeValues = list(browserBridge.BROWSER_RUNTIMES)
		self._savedRuntime = browserBridge.configured_browser_runtime()
		self._effectiveRuntime = browserBridge.effective_browser_runtime(self._savedRuntime)
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
		self._rateSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.RateSetting, "Rate")
		self._rateBoostSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.RateBoostSetting, "Rate boost")
		self._pitchSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.PitchSetting, "Pitch")
		self._volumeSettingName = _nvda_synth_setting_name(synthDriverHandler.SynthDriver.VolumeSetting, "Volume")
		self._preferredLanguageValues = self._enabled_auto_language_candidates()

		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		choices = [self._format_runtime_choice(runtime) for runtime in self._runtimeValues]
		self.runtimeChoice = helper.addLabeledControl(
			_("Browser &runtime:"),
			wx.Choice,
			choices=choices,
		)
		self.runtimeChoice.SetSelection(self._runtimeValues.index(self._savedRuntime))
		self.runtimeChoice.SetName(_("Browser runtime"))
		self.effectiveRuntimeText = helper.addItem(
			_focusable_status_text(self, self._effective_runtime_message(), _("Browser runtime status")),
			flag=wx.EXPAND,
		)

		self.autoLanguageCheck = helper.addItem(
			wx.CheckBox(self, label=_("&Automatically detect language between installed voices")),
		)
		self.autoLanguageCheck.SetName(_("Automatically detect language"))
		self.autoLanguageCheck.SetValue(self._savedAutoLanguageDetection and len(self._languageValues) >= 2)
		self.autoLanguageCheck.Bind(wx.EVT_CHECKBOX, self.on_auto_language_detection_changed)
		languageChoices = [
			_format_language_choice(language, self._languageCounts[language])
			for language in self._languageValues
		]
		preferredLanguageChoices = [
			_format_language_choice(language, self._languageCounts[language])
			for language in self._preferredLanguageValues
		]
		self.preferredLanguageChoice = helper.addLabeledControl(
			_("Preferred auto-detect &language:"),
			wx.Choice,
			choices=preferredLanguageChoices,
		)
		self.preferredLanguageChoice.SetName(_("Preferred auto-detect language"))
		self._select_preferred_auto_language()
		self.autoProfileLanguageChoice = helper.addLabeledControl(
			_("Auto-detect language &profile:"),
			wx.Choice,
			choices=languageChoices,
		)
		self.autoProfileLanguageChoice.SetName(_("Auto-detect language profile"))
		if self._languageValues:
			self.autoProfileLanguageChoice.SetSelection(0)
		self._selectedAutoLanguageProfileIndex = self.autoProfileLanguageChoice.GetSelection()
		self.autoProfileLanguageChoice.Bind(wx.EVT_CHOICE, self.on_auto_language_profile_changed)
		self.autoProfileEnabledCheck = helper.addItem(
			wx.CheckBox(self, label=_("&Use this language in auto-detect")),
		)
		self.autoProfileEnabledCheck.SetName(_("Use this language in auto-detect"))
		self.autoProfileEnabledCheck.Bind(wx.EVT_CHECKBOX, self.on_auto_language_profile_enabled_changed)
		self.autoProfileVoiceChoice = helper.addLabeledControl(
			f"{self._voiceSettingName}:",
			wx.Choice,
			choices=[],
		)
		self.autoProfileVoiceChoice.SetName(self._voiceSettingName)
		self.autoProfileRateSlider = helper.addLabeledControl(
			f"{self._rateSettingName}:",
			wx.Slider,
			value=50,
			minValue=0,
			maxValue=100,
		)
		self.autoProfileRateSlider.SetName(self._rateSettingName)
		_bind_slider_page_keys(self.autoProfileRateSlider)
		self.autoProfileRateBoostCheck = helper.addItem(
			wx.CheckBox(self, label=self._rateBoostSettingName),
		)
		self.autoProfileRateBoostCheck.SetName(self._rateBoostSettingName)
		self.autoProfilePitchSlider = helper.addLabeledControl(
			f"{self._pitchSettingName}:",
			wx.Slider,
			value=50,
			minValue=0,
			maxValue=100,
		)
		self.autoProfilePitchSlider.SetName(self._pitchSettingName)
		_bind_slider_page_keys(self.autoProfilePitchSlider)
		self.autoProfileVolumeSlider = helper.addLabeledControl(
			f"{self._volumeSettingName}:",
			wx.Slider,
			value=100,
			minValue=0,
			maxValue=100,
		)
		self.autoProfileVolumeSlider.SetName(self._volumeSettingName)
		_bind_slider_page_keys(self.autoProfileVolumeSlider)
		self._autoProfileValueControls = (
			self.autoProfileVoiceChoice,
			self.autoProfileRateSlider,
			self.autoProfileRateBoostCheck,
			self.autoProfilePitchSlider,
			self.autoProfileVolumeSlider,
		)
		self._load_selected_auto_language_profile()
		self.autoLanguageStatusText = helper.addItem(
			_focusable_status_text(self, self._auto_language_status_message(), _("Auto-detect language status")),
			flag=wx.EXPAND,
		)
		self._refresh_auto_language_controls()
		settingsSizer.Fit(self)

	def postInit(self) -> None:
		self.runtimeChoice.SetFocus()

	def onSave(self) -> None:
		selection = self.runtimeChoice.GetSelection()
		if selection < 0:
			selectedRuntime = self._savedRuntime
		else:
			selectedRuntime = self._runtimeValues[selection]
		self._store_selected_auto_language_profile(self._selectedAutoLanguageProfileIndex)
		self._save_auto_language_settings()
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
					"Google TTS For NVDA is using the selected browser runtime now. "
					"To change it safely, switch to another synthesizer first.\n\n"
					"Choose OK to open Select Synthesizer. "
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
		self.effectiveRuntimeText.SetValue(self._effective_runtime_message())

	def _format_runtime_choice(self, runtime: str) -> str:
		status = _("Available") if self._availability.get(runtime, False) else _("Unavailable")
		return _("{runtime} ({status})").format(runtime=_runtime_label(runtime), status=status)

	def _effective_runtime_message(self) -> str:
		if self._effectiveRuntime is None:
			return _("No supported browser runtime was found.")
		return _("Active browser runtime: {runtime}").format(runtime=_runtime_label(self._effectiveRuntime))

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
		}
		try:
			currentSynth = synthDriverHandler.getSynth()
			if getattr(currentSynth, "name", "") != SYNTH_NAME:
				return defaults
			defaults["voice"] = str(getattr(currentSynth, "voice", "") or "")
			defaults["rate"] = max(0, min(100, int(getattr(currentSynth, "rate", 50))))
			defaults["rateBoost"] = bool(getattr(currentSynth, "rateBoost", False))
			defaults["pitch"] = max(0, min(100, int(getattr(currentSynth, "pitch", 50))))
			defaults["volume"] = max(0, min(100, int(getattr(currentSynth, "volume", 100))))
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
				currentVoice = synthDriverHandler.getSynth().voice
				currentSynth = synthDriverHandler.getSynth()
				if getattr(currentSynth, "name", "") == SYNTH_NAME and hasattr(currentSynth, "catalog"):
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
			profile["voice"] = self._valid_profile_voice(language, profile.get("voice"))
			profile["rate"] = self._profile_int(profile.get("rate"), int(self._speechDefaults["rate"]))
			profile["rateBoost"] = self._profile_bool(profile.get("rateBoost"), bool(self._speechDefaults["rateBoost"]))
			profile["pitch"] = self._profile_int(profile.get("pitch"), int(self._speechDefaults["pitch"]))
			profile["volume"] = self._profile_int(profile.get("volume"), int(self._speechDefaults["volume"]))
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

	def _valid_profile_voice(self, language: str, voice: object) -> str:
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
			voice = self._valid_profile_voice(language, profile.get("voice"))
			voiceIndex = next((index for index, speaker in enumerate(speakers) if speaker.id == voice), wx.NOT_FOUND)
			self.autoProfileEnabledCheck.SetValue(bool(profile.get("enabled", False)))
			self.autoProfileVoiceChoice.SetSelection(voiceIndex)
			self.autoProfileRateSlider.SetValue(self._profile_int(profile.get("rate"), 50))
			self.autoProfileRateBoostCheck.SetValue(self._profile_bool(profile.get("rateBoost")))
			self.autoProfilePitchSlider.SetValue(self._profile_int(profile.get("pitch"), 50))
			self.autoProfileVolumeSlider.SetValue(self._profile_int(profile.get("volume"), 100))
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
		if len(self._languageValues) < 2:
			return _("Install at least two language voice packages to use auto-detect.")
		return _(
			"Auto-detect switches by sentence using the selected installed languages. "
			"The preferred language is used when a sentence is unclear."
		)

	def _refresh_auto_language_controls(self) -> None:
		available = len(self._languageValues) >= 2
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
		self._refresh_auto_language_profile_value_controls()
		self.autoLanguageStatusText.SetValue(self._auto_language_status_message())
		if not available:
			self.autoLanguageCheck.SetValue(False)

	def _refresh_auto_language_profile_value_controls(self) -> None:
		if not hasattr(self, "_autoProfileValueControls"):
			return
		show = (
			len(self._languageValues) >= 2
			and self.autoLanguageCheck.GetValue()
			and self.autoProfileEnabledCheck.GetValue()
		)
		for control in self._autoProfileValueControls:
			sizer = control.GetContainingSizer()
			if sizer is not None and sizer is not self._settingsSizer:
				self._settingsSizer.Show(sizer, show, recursive=True)
			else:
				self._settingsSizer.Show(control, show, recursive=True)
		self.Layout()
		self._settingsSizer.Layout()

	def _save_auto_language_settings(self) -> None:
		enabled = self.autoLanguageCheck.GetValue() and len(self._languageValues) >= 2
		candidates = self._checked_auto_language_candidates()
		preferredIndex = self.preferredLanguageChoice.GetSelection()
		preferred = self._preferredLanguageValues[preferredIndex] if 0 <= preferredIndex < len(self._preferredLanguageValues) else ""
		if preferred not in candidates:
			preferred = candidates[0] if candidates else ""
		if enabled and len(candidates) < 2:
			enabled = False
			ui.message(_("Auto-detect language was disabled because fewer than two languages are selected."))
		profiles = {
			language: {
				"enabled": bool(profile.get("enabled", False)),
				"voice": str(profile.get("voice") or ""),
				"rate": self._profile_int(profile.get("rate"), 50),
				"rateBoost": bool(profile.get("rateBoost", False)),
				"pitch": self._profile_int(profile.get("pitch"), 50),
				"volume": self._profile_int(profile.get("volume"), 100),
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
			self._refresh_synth_settings_ring()
		except Exception:
			log.debug("Could not save Google TTS auto-detect language settings.", exc_info=True)

	def _refresh_synth_settings_ring(self) -> None:
		try:
			currentSynth = synthDriverHandler.getSynth()
			settingsRing = getattr(globalVars, "settingsRing", None)
			if getattr(currentSynth, "name", "") == SYNTH_NAME and settingsRing is not None:
				settingsRing.updateSupportedSettings(currentSynth)
		except Exception:
			log.debug("Could not refresh Google TTS supported speech settings.", exc_info=True)
