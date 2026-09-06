from __future__ import annotations

import contextlib
import math

import wx

try:
    import addonHandler

    addonHandler.initTranslation()
except Exception:

    def _(message: str) -> str:
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
    with contextlib.suppress(Exception):
        control.InvalidateBestSize()


def bind_read_only_text_focus_announcement(
    control: wx.TextCtrl,
    minLines: int = 2,
    maxLines: int = 6,
    width: int | None = None,
) -> None:
    # Kept for existing call sites; focus now uses the normal read-only edit behavior.
    resize_read_only_text_for_content(control, minLines=minLines, maxLines=maxLines, width=width)


def format_size_mb(size: int) -> str:
    """Format bytes as megabytes with 1 decimal place."""
    if size <= 0:
        return ""
    return f"{size / (1024 * 1024):.1f} MB"


def format_size_auto(size: int) -> str:
    """Format bytes automatically choosing MB, KB, or bytes."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def open_synthesizer_dialog(
    parent: wx.Window | None = None,
    title: str | None = None,
) -> bool:
    """Open NVDA's Select Synthesizer dialog cleanly across NVDA versions."""
    import gui
    from logHandler import log

    dialogTitle = title or _("Google TTS For NVDA")
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
            dialogTitle,
            wx.OK | wx.ICON_ERROR,
            parent or gui.mainFrame,
        )
        return False


def show_runtime_error_dialog(
    message: str | None = None,
    parent: wx.Window | None = None,
    title: str | None = None,
    delayMs: int = 0,
) -> None:
    """Show a standardized modal error dialog for Google TTS runtime or speech failures."""
    import gui
    from logHandler import log

    dialogTitle = title or _("Google TTS For NVDA")
    dialogMessage = message or _("Google TTS For NVDA could not start speech in the Chromium browser runtime.")

    def _display() -> None:
        try:
            gui.messageBox(
                dialogMessage,
                dialogTitle,
                wx.OK | wx.ICON_ERROR,
                parent or gui.mainFrame,
            )
        except Exception:
            log.exception("Could not show Google TTS runtime error dialog.", exc_info=True)

    if delayMs > 0:
        try:
            wx.CallLater(delayMs, _display)
            return
        except Exception:
            pass
    _display()
