from __future__ import annotations

import contextlib
import math
import sys
from collections.abc import Callable
from typing import Any

import wx
from synthDrivers.googleTtsForNvda import language_utils
from synthDrivers.googleTtsForNvda.bridge import SYNTH_NAME
from synthDrivers.googleTtsForNvda.language_utils import call_compat as call_compat

try:
    import globalVars as _globalVars
except Exception:
    _globalVars = None  # type: ignore[assignment]

try:
    import synthDriverHandler as _synthDriverHandler
except Exception:
    _synthDriverHandler = None  # type: ignore[assignment]

try:
    import addonHandler

    addonHandler.initTranslation()
except Exception:

    def _(message: str) -> str:
        return message


def is_secure_mode() -> bool:
    """Return True when NVDA is running in secure mode."""
    try:
        globalVars = _globalVars if _globalVars is not None else sys.modules.get("globalVars")
        return bool(getattr(getattr(globalVars, "appArgs", None), "secure", False))
    except Exception:
        return False


def is_google_synth_current() -> bool:
    """Return True when Google TTS For NVDA is the active synthesizer."""
    try:
        synthDriverHandler = (
            _synthDriverHandler if _synthDriverHandler is not None else sys.modules.get("synthDriverHandler")
        )
        if synthDriverHandler is None:
            return False
        return getattr(synthDriverHandler.getSynth(), "name", "") == SYNTH_NAME
    except Exception:
        return False


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
    if not window_is_alive(control):
        return
    try:
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
    except RuntimeError:
        return


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


def window_is_alive(window: wx.Window | None) -> bool:
    """Return True if a wx window object still exists and is not being deleted."""
    if window is None or isinstance(window, bool):
        return False
    try:
        if hasattr(type(window), "__bool__") and not bool(window):
            return False
        isBeingDeleted = getattr(window, "IsBeingDeleted", None)
        if callable(isBeingDeleted) and isBeingDeleted():
            return False
        getId = getattr(window, "GetId", None)
        if callable(getId):
            getId()
        return True
    except RuntimeError:
        return False
    except Exception:
        return False


def dialog_parent(preferredParent: wx.Window | None = None) -> wx.Window | None:
    """Return a live top-level parent window, falling back to gui.mainFrame when alive."""
    if window_is_alive(preferredParent):
        getTopLevelParent = getattr(preferredParent, "GetTopLevelParent", None)
        if callable(getTopLevelParent):
            try:
                parent = getTopLevelParent()
                if window_is_alive(parent):
                    return parent
            except RuntimeError:
                pass
        return preferredParent
    try:
        import gui
    except Exception:
        return None
    mainFrame = getattr(gui, "mainFrame", None)
    if window_is_alive(mainFrame):
        return mainFrame
    return None


def pre_popup() -> None:
    """Safely invoke gui.mainFrame.prePopup() when mainFrame is available."""
    try:
        import gui
    except Exception:
        return
    mainFrame = getattr(gui, "mainFrame", None)
    prePopup = getattr(mainFrame, "prePopup", None)
    if callable(prePopup):
        with contextlib.suppress(RuntimeError):
            prePopup()


def post_popup() -> None:
    """Safely invoke gui.mainFrame.postPopup() when mainFrame is available."""
    try:
        import gui
    except Exception:
        return
    mainFrame = getattr(gui, "mainFrame", None)
    postPopup = getattr(mainFrame, "postPopup", None)
    if callable(postPopup):
        with contextlib.suppress(RuntimeError):
            postPopup()


def is_windows_store_nvda() -> bool:
    """Return True on Windows Store (AppX) NVDA builds where the attribute exists."""
    try:
        import config
    except Exception:
        return False
    return bool(getattr(config, "isAppX", False))


def show_message_box(
    message: str,
    caption: str | None = None,
    style: int | None = None,
    parent: wx.Window | None = None,
) -> int:
    """Display a modal message box compatible with NVDA 2024 through 2026+ and main."""
    dialogTitle = caption or _("Google TTS For NVDA")
    effectiveStyle = int(getattr(wx, "OK", 4) if style is None else style)
    return language_utils.show_message_box(message, dialogTitle, effectiveStyle, parent)


def stop_call_later(timer: Any) -> None:
    """Safely stop a wx.CallLater timer handle if present."""
    if timer is None:
        return
    stopFn = getattr(timer, "Stop", None)
    if callable(stopFn):
        with contextlib.suppress(Exception):
            stopFn()


def step_after_synth_switch(
    attempts: int,
    *,
    isStillActive: Callable[[], bool],
    isGoogleSynthCurrent: Callable[[], bool],
    onReady: Callable[[], None],
    onTimeout: Callable[[], None],
    retry: Callable[[int], None],
    isBusy: Callable[[], bool] | None = None,
    maxAttempts: int = 600,
) -> None:
    """Advance one 500 ms poll step waiting for the user to switch away from Google TTS."""
    if not isStillActive():
        return
    if not isGoogleSynthCurrent():
        if isBusy is not None and isBusy():
            retry(attempts)
            return
        onReady()
        return
    if attempts >= maxAttempts:
        onTimeout()
        return
    retry(attempts + 1)


def is_modal_dialog_open() -> bool:
    """Return True when NVDA reports a blocking modal dialog or message box is open."""
    try:
        import gui
    except Exception:
        return False
    try:
        blockAction = getattr(gui, "blockAction", None)
        if blockAction is None:
            from gui import blockAction  # type: ignore[no-redef]
        context = getattr(getattr(blockAction, "Context", None), "MODAL_DIALOG_OPEN", None)
        blockFn = getattr(context, "blockActionIf", None)
        if callable(blockFn):
            return bool(blockFn())
    except Exception:
        pass
    try:
        messageMod = getattr(gui, "message", None)
        if messageMod is None:
            from gui import message as messageMod  # type: ignore[no-redef]
        isModalActive = getattr(messageMod, "isModalMessageBoxActive", None)
        if callable(isModalActive):
            return bool(isModalActive())
    except Exception:
        pass
    return False


def open_synthesizer_dialog(
    parent: wx.Window | None = None,
    title: str | None = None,
) -> bool:
    """Open NVDA's Select Synthesizer dialog across NVDA versions."""
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
        mainFrame = getattr(gui, "mainFrame", None)
        if mainFrame is None or not callable(getattr(mainFrame, "popupSettingsDialog", None)):
            raise RuntimeError("NVDA mainFrame is unavailable.")
        if is_modal_dialog_open():
            mainFrame.popupSettingsDialog(dialogClass)
            return False
        mainFrame.popupSettingsDialog(dialogClass)
        return True
    except Exception as exc:
        log.error("Could not open Select Synthesizer dialog: %s", exc)
        show_message_box(
            _("The Select Synthesizer dialog could not be opened."),
            dialogTitle,
            wx.OK | wx.ICON_ERROR,
            dialog_parent(parent),
        )
        return False


def show_runtime_error_dialog(
    message: str | None = None,
    parent: wx.Window | None = None,
    title: str | None = None,
    delayMs: int = 0,
) -> None:
    """Show a standardized modal error dialog for Google TTS runtime or speech failures."""
    isMainThread = getattr(wx, "IsMainThread", None)
    if callable(isMainThread):
        try:
            onMainThread = bool(isMainThread())
        except Exception:
            onMainThread = True
        if not onMainThread:
            # Never fall through to modal GUI work on a worker thread, even if CallAfter fails.
            try:
                wx.CallAfter(
                    show_runtime_error_dialog,
                    message=message,
                    parent=parent,
                    title=title,
                    delayMs=delayMs,
                )
            except Exception:
                with contextlib.suppress(Exception):
                    from logHandler import log

                    log.debug("Could not marshal Google TTS runtime error dialog to the main thread.", exc_info=True)
            return

    dialogTitle = title or _("Google TTS For NVDA")
    dialogMessage = message or _("Google TTS For NVDA could not start speech in the Chromium browser runtime.")

    def _display() -> None:
        try:
            show_message_box(
                dialogMessage,
                dialogTitle,
                wx.OK | wx.ICON_ERROR,
                dialog_parent(parent),
            )
        except Exception:
            with contextlib.suppress(Exception):
                from logHandler import log

                log.exception("Could not show Google TTS runtime error dialog.", exc_info=True)

    if delayMs > 0:
        try:
            wx.CallLater(delayMs, _display)
            return
        except Exception:
            pass
    _display()
