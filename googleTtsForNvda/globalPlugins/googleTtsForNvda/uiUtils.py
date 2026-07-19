# -*- coding: utf-8 -*-
from __future__ import annotations

import math

import wx


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


def resize_read_only_text_for_content(
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


def bind_read_only_text_focus_announcement(
	control: wx.TextCtrl,
	minLines: int = 2,
	maxLines: int = 6,
	width: int | None = None,
) -> None:
	# Kept for existing call sites; focus now uses the normal read-only edit behavior.
	resize_read_only_text_for_content(control, minLines=minLines, maxLines=maxLines, width=width)
