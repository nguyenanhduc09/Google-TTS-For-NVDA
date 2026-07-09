# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import os
import threading
from typing import Any
import urllib.error

import addonHandler
import config
import gui
import synthDriverHandler
import ui
import wx
from gui import nvdaControls
from logHandler import log

from synthDrivers.googleTtsForNvda.catalog import VoiceCatalog, VoicePackage
from synthDrivers.googleTtsForNvda import voice_store


addonHandler.initTranslation()

SYNTH_NAME = "googleTtsForNvda"

LANGUAGE_NAMES: dict[str, str] = {
	"ar-XA": _("Arabic"),
	"as-IN": _("Assamese"),
	"bg-BG": _("Bulgarian"),
	"bn-BD": _("Bengali (Bangladesh)"),
	"bn-IN": _("Bengali (India)"),
	"brx-IN": _("Bodo"),
	"bs-BA": _("Bosnian"),
	"ca-ES": _("Catalan"),
	"cmn-CN": _("Mandarin Chinese (China)"),
	"cmn-TW": _("Mandarin Chinese (Taiwan)"),
	"cs-CZ": _("Czech"),
	"cy-GB": _("Welsh"),
	"da-DK": _("Danish"),
	"de-DE": _("German"),
	"doi-IN": _("Dogri"),
	"el-GR": _("Greek"),
	"en-AU": _("English (Australia)"),
	"en-GB": _("English (UK)"),
	"en-IN": _("English (India)"),
	"en-NG": _("English (Nigeria)"),
	"en-US": _("English (US)"),
	"es-ES": _("Spanish (Spain)"),
	"es-US": _("Spanish (US)"),
	"et-EE": _("Estonian"),
	"fi-FI": _("Finnish"),
	"fil-PH": _("Filipino"),
	"fr-CA": _("French (Canada)"),
	"fr-FR": _("French (France)"),
	"gu-IN": _("Gujarati"),
	"he-IL": _("Hebrew"),
	"hi-IN": _("Hindi"),
	"hr-HR": _("Croatian"),
	"hu-HU": _("Hungarian"),
	"id-ID": _("Indonesian"),
	"is-IS": _("Icelandic"),
	"it-IT": _("Italian"),
	"ja-JP": _("Japanese"),
	"jv-ID": _("Javanese"),
	"km-KH": _("Khmer"),
	"kn-IN": _("Kannada"),
	"ko-KR": _("Korean"),
	"kok-IN": _("Konkani"),
	"ks-IN": _("Kashmiri"),
	"lt-LT": _("Lithuanian"),
	"lv-LV": _("Latvian"),
	"mai-IN": _("Maithili"),
	"ml-IN": _("Malayalam"),
	"mni-IN": _("Manipuri"),
	"mr-IN": _("Marathi"),
	"ms-MY": _("Malay"),
	"nb-NO": _("Norwegian Bokmål"),
	"ne-NP": _("Nepali"),
	"nl-BE": _("Flemish"),
	"nl-NL": _("Dutch"),
	"or-IN": _("Odia"),
	"pa-IN": _("Punjabi"),
	"pl-PL": _("Polish"),
	"pt-BR": _("Portuguese (Brazil)"),
	"pt-PT": _("Portuguese (Portugal)"),
	"ro-RO": _("Romanian"),
	"ru-RU": _("Russian"),
	"sa-IN": _("Sanskrit"),
	"sat-IN": _("Santali"),
	"sd-IN": _("Sindhi"),
	"si-LK": _("Sinhala"),
	"sk-SK": _("Slovak"),
	"sl-SI": _("Slovenian"),
	"sq-AL": _("Albanian"),
	"sr-RS": _("Serbian"),
	"su-ID": _("Sundanese"),
	"sv-SE": _("Swedish"),
	"sw-KE": _("Swahili"),
	"ta-IN": _("Tamil"),
	"te-IN": _("Telugu"),
	"th-TH": _("Thai"),
	"tr-TR": _("Turkish"),
	"uk-UA": _("Ukrainian"),
	"ur-IN": _("Urdu (India)"),
	"ur-PK": _("Urdu (Pakistan)"),
	"vi-VN": _("Vietnamese"),
	"yue-HK": _("Cantonese"),
}


def get_language_display_name(lang_code: str) -> str:
	for k, v in LANGUAGE_NAMES.items():
		if k.lower() == lang_code.lower():
			return v
	return lang_code


class VoiceManagerDialog(nvdaControls.DPIScaledDialog):
	def __init__(
		self,
		parent: wx.Window,
		onDestroy: Callable[["VoiceManagerDialog"], None],
		initialPage: str = "installed",
	) -> None:
		super().__init__(
			parent,
			title=_("Google TTS Voice Manager"),
			size=(880, 640),
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
		)
		self._onDestroy = onDestroy
		self.catalog = VoiceCatalog.load()
		self.installedPackages: list[VoicePackage] = []
		self.downloadPackages: list[VoicePackage] = []
		self._allInstalledPackages: list[VoicePackage] = []
		self._allUsableInstalledPackages: list[VoicePackage] = []
		self._allDownloadPackages: list[VoicePackage] = []
		self.isBusy = False
		self._pendingRemoveAfterSynthSwitch: list[VoicePackage] | None = None
		self._initialPage = initialPage
		self._lastProgressAnnouncement = -1
		self._build_ui()
		self.SetMinSize((720, 520))
		self.SetEscapeId(wx.ID_CLOSE)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.Bind(wx.EVT_WINDOW_DESTROY, self.on_destroy)
		self.refresh_lists()
		wx.CallAfter(self.focus_default_control)

	def _build_ui(self) -> None:
		root = wx.BoxSizer(wx.VERTICAL)
		self.SetSizer(root)

		self.notebook = wx.Notebook(self)
		root.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 10)

		self.installedPanel = wx.Panel(self.notebook)
		self.downloadPanel = wx.Panel(self.notebook)
		self.notebook.AddPage(self.installedPanel, _("Installed"))
		self.notebook.AddPage(self.downloadPanel, _("Download"))
		self._build_installed_tab()
		self._build_download_tab()
		self.notebook.Bind(wx.EVT_KEY_DOWN, self._on_notebook_key_down)

		statusRow = wx.BoxSizer(wx.HORIZONTAL)
		self.statusText = wx.StaticText(self, label=_("Ready."))
		self.statusText.SetName(_("Status"))
		self.progressGauge = wx.Gauge(self, range=100)
		self.progressGauge.SetName(_("Progress"))
		self.progressGauge.SetValue(0)
		statusRow.Add(self.statusText, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
		statusRow.Add(self.progressGauge, 0, wx.ALIGN_CENTER_VERTICAL)
		root.Add(statusRow, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

		buttonRow = wx.BoxSizer(wx.HORIZONTAL)
		self.refreshButton = wx.Button(self, label=_("&Refresh"))
		self.openFolderButton = wx.Button(self, label=_("&Open voice packages folder"))
		self.closeButton = wx.Button(self, id=wx.ID_CLOSE)
		self.refreshButton.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_lists())
		self.openFolderButton.Bind(wx.EVT_BUTTON, self.on_open_folder)
		self.closeButton.Bind(wx.EVT_BUTTON, lambda evt: self.Close())
		buttonRow.Add(self.refreshButton)
		buttonRow.AddSpacer(8)
		buttonRow.Add(self.openFolderButton)
		buttonRow.AddStretchSpacer()
		buttonRow.Add(self.closeButton)
		root.Add(buttonRow, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

	def _build_installed_tab(self) -> None:
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.installedPanel.SetSizer(sizer)

		filterRow = wx.BoxSizer(wx.HORIZONTAL)
		self.installedFilterLabel = wx.StaticText(self.installedPanel, label=_("&Filter by language:"))
		self.installedLanguageCombo = wx.Choice(self.installedPanel)
		self.installedLanguageCombo.SetName(_("Filter installed voice packages by language"))
		self.installedLanguageCombo.Bind(wx.EVT_CHOICE, self.on_installed_language_filter_changed)
		filterRow.Add(self.installedFilterLabel, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
		filterRow.Add(self.installedLanguageCombo, 1, wx.ALIGN_CENTER_VERTICAL)
		sizer.Add(filterRow, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

		self.installedSelectAllCheck = wx.CheckBox(
			self.installedPanel, label=_("Select &all voice packages"),
		)
		self.installedSelectAllCheck.Bind(wx.EVT_CHECKBOX, self.on_installed_select_all)
		sizer.Add(self.installedSelectAllCheck, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.installedList = self._create_list(self.installedPanel, includeStatus=True)
		self.installedList.SetName(_("Installed voice packages"))
		self.installedList.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_installed_item_check_changed)
		self.installedList.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_installed_item_check_changed)
		sizer.Add(self.installedList, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		buttonRow = wx.BoxSizer(wx.HORIZONTAL)
		self.removeButton = wx.Button(self.installedPanel, label=_("&Remove checked voice packages"))
		self.removeButton.Bind(wx.EVT_BUTTON, self.on_remove_selected)
		buttonRow.Add(self.removeButton)
		sizer.Add(buttonRow, 0, wx.EXPAND | wx.ALL, 8)

	def _build_download_tab(self) -> None:
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.downloadPanel.SetSizer(sizer)

		filterRow = wx.BoxSizer(wx.HORIZONTAL)
		self.downloadFilterLabel = wx.StaticText(self.downloadPanel, label=_("&Filter by language:"))
		self.downloadLanguageCombo = wx.Choice(self.downloadPanel)
		self.downloadLanguageCombo.SetName(_("Filter downloadable voice packages by language"))
		self.downloadLanguageCombo.Bind(wx.EVT_CHOICE, self.on_download_language_filter_changed)
		filterRow.Add(self.downloadFilterLabel, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
		filterRow.Add(self.downloadLanguageCombo, 1, wx.ALIGN_CENTER_VERTICAL)
		sizer.Add(filterRow, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

		self.downloadSelectAllCheck = wx.CheckBox(
			self.downloadPanel, label=_("Select &all voice packages"),
		)
		self.downloadSelectAllCheck.Bind(wx.EVT_CHECKBOX, self.on_download_select_all)
		sizer.Add(self.downloadSelectAllCheck, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.downloadList = self._create_list(self.downloadPanel, includeStatus=False)
		self.downloadList.SetName(_("Downloadable voice packages"))
		self.downloadList.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_download_item_check_changed)
		self.downloadList.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_download_item_check_changed)
		sizer.Add(self.downloadList, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		buttonRow = wx.BoxSizer(wx.HORIZONTAL)
		self.downloadButton = wx.Button(self.downloadPanel, label=_("&Download checked voice packages"))
		self.downloadButton.Bind(wx.EVT_BUTTON, self.on_download_selected)
		buttonRow.Add(self.downloadButton)
		sizer.Add(buttonRow, 0, wx.EXPAND | wx.ALL, 8)

	def _create_list(self, parent: wx.Window, includeStatus: bool = False) -> wx.ListCtrl:
		listCtrl = wx.ListCtrl(parent, style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES)
		if hasattr(listCtrl, "EnableCheckBoxes"):
			listCtrl.EnableCheckBoxes()
		columns = [
			(_("Language"), 110),
			(_("Package"), 210),
			(_("Voices"), 300),
			(_("Size"), 100),
		]
		if includeStatus:
			columns.append((_("Status"), 180))
		for index, (label, width) in enumerate(columns):
			listCtrl.InsertColumn(index, label, width=width)
		return listCtrl

	def _update_language_combo(self, combo: wx.Choice, packages: list[VoicePackage]) -> None:
		unique_codes = sorted({pkg.language for pkg in packages}, key=lambda c: get_language_display_name(c).lower())
		display_names = [_("All")] + [get_language_display_name(code) for code in unique_codes]

		oldIdx = combo.GetSelection()
		oldSelectionCode = ""
		if hasattr(combo, "_langCodes") and 0 < oldIdx <= len(combo._langCodes):
			oldSelectionCode = combo._langCodes[oldIdx - 1]

		combo.Clear()
		combo.AppendItems(display_names)
		combo._langCodes = unique_codes

		newIdx = 0
		if oldSelectionCode and oldSelectionCode in unique_codes:
			newIdx = unique_codes.index(oldSelectionCode) + 1
		combo.SetSelection(newIdx)

	def _apply_installed_filter(self) -> None:
		idx = self.installedLanguageCombo.GetSelection()
		if idx <= 0 or not hasattr(self.installedLanguageCombo, "_langCodes") or idx > len(self.installedLanguageCombo._langCodes):
			self.installedPackages = list(self._allInstalledPackages)
		else:
			target_code = self.installedLanguageCombo._langCodes[idx - 1]
			self.installedPackages = [
				pkg for pkg in self._allInstalledPackages if pkg.language.lower() == target_code.lower()
			]
		self._populate_installed_list()
		self._refresh_buttons()

	def _apply_download_filter(self) -> None:
		idx = self.downloadLanguageCombo.GetSelection()
		if idx <= 0 or not hasattr(self.downloadLanguageCombo, "_langCodes") or idx > len(self.downloadLanguageCombo._langCodes):
			self.downloadPackages = list(self._allDownloadPackages)
		else:
			target_code = self.downloadLanguageCombo._langCodes[idx - 1]
			self.downloadPackages = [
				pkg for pkg in self._allDownloadPackages if pkg.language.lower() == target_code.lower()
			]
		self._populate_download_list()
		self._refresh_buttons()

	def on_installed_language_filter_changed(self, evt: wx.CommandEvent) -> None:
		self._apply_installed_filter()

	def on_download_language_filter_changed(self, evt: wx.CommandEvent) -> None:
		self._apply_download_filter()

	def refresh_lists(self) -> None:
		self._allInstalledPackages = voice_store.physically_installed_packages(self.catalog)
		self._allUsableInstalledPackages = voice_store.installed_packages(self.catalog)
		installedIds = {pkg.id for pkg in self._allInstalledPackages}
		self._allDownloadPackages = [pkg for pkg in self.catalog.packages if pkg.id not in installedIds]

		title = _("{installed} installed voice packages, {available} available to download - Google TTS Voice Manager").format(
			installed=len(self._allInstalledPackages),
			available=len(self._allDownloadPackages),
		)
		self.SetTitle(title)

		self._update_language_combo(self.installedLanguageCombo, self._allInstalledPackages)
		self._update_language_combo(self.downloadLanguageCombo, self._allDownloadPackages)

		self._apply_installed_filter()
		self._apply_download_filter()

	def focus_default_control(self) -> None:
		if self._initialPage == "download":
			self.show_download_tab()
			return
		self._focus_active_page()

	def show_download_tab(self) -> None:
		self.notebook.SetSelection(1)
		self._focus_download_tab()

	def _focus_active_page(self) -> None:
		if self.notebook.GetSelection() == 1:
			self._focus_download_tab()
		else:
			self._focus_installed_tab()

	def _focus_installed_tab(self) -> None:
		self.notebook.SetFocus()

	def _focus_download_tab(self) -> None:
		self.notebook.SetFocus()

	def on_char_hook(self, evt: wx.KeyEvent) -> None:
		if evt.GetKeyCode() != wx.WXK_TAB or not evt.ControlDown():
			evt.Skip()
			return
		pageCount = self.notebook.GetPageCount()
		if pageCount <= 1:
			evt.Skip()
			return
		currentPage = self.notebook.GetSelection()
		if currentPage == wx.NOT_FOUND:
			currentPage = 0
		direction = -1 if evt.ShiftDown() else 1
		newPage = (currentPage + direction) % pageCount
		self.notebook.SetSelection(newPage)
		self.notebook.SetFocus()

	def _on_notebook_key_down(self, evt: wx.KeyEvent) -> None:
		key = evt.GetKeyCode()
		if key in (wx.WXK_UP, wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_RIGHT):
			pageCount = self.notebook.GetPageCount()
			if pageCount > 1:
				currentPage = self.notebook.GetSelection()
				if currentPage == wx.NOT_FOUND:
					currentPage = 0
				direction = -1 if key in (wx.WXK_UP, wx.WXK_LEFT) else 1
				newPage = (currentPage + direction) % pageCount
				self.notebook.SetSelection(newPage)
				self.notebook.SetFocus()
			return
		evt.Skip()

	def _populate_installed_list(self) -> None:
		self.installedList.DeleteAllItems()
		for index, package in enumerate(self.installedPackages):
			self._insert_package_row(self.installedList, index, package)
		if self.installedList.ItemCount:
			self.installedList.Select(0)
		# Reset the select-all toggle when list contents change.
		self.installedSelectAllCheck.SetValue(False)

	def _populate_download_list(self) -> None:
		self.downloadList.DeleteAllItems()
		for index, package in enumerate(self.downloadPackages):
			self._insert_package_row(self.downloadList, index, package, includeStatus=False)
		if self.downloadList.ItemCount:
			self.downloadList.Select(0)
		# Reset the select-all toggle when list contents change.
		self.downloadSelectAllCheck.SetValue(False)

	def _insert_package_row(
		self,
		listCtrl: wx.ListCtrl,
		index: int,
		package: VoicePackage,
		includeStatus: bool = False,
	) -> None:
		listCtrl.InsertItem(index, get_language_display_name(package.language))
		listCtrl.SetItem(index, 1, package.id)
		listCtrl.SetItem(index, 2, self._speaker_names(package))
		listCtrl.SetItem(index, 3, self._format_size(package.compressedSize))
		if includeStatus:
			listCtrl.SetItem(index, 4, self._package_status(package))

	def _package_status(self, package: VoicePackage) -> str:
		if package.dependentVoiceId:
			installedIds = {pkg.id for pkg in self._allInstalledPackages}
			if package.dependentVoiceId not in installedIds:
				return _("Missing required package: {dependency}").format(
					dependency=package.dependentVoiceId,
				)
		return _("Installed")

	def _speaker_names(self, package: VoicePackage) -> str:
		names = [str(speaker.get("name") or speaker.get("speaker") or "") for speaker in package.speakers]
		return ", ".join(name for name in names if name)

	def _format_size(self, size: int) -> str:
		if size <= 0:
			return ""
		return _("{size:.1f} MB").format(size=size / 1024 / 1024)

	def _checked_packages(self, listCtrl: wx.ListCtrl, packages: list[VoicePackage]) -> list[VoicePackage]:
		if hasattr(listCtrl, "IsItemChecked"):
			count = min(listCtrl.ItemCount, len(packages))
			return [packages[i] for i in range(count) if listCtrl.IsItemChecked(i)]
		return []

	def _with_installed_dependents(self, packages: list[VoicePackage]) -> list[VoicePackage]:
		expanded = list(packages)
		includedIds = {pkg.id for pkg in expanded}
		while True:
			added = False
			for package in self._allInstalledPackages:
				if package.id in includedIds:
					continue
				if package.dependentVoiceId in includedIds:
					expanded.append(package)
					includedIds.add(package.id)
					added = True
			if not added:
				return expanded

	def _dependency_depth(self, package: VoicePackage, packagesById: dict[str, VoicePackage]) -> int:
		depth = 0
		seen: set[str] = set()
		current = package
		while current.dependentVoiceId and current.dependentVoiceId not in seen:
			seen.add(current.id)
			parent = packagesById.get(current.dependentVoiceId)
			if parent is None:
				break
			depth += 1
			current = parent
		return depth

	def _dependents_first(self, packages: list[VoicePackage]) -> list[VoicePackage]:
		packagesById = {pkg.id: pkg for pkg in self._allInstalledPackages}
		return sorted(
			packages,
			key=lambda package: self._dependency_depth(package, packagesById),
			reverse=True,
		)

	def _package_list_text(self, packages: list[VoicePackage]) -> str:
		return ", ".join(pkg.id for pkg in packages)

	def _usable_packages_after_removal(self, packages: list[VoicePackage]) -> list[VoicePackage]:
		removedIds = {pkg.id for pkg in packages}
		remaining = [pkg for pkg in self._allInstalledPackages if pkg.id not in removedIds]
		return voice_store.installed_packages(VoiceCatalog(remaining))

	def _removes_all_usable_voices(self, packages: list[VoicePackage]) -> bool:
		return bool(self._allUsableInstalledPackages) and not self._usable_packages_after_removal(packages)

	def _is_google_synth_current(self) -> bool:
		try:
			return getattr(synthDriverHandler.getSynth(), "name", "") == SYNTH_NAME
		except Exception:
			return False

	def _open_synthesizer_dialog(self) -> bool:
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
				_("Google TTS Voice Manager"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			return False

	def _confirm_remove_last_inactive_voice(self, packages: list[VoicePackage]) -> bool:
		packageNames = self._package_list_text(packages)
		answer = gui.messageBox(
			_(
				"You are removing the last installed Google TTS For NVDA voice package. "
				"After it is removed, Google TTS For NVDA will have no voices available "
				"until you download another voice package.\n\n"
				"Packages to remove: {packages}"
			).format(packages=packageNames),
			_("Google TTS Voice Manager"),
			wx.OK | wx.CANCEL | wx.ICON_WARNING,
			self,
		)
		return answer == wx.OK

	def _confirm_remove_last_active_voice(self, packages: list[VoicePackage]) -> bool:
		packageNames = self._package_list_text(packages)
		answer = gui.messageBox(
			_(
				"You are removing the last installed Google TTS For NVDA voice package. "
				"Google TTS For NVDA is currently selected as your synthesizer, so it needs "
				"at least one voice package to keep speaking.\n\n"
				"Packages to remove: {packages}\n\n"
				"Choose Yes to open Select Synthesizer and switch to another synthesizer before removal. "
				"Choose No to keep this voice package installed."
			).format(packages=packageNames),
			_("Google TTS Voice Manager"),
			wx.YES_NO | wx.ICON_WARNING,
			self,
		)
		return answer == wx.YES

	def _schedule_remove_after_synth_switch(self, packages: list[VoicePackage]) -> None:
		self._pendingRemoveAfterSynthSwitch = packages
		self.set_status(
			_("Waiting for you to switch from Google TTS For NVDA to another synthesizer before removing the last voice package."),
			0,
			announce=True,
		)
		if self._open_synthesizer_dialog():
			wx.CallLater(500, self._remove_after_synth_switch, packages, 0)
		else:
			self._pendingRemoveAfterSynthSwitch = None

	def _remove_after_synth_switch(self, packages: list[VoicePackage], attempts: int) -> None:
		if self._pendingRemoveAfterSynthSwitch is not packages:
			return
		try:
			if not self.IsShown():
				self._pendingRemoveAfterSynthSwitch = None
				return
		except RuntimeError:
			self._pendingRemoveAfterSynthSwitch = None
			return
		if not self._is_google_synth_current():
			self._pendingRemoveAfterSynthSwitch = None
			self._remove_packages(packages)
			return
		if attempts >= 600:
			self._pendingRemoveAfterSynthSwitch = None
			self.set_status(
				_("The last voice package was not removed because Google TTS For NVDA is still the current synthesizer."),
				0,
				announce=True,
			)
			return
		wx.CallLater(500, self._remove_after_synth_switch, packages, attempts + 1)

	def _first_voice_id(self, packages: list[VoicePackage]) -> str | None:
		catalog = VoiceCatalog(packages)
		for speaker in catalog.speakers:
			if speaker.language == "en-US":
				return speaker.id
		if catalog.speakers:
			return catalog.speakers[0].id
		return None

	def _reset_configured_voice_if_removed(self, removedPackageIds: set[str]) -> str | None:
		try:
			configuredVoice = str(config.conf["speech"][SYNTH_NAME]["voice"])
		except Exception:
			return None
		removedPrefix = tuple(f"{packageId}:" for packageId in removedPackageIds)
		if not configuredVoice.startswith(removedPrefix):
			return None
		fallbackVoice = self._first_voice_id(self._allUsableInstalledPackages)
		if not fallbackVoice:
			return None
		try:
			config.conf["speech"][SYNTH_NAME]["voice"] = fallbackVoice
		except Exception:
			log.debug("Could not reset removed Google TTS configured voice.", exc_info=True)
			return None
		return fallbackVoice

	def _on_check_all(self, listCtrl: wx.ListCtrl, check: bool) -> None:
		if not hasattr(listCtrl, "CheckItem"):
			return
		for i in range(listCtrl.ItemCount):
			listCtrl.CheckItem(i, check)

	def on_installed_select_all(self, evt: wx.CommandEvent) -> None:
		"""Toggle all checkboxes in the installed list to match the select-all checkbox."""
		self._on_check_all(self.installedList, evt.IsChecked())

	def on_download_select_all(self, evt: wx.CommandEvent) -> None:
		"""Toggle all checkboxes in the download list to match the select-all checkbox."""
		self._on_check_all(self.downloadList, evt.IsChecked())

	def _on_installed_item_check_changed(self, evt: wx.ListEvent) -> None:
		"""Keep the select-all checkbox in sync when individual items are toggled."""
		count = self.installedList.ItemCount
		if count == 0:
			return
		all_checked = all(self.installedList.IsItemChecked(i) for i in range(count))
		self.installedSelectAllCheck.SetValue(all_checked)
		evt.Skip()

	def _on_download_item_check_changed(self, evt: wx.ListEvent) -> None:
		"""Keep the select-all checkbox in sync when individual items are toggled."""
		count = self.downloadList.ItemCount
		if count == 0:
			return
		all_checked = all(self.downloadList.IsItemChecked(i) for i in range(count))
		self.downloadSelectAllCheck.SetValue(all_checked)
		evt.Skip()

	def on_download_selected(self, evt: wx.CommandEvent) -> None:
		packages = self._checked_packages(self.downloadList, self.downloadPackages)
		if not packages:
			self.set_status(_("No voice packages selected for download."), 0, announce=True)
			return
		totalCount = len(packages)

		def work() -> dict[str, Any]:
			succeeded = 0
			failed: list[str] = []
			for i, package in enumerate(packages):
				def _progress(
					percent: int | None,
					message: str,
					_idx: int = i,
					_pkgId: str = package.id,
				) -> None:
					if percent is not None:
						overall = int((_idx * 100 + percent) / totalCount)
					else:
						overall = None
					wx.CallAfter(
						self.set_status,
						_("Downloading {current}/{total}: {package}").format(
							current=_idx + 1, total=totalCount, package=_pkgId,
						),
						overall,
					)
				try:
					voice_store.download_package(package, _progress)
					succeeded += 1
				except Exception as exc:
					log.error("Failed to download %s: %s", package.id, exc)
					failed.append(package.id)
			return {"succeeded": succeeded, "failed": failed}

		def done(result: Any | BaseException) -> None:
			self.isBusy = False
			if isinstance(result, BaseException):
				self._refresh_buttons()
				self.show_error(result)
				return
			self.refresh_lists()
			succeeded = result["succeeded"]
			failed = result["failed"]
			if failed:
				message = _(
					"Downloaded {succeeded} of {total} voice packages. Could not download: {failList}"
				).format(
					succeeded=succeeded,
					total=totalCount,
					failList=", ".join(failed),
				)
			elif succeeded == 1:
				message = _("Downloaded {package}.").format(package=packages[0].id)
			else:
				message = _("Downloaded {count} voice packages.").format(count=succeeded)
			self.set_status(message, 100)
			ui.message(message)
			self._focus_active_page()

		self._run_worker(work, done, _("Downloading voice packages..."))

	def on_remove_selected(self, evt: wx.CommandEvent) -> None:
		if self._pendingRemoveAfterSynthSwitch is not None:
			self.set_status(
				_("Waiting for you to switch from Google TTS For NVDA to another synthesizer before removing the last voice package."),
				0,
				announce=True,
			)
			return
		selectedPackages = self._checked_packages(self.installedList, self.installedPackages)
		if not selectedPackages:
			self.set_status(_("No voice packages selected for removal."), 0, announce=True)
			return
		selectedIds = {pkg.id for pkg in selectedPackages}
		packages = self._with_installed_dependents(selectedPackages)
		dependentPackages = [pkg for pkg in packages if pkg.id not in selectedIds]
		removesAllUsable = self._removes_all_usable_voices(packages)
		if removesAllUsable:
			packages = self._dependents_first(packages)
			if self._is_google_synth_current():
				if self._confirm_remove_last_active_voice(packages):
					self._schedule_remove_after_synth_switch(packages)
				return
			if not self._confirm_remove_last_inactive_voice(packages):
				return
		else:
			if len(packages) == 1:
				confirmMsg = _("Remove voice package {package}?").format(package=packages[0].id)
			elif dependentPackages:
				selectedNames = self._package_list_text(selectedPackages)
				dependentNames = self._package_list_text(dependentPackages)
				confirmMsg = _(
					"Remove {count} voice packages?\n"
					"Selected: {selected}\n"
					"Also remove voice packages that depend on them: {dependents}"
				).format(
					count=len(packages),
					selected=selectedNames,
					dependents=dependentNames,
				)
			else:
				packageNames = self._package_list_text(packages)
				confirmMsg = _("Remove {count} voice packages?\n{packages}").format(
					count=len(packages), packages=packageNames,
				)
			answer = gui.messageBox(
				confirmMsg,
				_("Google TTS Voice Manager"),
				wx.YES_NO | wx.ICON_QUESTION,
				self,
			)
			if answer != wx.YES:
				return
			packages = self._dependents_first(packages)
		self._remove_packages(packages)

	def _remove_packages(self, packages: list[VoicePackage]) -> None:
		totalCount = len(packages)

		def work() -> dict[str, Any]:
			succeeded = 0
			failed: list[str] = []
			removedIds: list[str] = []
			for package in packages:
				try:
					voice_store.remove_package(package)
					succeeded += 1
					removedIds.append(package.id)
				except Exception as exc:
					log.error("Failed to remove %s: %s", package.id, exc)
					failed.append(package.id)
			return {"succeeded": succeeded, "failed": failed, "removedIds": removedIds}

		def done(result: Any | BaseException) -> None:
			self.isBusy = False
			if isinstance(result, BaseException):
				self._refresh_buttons()
				self.show_error(result)
				return
			self.refresh_lists()
			succeeded = result["succeeded"]
			failed = result["failed"]
			resetVoice = self._reset_configured_voice_if_removed(set(result.get("removedIds", [])))
			if failed:
				message = _(
					"Removed {succeeded} of {total} voice packages. Could not remove: {failList}"
				).format(
					succeeded=succeeded,
					total=totalCount,
					failList=", ".join(failed),
				)
			elif succeeded == 1:
				message = _("Removed {package}.").format(package=packages[0].id)
			else:
				message = _("Removed {count} voice packages.").format(count=succeeded)
			if resetVoice:
				message = _("{message} The current voice was reset to {voice}.").format(
					message=message,
					voice=resetVoice,
				)
			self.set_status(message, 100)
			ui.message(message)
			self._focus_active_page()

		self._run_worker(work, done, _("Removing voice packages..."))

	def on_open_folder(self, evt: wx.CommandEvent) -> None:
		try:
			path = voice_store.voice_dir()
			os.startfile(os.fspath(path))  # type: ignore[attr-defined]
		except Exception as exc:
			self.show_error(exc)

	def _run_worker(
		self,
		work: Callable[[], Any],
		done: Callable[[Any | BaseException], None],
		busyMessage: str,
	) -> None:
		if self.isBusy:
			return
		self.isBusy = True
		self._lastProgressAnnouncement = -1
		self.closeButton.SetFocus()
		self._refresh_buttons()
		self.set_status(busyMessage, 0, announce=True)

		def run() -> None:
			try:
				result = work()
			except Exception as exc:
				result = exc
			wx.CallAfter(done, result)

		threading.Thread(target=run, name="googleTtsForNvda.voiceManager", daemon=True).start()

	def set_status(self, message: str, percent: int | None = None, announce: bool = False) -> None:
		self.statusText.SetLabel(message)
		if percent is not None:
			value = max(0, min(100, int(percent)))
			self.progressGauge.SetValue(value)
			if 0 <= value <= 100 and value // 25 > self._lastProgressAnnouncement // 25:
				self._lastProgressAnnouncement = value
				announce = True
		self.Layout()
		if announce:
			ui.message(message)

	def _user_friendly_error_message(self, error: BaseException) -> str:
		if isinstance(error, PermissionError):
			return _("Could not write to the voice packages folder. Check folder permissions and try again.")
		if isinstance(error, FileNotFoundError):
			return _("The selected voice package file could not be found.")
		if isinstance(error, (urllib.error.URLError, TimeoutError)):
			return _("Could not download the voice package. Check your internet connection and try again.")
		if isinstance(error, OSError):
			return _(
				"A file system error occurred while managing voice packages. "
				"Check the voice packages folder and try again."
			)
		message = str(error).strip()
		return message or _("An unexpected error occurred while managing voice packages.")

	def show_error(self, error: BaseException) -> None:
		message = self._user_friendly_error_message(error)
		technicalMessage = str(error).strip() or error.__class__.__name__
		log.error("Google TTS voice manager operation failed: %s", technicalMessage)
		self.set_status(_("Voice package operation failed: {message}").format(message=message), 0)
		gui.messageBox(message, _("Google TTS Voice Manager"), wx.OK | wx.ICON_ERROR, self)

	def _refresh_buttons(self) -> None:
		hasInstalledItems = self.installedList.ItemCount > 0
		hasDownloadItems = self.downloadList.ItemCount > 0
		for control in (
			self.refreshButton,
			self.openFolderButton,
			self.installedList,
			self.downloadList,
			self.installedLanguageCombo,
			self.downloadLanguageCombo,
		):
			control.Enable(not self.isBusy)
		self.installedSelectAllCheck.Enable(not self.isBusy and hasInstalledItems)
		self.downloadSelectAllCheck.Enable(not self.isBusy and hasDownloadItems)
		self.removeButton.Enable(not self.isBusy and hasInstalledItems)
		self.downloadButton.Enable(not self.isBusy and hasDownloadItems)

	def on_close(self, evt: wx.CloseEvent) -> None:
		if self.isBusy:
			evt.Veto()
			gui.messageBox(
				_("A voice package operation is still running."),
				_("Google TTS Voice Manager"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		self.Destroy()

	def on_destroy(self, evt: wx.WindowDestroyEvent) -> None:
		if evt.GetEventObject() is self:
			self._onDestroy(self)
		evt.Skip()
