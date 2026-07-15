# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import threading
from typing import Any
import unicodedata
import urllib.error

import addonHandler
import config
import gui
import languageHandler
import synthDriverHandler
import ui
import wx
from gui import nvdaControls
from logHandler import log

from synthDrivers.googleTtsForNvda import bridge as browserBridge
from synthDrivers.googleTtsForNvda.catalog import VoiceCatalog, VoicePackage, is_package_supported_by_engine
from synthDrivers.googleTtsForNvda import voice_store


addonHandler.initTranslation()

SYNTH_NAME = "googleTtsForNvda"
BASE_DIR = Path(__file__).resolve().parents[2]
LOCALE_DIR = BASE_DIR / "locale"
_languageSortRulesByLocale: dict[str, dict[str, Any] | None] = {}

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


def get_nvda_locale_for_language(lang_code: str | None) -> str:
	if not lang_code:
		return ""
	languageText = str(lang_code).strip()
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
	try:
		normalized = languageHandler.normalizeLanguage(languageText)
	except Exception:
		normalized = languageText.replace("-", "_")
	return str(normalized or "").strip()


def _language_display_candidates(lang_code: str) -> list[str]:
	nvdaLocale = get_nvda_locale_for_language(lang_code)
	candidates: list[str] = []
	for candidate in (nvdaLocale, nvdaLocale.split("_", 1)[0] if "_" in nvdaLocale else "", lang_code):
		if candidate and candidate not in candidates:
			candidates.append(candidate)
	return candidates


def get_language_display_name(lang_code: str) -> str:
	for candidate in _language_display_candidates(lang_code):
		try:
			description = languageHandler.getLanguageDescription(candidate)
		except Exception:
			description = None
		if description:
			return description
	for k, v in LANGUAGE_NAMES.items():
		if k.lower() == lang_code.lower():
			return v
	return lang_code


def _current_ui_language() -> str:
	try:
		return languageHandler.getLanguage().replace("-", "_").lower()
	except Exception:
		return ""


def _locale_candidates(language: str) -> list[str]:
	if not language:
		return []
	candidates = [language]
	rootLanguage = language.split("_", 1)[0]
	if rootLanguage != language:
		candidates.append(rootLanguage)
	return candidates


def _language_sort_rules_for_current_ui() -> dict[str, Any] | None:
	for localeName in _locale_candidates(_current_ui_language()):
		rules = _load_language_sort_rules(localeName)
		if rules is not None:
			return rules
	return None


def _load_language_sort_rules(localeName: str) -> dict[str, Any] | None:
	if localeName in _languageSortRulesByLocale:
		return _languageSortRulesByLocale[localeName]
	path = LOCALE_DIR / localeName / "languageSort.json"
	try:
		rawRules = json.loads(path.read_text(encoding="utf-8"))
	except FileNotFoundError:
		_languageSortRulesByLocale[localeName] = None
		return None
	except Exception:
		log.debug("Could not load Google TTS language sort rules from %s.", path, exc_info=True)
		_languageSortRulesByLocale[localeName] = None
		return None
	rules = _normalize_language_sort_rules(rawRules)
	_languageSortRulesByLocale[localeName] = rules
	return rules


def _normalize_language_sort_rules(rawRules: Any) -> dict[str, Any] | None:
	if not isinstance(rawRules, dict):
		return None
	rawLetterOrder = rawRules.get("letterOrder")
	if not isinstance(rawLetterOrder, list) or not rawLetterOrder:
		return None
	letterOrder: dict[str, str] = {}
	for index, item in enumerate(rawLetterOrder):
		if not isinstance(item, str) or not item:
			return None
		letter = unicodedata.normalize("NFC", _strip_combining_marks(item.casefold(), set()))
		letterOrder[letter] = f"{index:04d}"
	stripPrefixes = tuple(
		prefix
		for prefix in rawRules.get("stripPrefixes", [])
		if isinstance(prefix, str) and prefix
	)
	ignoredMarks = _combining_marks_from_names(rawRules.get("ignoreCombiningMarks", []))
	return {
		"letterOrder": letterOrder,
		"stripPrefixes": stripPrefixes,
		"ignoredMarks": ignoredMarks,
	}


def _combining_marks_from_names(markNames: Any) -> set[str]:
	marks: set[str] = set()
	if not isinstance(markNames, list):
		return marks
	for name in markNames:
		if not isinstance(name, str):
			continue
		normalizedName = name.upper()
		try:
			marks.add(unicodedata.lookup(f"COMBINING {normalizedName}"))
		except KeyError:
			try:
				marks.add(unicodedata.lookup(f"COMBINING {normalizedName} ACCENT"))
			except KeyError:
				log.debug("Ignoring unknown Google TTS language sort combining mark: %s", name)
	return marks


def _strip_combining_marks(value: str, marks: set[str]) -> str:
	if not marks:
		return value
	return "".join(
		char
		for char in unicodedata.normalize("NFD", value)
		if char not in marks
	)


def _rule_based_visible_sort_key(displayName: str, rules: dict[str, Any]) -> tuple[str, str]:
	ignoredMarks = rules["ignoredMarks"]
	sortName = unicodedata.normalize("NFC", _strip_combining_marks(displayName.casefold(), ignoredMarks))
	for prefix in rules["stripPrefixes"]:
		normalizedPrefix = unicodedata.normalize("NFC", _strip_combining_marks(prefix.casefold(), ignoredMarks))
		if sortName.startswith(normalizedPrefix):
			sortName = sortName[len(normalizedPrefix):].lstrip()
			break
	letterOrder = rules["letterOrder"]
	normalizedLetters = "".join(letterOrder.get(char, char) for char in sortName)
	return (normalizedLetters, sortName)


def _visible_language_sort_key(displayName: str) -> tuple[str, str]:
	rules = _language_sort_rules_for_current_ui()
	if rules is not None:
		return _rule_based_visible_sort_key(displayName, rules)
	return (displayName.casefold(), displayName)


def _language_codes_for_display(packages: list["VoicePackage"]) -> list[str]:
	seen: set[str] = set()
	codes: list[str] = []
	for package in packages:
		normalizedCode = package.language.lower()
		if normalizedCode in seen:
			continue
		seen.add(normalizedCode)
		codes.append(package.language)
	if _language_sort_rules_for_current_ui() is not None:
		codes.sort(key=lambda code: _visible_language_sort_key(get_language_display_name(code)))
	return codes


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
		self._allInstalledPackageIds: set[str] = set()
		self._allUsableInstalledPackages: list[VoicePackage] = []
		self._allUsableInstalledPackageIds: set[str] = set()
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
		self.refreshButton.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_lists(announce=True))
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
			self.installedPanel, label=_("Check &all voice packages"),
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
			self.downloadPanel, label=_("Check &all voice packages"),
		)
		self.downloadSelectAllCheck.Bind(wx.EVT_CHECKBOX, self.on_download_select_all)
		sizer.Add(self.downloadSelectAllCheck, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.downloadList = self._create_list(self.downloadPanel, includeStatus=True)
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
		unique_codes = _language_codes_for_display(packages)
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
		if _language_sort_rules_for_current_ui() is not None:
			self.installedPackages.sort(key=self._visible_package_sort_key)
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
		if _language_sort_rules_for_current_ui() is not None:
			self.downloadPackages.sort(key=self._visible_package_sort_key)
		self._populate_download_list()
		self._refresh_buttons()

	def on_installed_language_filter_changed(self, evt: wx.CommandEvent) -> None:
		self._apply_installed_filter()

	def on_download_language_filter_changed(self, evt: wx.CommandEvent) -> None:
		self._apply_download_filter()

	def refresh_lists(self, announce: bool = False) -> None:
		self._allInstalledPackages = voice_store.physically_installed_packages(self.catalog)
		self._allInstalledPackageIds = {pkg.id for pkg in self._allInstalledPackages}
		self._allUsableInstalledPackages = voice_store.usable_installed_packages(self._allInstalledPackages)
		self._allUsableInstalledPackageIds = {pkg.id for pkg in self._allUsableInstalledPackages}
		self._allDownloadPackages = [pkg for pkg in self.catalog.packages if pkg.id not in self._allInstalledPackageIds]
		supportedDownloadCount = sum(1 for pkg in self._allDownloadPackages if is_package_supported_by_engine(pkg))

		summary = _("{installed} installed voice packages, {available} available to download.").format(
			installed=len(self._allInstalledPackages),
			available=supportedDownloadCount,
		)
		title = _("{installed} installed voice packages, {available} available to download - Google TTS Voice Manager").format(
			installed=len(self._allInstalledPackages),
			available=supportedDownloadCount,
		)
		self.SetTitle(title)

		self._update_language_combo(self.installedLanguageCombo, self._allInstalledPackages)
		self._update_language_combo(self.downloadLanguageCombo, self._allDownloadPackages)

		self._apply_installed_filter()
		self._apply_download_filter()
		if announce:
			self.set_status(summary, 0, announce=True)

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
			self._insert_package_row(self.installedList, index, package, self._installed_package_status)
		if self.installedList.ItemCount:
			self.installedList.Select(0)
		# Reset the select-all toggle when list contents change.
		self.installedSelectAllCheck.SetValue(False)

	def _populate_download_list(self) -> None:
		self.downloadList.DeleteAllItems()
		for index, package in enumerate(self.downloadPackages):
			self._insert_package_row(self.downloadList, index, package, self._download_package_status)
		if self.downloadList.ItemCount:
			self.downloadList.Select(0)
		# Reset the select-all toggle when list contents change.
		self.downloadSelectAllCheck.SetValue(False)

	def _visible_package_sort_key(self, package: VoicePackage) -> tuple[tuple[str, str], str]:
		return (_visible_language_sort_key(get_language_display_name(package.language)), package.id)

	def _insert_package_row(
		self,
		listCtrl: wx.ListCtrl,
		index: int,
		package: VoicePackage,
		statusProvider: Callable[[VoicePackage], str] | None = None,
	) -> None:
		listCtrl.InsertItem(index, get_language_display_name(package.language))
		listCtrl.SetItem(index, 1, package.id)
		listCtrl.SetItem(index, 2, self._speaker_names(package))
		listCtrl.SetItem(index, 3, self._format_size(package.compressedSize))
		if statusProvider:
			listCtrl.SetItem(index, 4, statusProvider(package))

	def _direct_installed_dependents(self, package: VoicePackage) -> list[VoicePackage]:
		return [
			installedPackage
			for installedPackage in self._allInstalledPackages
			if installedPackage.dependentVoiceId == package.id
		]

	def _direct_download_dependents(self, package: VoicePackage) -> list[VoicePackage]:
		return [
			downloadPackage
			for downloadPackage in self._allDownloadPackages
			if downloadPackage.dependentVoiceId == package.id
			and is_package_supported_by_engine(downloadPackage)
		]

	def _installed_package_status(self, package: VoicePackage) -> str:
		if not is_package_supported_by_engine(package):
			return _("Not supported by the bundled engine")
		if package.dependentVoiceId:
			if package.dependentVoiceId not in self._allInstalledPackageIds:
				return _("Not usable. Missing required package: {dependency}").format(
					dependency=package.dependentVoiceId,
				)
			if package.dependentVoiceId not in self._allUsableInstalledPackageIds:
				return _("Not usable. Required package is not usable: {dependency}").format(
					dependency=package.dependentVoiceId,
				)
		dependentCount = len(self._direct_installed_dependents(package))
		if package.dependentVoiceId:
			if dependentCount:
				return _("Usable. Requires {dependency}; required by {count} installed packages").format(
					dependency=package.dependentVoiceId,
					count=dependentCount,
				)
			return _("Usable. Requires {dependency}").format(
				dependency=package.dependentVoiceId,
			)
		if dependentCount:
			return _("Usable. Required by {count} installed packages").format(
				count=dependentCount,
			)
		return _("Usable")

	def _download_package_status(self, package: VoicePackage) -> str:
		if not is_package_supported_by_engine(package):
			return _("Not supported by the bundled engine")
		if package.dependentVoiceId:
			if package.dependentVoiceId in self._allUsableInstalledPackageIds:
				return _("Available. Required package is installed: {dependency}").format(
					dependency=package.dependentVoiceId,
				)
			return _("Available. Requires package: {dependency}").format(
				dependency=package.dependentVoiceId,
			)
		dependentCount = len(self._direct_download_dependents(package))
		if dependentCount:
			return _("Available. Required by {count} downloadable packages").format(
				count=dependentCount,
			)
		return _("Available to download")

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

	def _with_required_download_dependencies(self, packages: list[VoicePackage]) -> list[VoicePackage]:
		expanded = list(packages)
		includedIds = {pkg.id for pkg in expanded}
		installedIds = {pkg.id for pkg in self._allInstalledPackages}
		catalogPackagesById = {pkg.id: pkg for pkg in self.catalog.packages}

		def add_dependency_chain(package: VoicePackage, seenIds: set[str]) -> None:
			dependencyId = package.dependentVoiceId
			if not dependencyId or dependencyId in seenIds:
				return
			seenIds.add(dependencyId)
			dependency = catalogPackagesById.get(dependencyId)
			if dependency is None or not is_package_supported_by_engine(dependency):
				return
			if dependency.id not in installedIds and dependency.id not in includedIds:
				expanded.append(dependency)
				includedIds.add(dependency.id)
			add_dependency_chain(dependency, seenIds)

		for package in list(expanded):
			add_dependency_chain(package, set())
		return expanded

	def _missing_dependency_for_package(
		self,
		package: VoicePackage,
		catalogPackagesById: dict[str, VoicePackage],
	) -> str:
		seenIds: set[str] = set()
		dependencyId = package.dependentVoiceId
		while dependencyId:
			if dependencyId in seenIds:
				return ""
			seenIds.add(dependencyId)
			dependency = catalogPackagesById.get(dependencyId)
			if (
				dependency is None
				or not is_package_supported_by_engine(dependency)
				or not voice_store.is_package_installed(dependency)
			):
				return dependencyId
			dependencyId = dependency.dependentVoiceId
		return ""

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

	def _dependencies_first(self, packages: list[VoicePackage]) -> list[VoicePackage]:
		packagesById = {pkg.id: pkg for pkg in self.catalog.packages}
		return sorted(
			packages,
			key=lambda package: self._dependency_depth(package, packagesById),
		)

	def _package_list_text(self, packages: list[VoicePackage]) -> str:
		return ", ".join(pkg.id for pkg in packages)

	def _usable_packages_after_removal(self, packages: list[VoicePackage]) -> list[VoicePackage]:
		removedIds = {pkg.id for pkg in packages}
		remaining = [pkg for pkg in self._allInstalledPackages if pkg.id not in removedIds]
		return voice_store.usable_installed_packages(remaining)

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
				raise RuntimeError(_("Select Synthesizer dialog class was not found."))
			gui.mainFrame.popupSettingsDialog(dialogClass)
			return True
		except Exception as exc:
			log.error("Could not open Select Synthesizer dialog: %s", exc)
			gui.messageBox(
				_("The Select Synthesizer dialog could not be opened."),
				_("Google TTS Voice Manager"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			return False

	def _confirm_remove_last_inactive_voice(self, packages: list[VoicePackage]) -> bool:
		packageNames = self._package_list_text(packages)
		answer = gui.messageBox(
			_(
				"You are about to remove the last usable voice package. "
				"After it is removed, Google TTS For NVDA will not have any voices available "
				"until you download another package.\n\n"
				"Packages to remove, including dependent packages: {packages}\n\n"
				"Choose Yes to remove these packages. Choose No to keep them installed."
			).format(packages=packageNames),
			_("Google TTS Voice Manager"),
			wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
			self,
		)
		return answer == wx.YES

	def _confirm_remove_last_active_voice(self, packages: list[VoicePackage]) -> bool:
		packageNames = self._package_list_text(packages)
		answer = gui.messageBox(
			_(
				"You are about to remove the last usable voice package. "
				"Google TTS For NVDA is currently selected as your synthesizer, so it needs "
				"at least one voice to keep speaking.\n\n"
				"Packages to remove, including dependent packages: {packages}\n\n"
				"Choose Yes to open Select Synthesizer and switch first. "
				"Choose No to keep this package installed."
			).format(packages=packageNames),
			_("Google TTS Voice Manager"),
			wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
			self,
		)
		return answer == wx.YES

	def _schedule_remove_after_synth_switch(self, packages: list[VoicePackage]) -> None:
		self._pendingRemoveAfterSynthSwitch = packages
		self.set_status(
			_("Waiting for you to switch away from Google TTS For NVDA before removing the last usable voice package."),
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
				_("The last usable voice package was kept because Google TTS For NVDA is still the current synthesizer."),
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
			synthConfig = config.conf["speech"][SYNTH_NAME]
			configuredVoice = str(synthConfig.get("voice") or "")
			configuredVariant = str(synthConfig.get("variant") or "")
		except Exception:
			return None
		removedPrefix = tuple(f"{packageId}:" for packageId in removedPackageIds)
		if not configuredVariant.startswith(removedPrefix) and not configuredVoice.startswith(removedPrefix):
			return None
		fallbackVoice = self._first_voice_id(self._allUsableInstalledPackages)
		if not fallbackVoice:
			return None
		fallbackLanguage = ""
		try:
			fallbackLanguage = VoiceCatalog(self._allUsableInstalledPackages).language_for_voice(fallbackVoice)
		except Exception:
			log.debug("Could not resolve fallback Google TTS voice language.", exc_info=True)
		try:
			synthConfig["variant"] = fallbackVoice
			if fallbackLanguage:
				synthConfig["voice"] = fallbackLanguage
		except Exception:
			log.debug("Could not reset removed Google TTS configured voice.", exc_info=True)
			return None
		return fallbackVoice

	def _reset_auto_language_profile_variants_if_removed(self, removedPackageIds: set[str]) -> int:
		if not removedPackageIds:
			return 0
		try:
			section = config.conf[browserBridge.CONFIG_SECTION]
			rawProfiles = section[browserBridge.CONFIG_AUTO_LANGUAGE_PROFILES]
			profiles = json.loads(str(rawProfiles or "{}"))
		except Exception:
			return 0
		if not isinstance(profiles, dict):
			return 0

		removedPrefix = tuple(f"{packageId}:" for packageId in removedPackageIds)
		speakersByLanguage = VoiceCatalog(self._allUsableInstalledPackages).voices_by_language()
		changedCount = 0
		for rawLanguage, rawProfile in list(profiles.items()):
			if not isinstance(rawProfile, dict):
				continue
			language = str(rawLanguage or "").strip().replace("_", "-")
			speakers = speakersByLanguage.get(language, [])
			validVoiceIds = {speaker.id for speaker in speakers}
			voiceId = str(rawProfile.get("voice") or "")
			voiceWasRemoved = voiceId.startswith(removedPrefix)
			voiceIsInvalid = bool(validVoiceIds and voiceId and voiceId not in validVoiceIds)
			if not voiceWasRemoved and not voiceIsInvalid:
				continue
			replacementVoice = speakers[0].id if speakers else ""
			if replacementVoice == voiceId:
				continue
			profile = dict(rawProfile)
			profile["voice"] = replacementVoice
			profiles[rawLanguage] = profile
			changedCount += 1

		if not changedCount:
			return 0
		try:
			section[browserBridge.CONFIG_AUTO_LANGUAGE_PROFILES] = json.dumps(
				profiles,
				ensure_ascii=False,
				sort_keys=True,
			)
		except Exception:
			log.debug("Could not reset removed Google TTS automatic language profile variants.", exc_info=True)
			return 0
		return changedCount

	def _apply_reset_voice_to_current_synth(self, voiceId: str) -> None:
		try:
			currentSynth = synthDriverHandler.getSynth()
			if getattr(currentSynth, "name", "") != SYNTH_NAME:
				return
			try:
				language = currentSynth.catalog.language_for_voice(voiceId)
			except Exception:
				language = VoiceCatalog(self._allUsableInstalledPackages).language_for_voice(voiceId)
			if language:
				currentSynth.voice = language
			if hasattr(currentSynth, "variant"):
				currentSynth.variant = voiceId
		except Exception:
			log.debug("Could not apply reset Google TTS voice to current synth.", exc_info=True)

	def _warm_current_google_synth_voice(self) -> None:
		try:
			currentSynth = synthDriverHandler.getSynth()
			if getattr(currentSynth, "name", "") != SYNTH_NAME:
				return
			warmCurrentVoice = getattr(currentSynth, "_warm_current_voice_async", None)
			if callable(warmCurrentVoice):
				warmCurrentVoice()
		except Exception:
			log.debug("Could not warm Google TTS voice after package change.", exc_info=True)

	def _on_check_all(
		self,
		listCtrl: wx.ListCtrl,
		check: bool,
		packages: list[VoicePackage] | None = None,
		allowPackage: Callable[[VoicePackage], bool] | None = None,
	) -> None:
		if not hasattr(listCtrl, "CheckItem"):
			return
		for i in range(listCtrl.ItemCount):
			if check and packages is not None and allowPackage is not None and i < len(packages):
				if not allowPackage(packages[i]):
					listCtrl.CheckItem(i, False)
					continue
			listCtrl.CheckItem(i, check)

	def on_installed_select_all(self, evt: wx.CommandEvent) -> None:
		"""Toggle all checkboxes in the installed list to match the select-all checkbox."""
		self._on_check_all(self.installedList, evt.IsChecked())

	def on_download_select_all(self, evt: wx.CommandEvent) -> None:
		"""Toggle all checkboxes in the download list to match the select-all checkbox."""
		self._on_check_all(self.downloadList, evt.IsChecked(), self.downloadPackages, is_package_supported_by_engine)

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
		index = evt.GetIndex()
		if (
			0 <= index < len(self.downloadPackages)
			and hasattr(self.downloadList, "IsItemChecked")
			and hasattr(self.downloadList, "CheckItem")
			and self.downloadList.IsItemChecked(index)
			and not is_package_supported_by_engine(self.downloadPackages[index])
		):
			self.downloadList.CheckItem(index, False)
			self.set_status(_("This voice package is not supported by the bundled Google TTS engine."), 0, announce=True)
			return
		all_checked = all(self.downloadList.IsItemChecked(i) for i in range(count))
		self.downloadSelectAllCheck.SetValue(all_checked)
		evt.Skip()

	def on_download_selected(self, evt: wx.CommandEvent) -> None:
		checkedPackages = self._checked_packages(self.downloadList, self.downloadPackages)
		if not checkedPackages:
			self.set_status(_("No voice packages are checked for download."), 0, announce=True)
			return
		unsupportedPackages = [pkg for pkg in checkedPackages if not is_package_supported_by_engine(pkg)]
		packages = [pkg for pkg in checkedPackages if is_package_supported_by_engine(pkg)]
		if not packages:
			self.set_status(
				_("The checked voice packages are not supported by the bundled Google TTS engine."),
				0,
				announce=True,
			)
			return
		selectedDownloadIds = {pkg.id for pkg in packages}
		packages = self._dependencies_first(self._with_required_download_dependencies(packages))
		requiredPackages = [pkg for pkg in packages if pkg.id not in selectedDownloadIds]
		if requiredPackages:
			confirmMsg = _(
				"Download required voice packages?\n"
				"Selected: {selected}\n"
				"Also download packages required by your selection: {dependencies}"
			).format(
				selected=self._package_list_text([pkg for pkg in packages if pkg.id in selectedDownloadIds]),
				dependencies=self._package_list_text(requiredPackages),
			)
			answer = gui.messageBox(
				confirmMsg,
				_("Google TTS Voice Manager"),
				wx.YES_NO | wx.ICON_QUESTION,
				self,
			)
			if answer != wx.YES:
				return
		totalCount = len(packages)
		catalogPackagesById = {pkg.id: pkg for pkg in self.catalog.packages}

		def work() -> dict[str, Any]:
			succeeded = 0
			succeededIds: list[str] = []
			failed: list[tuple[str, str]] = []
			lastOverall: int | None = None
			lastStatusMessage = ""
			for i, package in enumerate(packages):
				def _progress(
					percent: int | None,
					message: str,
					_idx: int = i,
					_pkgId: str = package.id,
				) -> None:
					nonlocal lastOverall, lastStatusMessage
					if percent is not None:
						overall = int((_idx * 100 + percent) / totalCount)
						statusMessage = _("Downloading {current}/{total}: {package}, overall {percent} percent complete").format(
							current=_idx + 1,
							total=totalCount,
							package=_pkgId,
							percent=overall,
						)
					else:
						overall = None
						statusMessage = _("Downloading {current}/{total}: {package}").format(
							current=_idx + 1,
							total=totalCount,
							package=_pkgId,
						)
					if overall == lastOverall and statusMessage == lastStatusMessage:
						return
					lastOverall = overall
					lastStatusMessage = statusMessage
					wx.CallAfter(
						self.set_status,
						statusMessage,
						overall,
					)
				try:
					missingDependencyId = self._missing_dependency_for_package(package, catalogPackagesById)
					if missingDependencyId:
						failed.append((
							package.id,
							_("Missing required package: {dependency}").format(
								dependency=missingDependencyId,
							),
						))
						continue
					voice_store.download_package(package, _progress)
					succeeded += 1
					succeededIds.append(package.id)
				except Exception as exc:
					log.error("Failed to download %s: %s", package.id, exc)
					failed.append((package.id, self._user_friendly_error_message(exc)))
			return {"succeeded": succeeded, "succeededIds": succeededIds, "failed": failed}

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
					"Downloaded {succeeded} of {total} packages. Could not download: {failList}. First error: {reason}"
				).format(
					succeeded=succeeded,
					total=totalCount,
					failList=", ".join(packageId for packageId, _reason in failed),
					reason=failed[0][1],
				)
			elif succeeded == 1:
				message = _("Downloaded {package}.").format(package=packages[0].id)
			else:
				message = _("Downloaded {count} voice packages.").format(count=succeeded)
			if unsupportedPackages:
				message = _("{message} Skipped packages not supported by this engine: {packages}").format(
					message=message,
					packages=", ".join(package.id for package in unsupportedPackages),
				)
			succeededIds = set(result.get("succeededIds", []))
			requiredSucceededPackages = [pkg for pkg in requiredPackages if pkg.id in succeededIds]
			if requiredSucceededPackages:
				message = _("{message} Also downloaded required packages: {packages}").format(
					message=message,
					packages=", ".join(package.id for package in requiredSucceededPackages),
				)
			if succeededIds:
				self._warm_current_google_synth_voice()
			self.set_status(message, 100, announce=True)
			self._focus_active_page()

		self._run_worker(work, done, _("Downloading voice packages..."))

	def on_remove_selected(self, evt: wx.CommandEvent) -> None:
		if self._pendingRemoveAfterSynthSwitch is not None:
			self.set_status(
				_("Waiting for you to switch away from Google TTS For NVDA before removing the last usable voice package."),
				0,
				announce=True,
			)
			return
		selectedPackages = self._checked_packages(self.installedList, self.installedPackages)
		if not selectedPackages:
			self.set_status(_("No voice packages are checked for removal."), 0, announce=True)
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
					"Remove {count} packages?\n"
					"Selected: {selected}\n"
					"Also remove packages that depend on your selection: {dependents}"
				).format(
					count=len(packages),
					selected=selectedNames,
					dependents=dependentNames,
				)
			else:
				packageNames = self._package_list_text(packages)
				confirmMsg = _("Remove {count} packages?\n{packages}").format(
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
			failed: list[tuple[str, str]] = []
			removedIds: list[str] = []
			for package in packages:
				try:
					voice_store.remove_package(package)
					succeeded += 1
					removedIds.append(package.id)
				except Exception as exc:
					log.error("Failed to remove %s: %s", package.id, exc)
					failed.append((package.id, self._user_friendly_error_message(exc)))
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
			removedIds = set(result.get("removedIds", []))
			resetVoice = self._reset_configured_voice_if_removed(removedIds)
			resetProfileCount = self._reset_auto_language_profile_variants_if_removed(removedIds)
			if resetVoice:
				self._apply_reset_voice_to_current_synth(resetVoice)
			if resetProfileCount:
				self._warm_current_google_synth_voice()
			if failed:
				message = _(
					"Removed {succeeded} of {total} packages. Could not remove: {failList}. First error: {reason}"
				).format(
					succeeded=succeeded,
					total=totalCount,
					failList=", ".join(packageId for packageId, _reason in failed),
					reason=failed[0][1],
				)
			elif succeeded == 1:
				message = _("Removed {package}.").format(package=packages[0].id)
			else:
				message = _("Removed {count} voice packages.").format(count=succeeded)
			if resetVoice:
				message = _("{message} The current variant was reset to {voice}.").format(
					message=message,
					voice=resetVoice,
				)
			self.set_status(message, 100, announce=True)
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
		self._lastProgressAnnouncement = 0
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
			if 25 <= value < 100 and value // 25 > self._lastProgressAnnouncement // 25:
				self._lastProgressAnnouncement = value
				announce = True
		self.Layout()
		if announce:
			ui.message(message)

	def _user_friendly_error_message(self, error: BaseException) -> str:
		if isinstance(error, PermissionError):
			return _("Could not write to the voice packages folder. Check folder permissions and try again.")
		if isinstance(error, FileNotFoundError):
			return _("The voice package file could not be found. Refresh the list and try again.")
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
		hasDownloadItems = any(is_package_supported_by_engine(package) for package in self.downloadPackages)
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
