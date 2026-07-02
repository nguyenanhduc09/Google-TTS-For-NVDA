# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import os
import threading
from typing import Any

import addonHandler
import gui
import ui
import wx
from gui import nvdaControls
from logHandler import log

from synthDrivers.googleTtsForNvda.catalog import VoiceCatalog, VoicePackage
from synthDrivers.googleTtsForNvda import voice_store


addonHandler.initTranslation()


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
		self.isBusy = False
		self._initialPage = initialPage
		self._lastProgressAnnouncement = -1
		self._build_ui()
		self.SetMinSize((720, 520))
		self.SetEscapeId(wx.ID_CLOSE)
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
		self.openFolderButton = wx.Button(self, label=_("&Open voices folder"))
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
		self.installedSelectAllCheck = wx.CheckBox(
			self.installedPanel, label=_("Select &all voices"),
		)
		self.installedSelectAllCheck.Bind(wx.EVT_CHECKBOX, self.on_installed_select_all)
		sizer.Add(self.installedSelectAllCheck, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.installedList = self._create_list(self.installedPanel)
		self.installedList.SetName(_("Installed voice packages"))
		self.installedList.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_installed_item_check_changed)
		self.installedList.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_installed_item_check_changed)
		sizer.Add(self.installedList, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		buttonRow = wx.BoxSizer(wx.HORIZONTAL)
		self.removeButton = wx.Button(self.installedPanel, label=_("&Remove checked voices"))
		self.removeButton.Bind(wx.EVT_BUTTON, self.on_remove_selected)
		buttonRow.Add(self.removeButton)
		sizer.Add(buttonRow, 0, wx.EXPAND | wx.ALL, 8)

	def _build_download_tab(self) -> None:
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.downloadPanel.SetSizer(sizer)
		self.downloadSelectAllCheck = wx.CheckBox(
			self.downloadPanel, label=_("Select &all voices"),
		)
		self.downloadSelectAllCheck.Bind(wx.EVT_CHECKBOX, self.on_download_select_all)
		sizer.Add(self.downloadSelectAllCheck, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.downloadList = self._create_list(self.downloadPanel, includeStatus=False)
		self.downloadList.SetName(_("Downloadable voice packages"))
		self.downloadList.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_download_item_check_changed)
		self.downloadList.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_download_item_check_changed)
		sizer.Add(self.downloadList, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
		buttonRow = wx.BoxSizer(wx.HORIZONTAL)
		self.downloadButton = wx.Button(self.downloadPanel, label=_("&Download checked voices"))
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
			columns.append((_("Status"), 120))
		for index, (label, width) in enumerate(columns):
			listCtrl.InsertColumn(index, label, width=width)
		return listCtrl

	def refresh_lists(self) -> None:
		self.installedPackages = voice_store.installed_packages(self.catalog)
		installedIds = {pkg.id for pkg in self.installedPackages}
		self.downloadPackages = [pkg for pkg in self.catalog.packages if pkg.id not in installedIds]
		self._populate_installed_list()
		self._populate_download_list()
		self.set_status(
			_("{installed} installed; {total} available.").format(
				installed=len(self.installedPackages),
				total=len(self.catalog.packages),
			),
			0,
		)
		self._refresh_buttons()

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
		if self.installedList.ItemCount:
			self.installedList.SetFocus()
		else:
			self.refreshButton.SetFocus()

	def _focus_download_tab(self) -> None:
		if self.downloadList.ItemCount:
			self.downloadList.SetFocus()
		else:
			self.refreshButton.SetFocus()

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
		listCtrl.InsertItem(index, package.language)
		listCtrl.SetItem(index, 1, package.id)
		listCtrl.SetItem(index, 2, self._speaker_names(package))
		listCtrl.SetItem(index, 3, self._format_size(package.compressedSize))
		if includeStatus:
			status = _("installed") if voice_store.is_package_installed(package) else _("not installed")
			listCtrl.SetItem(index, 4, status)

	def _speaker_names(self, package: VoicePackage) -> str:
		names = [str(speaker.get("name") or speaker.get("speaker") or "") for speaker in package.speakers]
		return ", ".join(name for name in names if name)

	def _format_size(self, size: int) -> str:
		if size <= 0:
			return ""
		return _("{size:.1f} MB").format(size=size / 1024 / 1024)

	def _checked_packages(self, listCtrl: wx.ListCtrl, packages: list[VoicePackage]) -> list[VoicePackage]:
		checked: list[VoicePackage] = []
		if hasattr(listCtrl, "IsItemChecked"):
			count = min(listCtrl.ItemCount, len(packages))
			checked = [packages[i] for i in range(count) if listCtrl.IsItemChecked(i)]
			if checked:
				return checked
		index = listCtrl.GetFirstSelected()
		if 0 <= index < len(packages):
			return [packages[index]]
		return []

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
			self.set_status(_("No voice packages selected."), 0, announce=True)
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
					"Downloaded {succeeded} of {total}. Failed: {failList}"
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

		self._run_worker(work, done)

	def on_remove_selected(self, evt: wx.CommandEvent) -> None:
		packages = self._checked_packages(self.installedList, self.installedPackages)
		if not packages:
			self.set_status(_("No installed voice packages selected."), 0, announce=True)
			return
		if len(packages) == 1:
			confirmMsg = _("Remove {package}?").format(package=packages[0].id)
		else:
			packageNames = ", ".join(pkg.id for pkg in packages)
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
		totalCount = len(packages)

		def work() -> dict[str, Any]:
			succeeded = 0
			failed: list[str] = []
			for package in packages:
				try:
					voice_store.remove_package(package)
					succeeded += 1
				except Exception as exc:
					log.error("Failed to remove %s: %s", package.id, exc)
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
					"Removed {succeeded} of {total}. Failed: {failList}"
				).format(
					succeeded=succeeded,
					total=totalCount,
					failList=", ".join(failed),
				)
			elif succeeded == 1:
				message = _("Removed {package}.").format(package=packages[0].id)
			else:
				message = _("Removed {count} voice packages.").format(count=succeeded)
			self.set_status(message, 100)
			ui.message(message)
			self._focus_active_page()

		self._run_worker(work, done)

	def on_open_folder(self, evt: wx.CommandEvent) -> None:
		try:
			path = voice_store.voice_dir()
			os.startfile(os.fspath(path))  # type: ignore[attr-defined]
		except Exception as exc:
			self.show_error(exc)

	def _run_worker(self, work: Callable[[], Any], done: Callable[[Any | BaseException], None]) -> None:
		if self.isBusy:
			return
		self.isBusy = True
		self._lastProgressAnnouncement = -1
		self.closeButton.SetFocus()
		self._refresh_buttons()
		self.set_status(_("Working..."), 0, announce=True)

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

	def show_error(self, error: BaseException) -> None:
		message = str(error)
		log.error("Google TTS voice manager operation failed: %s", message)
		self.set_status(_("Failed: {message}").format(message=message), 0)
		gui.messageBox(message, _("Google TTS Voice Manager"), wx.OK | wx.ICON_ERROR, self)

	def _refresh_buttons(self) -> None:
		hasInstalledItems = self.installedList.ItemCount > 0
		hasDownloadItems = self.downloadList.ItemCount > 0
		for control in (
			self.refreshButton,
			self.openFolderButton,
			self.installedList,
			self.downloadList,
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
				_("A voice operation is still running."),
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
