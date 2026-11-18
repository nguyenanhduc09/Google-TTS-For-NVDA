"""Language and locale normalization helpers for Google TTS For NVDA."""

from __future__ import annotations

import contextlib
import functools
import inspect
import os
import sys
import threading
from typing import Any

try:
    import wx as _wx
except Exception:  # pragma: no cover - NVDA/wx is not available in all unit test contexts.
    _wx = None  # type: ignore[assignment]

SPECIAL_NVDA_LOCALES: dict[str, str] = {
    "cmn-CN": "zh_CN",
    "cmn-TW": "zh_TW",
    "yue-HK": "zh_HK",
    "ar-XA": "ar",
    "fil": "tl",
    "fil-PH": "tl",
}
_SPECIAL_NVDA_LOCALES_LOWER: dict[str, str] = {k.lower(): v for k, v in SPECIAL_NVDA_LOCALES.items()}


def call_compat(target: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke an NVDA callable while filtering parameters unsupported by its signature."""
    if not callable(target):
        raise TypeError(f"{target!r} is not callable")
    try:
        signature = inspect.signature(target, follow_wrapped=False)
    except (TypeError, ValueError):
        try:
            return target(*args, **kwargs)
        except TypeError as exc:
            # Retry without kwargs only when Python rejected an unexpected keyword
            # argument at call dispatch, never when target raised TypeError internally.
            if (
                kwargs
                and exc.__traceback__ is not None
                and exc.__traceback__.tb_next is None
                and "keyword" in str(exc).lower()
            ):
                return target(*args)
            raise
    parameters = signature.parameters
    acceptsVarKeyword = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
    acceptsVarPositional = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in parameters.values())
    positionalParams = [
        param
        for param in parameters.values()
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    keywordNames = {
        param.name
        for param in parameters.values()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    filteredKwargs = dict(kwargs) if acceptsVarKeyword else {k: v for k, v in kwargs.items() if k in keywordNames}
    if not acceptsVarPositional:
        maxPositional = len(positionalParams)
        if len(args) > maxPositional:
            if not acceptsVarKeyword:
                for extraIndex in range(maxPositional, len(args)):
                    extraValue = args[extraIndex]
                    for param in parameters.values():
                        if (
                            param.kind == inspect.Parameter.KEYWORD_ONLY
                            and param.name not in filteredKwargs
                            and param.default is not inspect.Parameter.empty
                            and type(param.default) is type(extraValue)
                        ):
                            filteredKwargs[param.name] = extraValue
                            break
            args = args[:maxPositional]
    if not acceptsVarKeyword:
        boundPositionalNames = {
            param.name
            for param in positionalParams[: len(args)]
            if param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
        }
        for boundName in boundPositionalNames:
            filteredKwargs.pop(boundName, None)
    return target(*args, **filteredKwargs)


def normalize_language(language: str | None) -> str:
    """Normalize a language tag to lower-case with hyphens (e.g. 'vi_vn' -> 'vi-vn')."""
    return str(language or "").strip().replace("_", "-").lower()


def language_root(language: str | None) -> str:
    """Return the lower-case primary language subtag (e.g. 'vi-VN' -> 'vi')."""
    return normalize_language(language).split("-", 1)[0]


def normalize_language_code(language: str | None) -> str:
    """Normalize a language tag preserving case with hyphens (e.g. 'vi_VN' -> 'vi-VN')."""
    return str(language or "").strip().replace("_", "-")


def parse_language_codes(value: str | None) -> list[str]:
    """Parse a comma-separated string into non-empty, case-insensitively deduplicated language codes."""
    codes: list[str] = []
    seen: set[str] = set()
    for rawCode in str(value or "").split(","):
        code = normalize_language_code(rawCode)
        key = code.lower()
        if not code or key in seen:
            continue
        codes.append(code)
        seen.add(key)
    return codes


def normalize_language_key(language: str | None) -> str:
    """Alias for normalize_language for dictionary keys."""
    return normalize_language(language)


def _fallback_normalize_locale(languageText: str) -> str:
    parts = [part for part in languageText.replace("-", "_").split("_") if part]
    if not parts:
        return ""
    normalizedParts = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalizedParts.append(part.title())
        elif len(part) == 2 and part.isalpha():
            normalizedParts.append(part.upper())
        else:
            normalizedParts.append(part)
    return "_".join(normalizedParts)


def get_nvda_locale_for_language(lang_code: str | None) -> str:
    """Convert a language tag into a valid NVDA locale code (e.g. 'cmn-CN' -> 'zh_CN')."""
    if not lang_code:
        return ""
    languageText = str(lang_code).strip()
    if languageText in SPECIAL_NVDA_LOCALES:
        return SPECIAL_NVDA_LOCALES[languageText]
    lowerLanguage = languageText.replace("_", "-").lower()
    if lowerLanguage in _SPECIAL_NVDA_LOCALES_LOWER:
        return _SPECIAL_NVDA_LOCALES_LOWER[lowerLanguage]
    if lowerLanguage.startswith(("cmn-hk", "cmn-mo")):
        return "zh_HK"
    if lowerLanguage.startswith(("cmn-tw", "cmn-hant")):
        return "zh_TW"
    if lowerLanguage.startswith("cmn"):
        return "zh_CN"
    if lowerLanguage.startswith("yue"):
        return "zh_HK"
    try:
        import languageHandler

        normalized = languageHandler.normalizeLanguage(languageText)
    except Exception:
        normalized = ""
    result = str(normalized or "").strip()
    if not result:
        result = _fallback_normalize_locale(languageText)
    return result


@functools.lru_cache(maxsize=64)
def _nvda_locale_exists_in_dir(appDir: str, locale: str) -> bool:
    return bool(appDir) and os.path.isdir(os.path.join(appDir, "locale", locale))


def nvda_locale_exists(locale: str) -> bool:
    """Return True if the given locale exists in NVDA's installation directory."""
    if not locale:
        return False
    try:
        import globalVars

        appDir = str(getattr(globalVars, "appDir", "") or "")
    except Exception:
        return False
    return _nvda_locale_exists_in_dir(appDir, locale)


def resolve_nvda_locale(language: str | None) -> str:
    """Resolve the effective NVDA locale for speech messages or fall back to 'en'."""
    nvdaLocale = get_nvda_locale_for_language(language)
    if not nvdaLocale:
        return "en"
    if nvda_locale_exists(nvdaLocale):
        return nvdaLocale
    rootLocale = nvdaLocale.split("_", 1)[0]
    if rootLocale != nvdaLocale and nvda_locale_exists(rootLocale):
        return rootLocale
    return "en"


def _language_display_candidates(lang_code: str) -> tuple[str, ...]:
    nvdaLocale = get_nvda_locale_for_language(lang_code)
    candidates: list[str] = []
    for candidate in (nvdaLocale, nvdaLocale.split("_", 1)[0] if "_" in nvdaLocale else "", lang_code):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def get_language_display_name(
    lang_code: str,
    custom_language_names: dict[str, str] | None = None,
) -> str:
    """Return a human-readable language description."""
    if custom_language_names:
        normalized = str(lang_code or "").strip().replace("_", "-").lower()
        for code, name in custom_language_names.items():
            if code.replace("_", "-").lower() == normalized:
                return name
    for candidate in _language_display_candidates(lang_code):
        try:
            import languageHandler

            description = languageHandler.getLanguageDescription(candidate)
        except Exception:
            description = None
        if description:
            return description
    return lang_code


def _resolve_alive_dialog_parent(parent: Any, guiMod: Any) -> Any:
    for candidate in (parent, getattr(guiMod, "mainFrame", None)):
        if candidate is None or isinstance(candidate, bool):
            continue
        try:
            isBeingDeleted = getattr(candidate, "IsBeingDeleted", None)
            if callable(isBeingDeleted) and isBeingDeleted():
                continue
            getId = getattr(candidate, "GetId", None)
            if callable(getId):
                getId()
            return candidate
        except RuntimeError:
            continue
    return None


def show_message_box(
    message: str,
    caption: str,
    style: int,
    parent: Any = None,
) -> int:
    """Display a modal message box across NVDA 2024–2027."""
    wx = _wx if _wx is not None else sys.modules.get("wx")
    if wx is None:
        import wx as _imported_wx

        wx = _imported_wx

    try:
        import core

        if getattr(core, "_hasShutdownBeenTriggered", False):
            return int(getattr(wx, "CANCEL", 16))
    except Exception:
        pass
    try:
        import gui
    except Exception:
        gui = None  # type: ignore

    dialogParent = _resolve_alive_dialog_parent(parent, gui) if gui is not None else None
    messageMod = getattr(gui, "message", None) if gui is not None else None
    guiMsgBox = getattr(gui, "messageBox", None) if gui is not None else None
    modMsgBox = getattr(messageMod, "messageBox", None) if messageMod is not None else None
    messageDialogCls = getattr(messageMod, "MessageDialog", None)
    dialogTypeEnum = getattr(messageMod, "DialogType", None)
    defaultButtonEnum = getattr(messageMod, "DefaultButton", None)
    returnCodeEnum = getattr(messageMod, "ReturnCode", None)
    isMainThreadFn = getattr(wx, "IsMainThread", None)
    onMainThread = not callable(isMainThreadFn) or bool(isMainThreadFn())
    try:
        guiHelperMod = getattr(gui, "guiHelper", None)
    except Exception:
        guiHelperMod = None
    wxCallOnMain = getattr(messageMod, "wxCallOnMain", None) or getattr(guiHelperMod, "wxCallOnMain", None)
    if (
        callable(messageDialogCls)
        and dialogTypeEnum is not None
        and defaultButtonEnum is not None
        and returnCodeEnum is not None
        and (guiMsgBox is None or guiMsgBox is modMsgBox)
        and (onMainThread or callable(wxCallOnMain))
    ):
        if style & getattr(wx, "ICON_NONE", 0):
            dialogType = dialogTypeEnum.STANDARD
        elif style & getattr(wx, "ICON_ERROR", 0):
            dialogType = dialogTypeEnum.ERROR
        elif style & getattr(wx, "ICON_WARNING", 0):
            dialogType = dialogTypeEnum.WARNING
        else:
            dialogType = dialogTypeEnum.STANDARD
        buttons: list[Any] = []
        cancelDefault = bool(
            (style & int(getattr(wx, "CANCEL_DEFAULT", 0)))
            and not ((style & int(getattr(wx, "NO", 0))) and (style & int(getattr(wx, "NO_DEFAULT", 0))))
        )
        if style & (int(getattr(wx, "YES", 0)) | int(getattr(wx, "NO", 0))):
            noDefault = bool(style & int(getattr(wx, "NO_DEFAULT", 0)))
            yesBtn = defaultButtonEnum.YES
            yesReplaceFn = getattr(yesBtn, "_replace", None) or getattr(
                getattr(yesBtn, "value", None), "_replace", None
            )
            if callable(yesReplaceFn) and (noDefault or cancelDefault):
                yesBtn = yesReplaceFn(defaultFocus=False)
            noBtn = defaultButtonEnum.NO
            replaceFn = getattr(noBtn, "_replace", None) or getattr(getattr(noBtn, "value", None), "_replace", None)
            if callable(replaceFn):
                noBtn = replaceFn(defaultFocus=noDefault)
            buttons.extend((yesBtn, noBtn))
        else:
            okBtn = defaultButtonEnum.OK
            if cancelDefault and (style & int(getattr(wx, "CANCEL", 0))):
                replaceFn = getattr(okBtn, "_replace", None) or getattr(getattr(okBtn, "value", None), "_replace", None)
                if callable(replaceFn):
                    okBtn = replaceFn(defaultFocus=False)
            buttons.append(okBtn)
        if style & int(getattr(wx, "CANCEL", 0)):
            cancelBtn = defaultButtonEnum.CANCEL
            replaceFn = getattr(cancelBtn, "_replace", None) or getattr(
                getattr(cancelBtn, "value", None), "_replace", None
            )
            if callable(replaceFn):
                cancelBtn = replaceFn(defaultFocus=cancelDefault)
            buttons.append(cancelBtn)
        if style & int(getattr(wx, "HELP", 0)) and hasattr(defaultButtonEnum, "HELP"):
            buttons.append(defaultButtonEnum.HELP)

        codeMap: dict[int, int] = {}
        for enumName, wxName, idName in (
            ("YES", "YES", "ID_YES"),
            ("NO", "NO", "ID_NO"),
            ("CANCEL", "CANCEL", "ID_CANCEL"),
            ("OK", "OK", "ID_OK"),
            ("HELP", "HELP", "ID_HELP"),
        ):
            enumMember = getattr(returnCodeEnum, enumName, None)
            wxValue = getattr(wx, wxName, None)
            wxIdValue = getattr(wx, idName, None)
            if enumMember is not None and wxValue is not None:
                with contextlib.suppress(TypeError, ValueError):
                    codeMap[int(getattr(enumMember, "value", enumMember))] = int(wxValue)
            if wxIdValue is not None and wxValue is not None:
                codeMap.setdefault(int(wxIdValue), int(wxValue))
            if wxValue is not None:
                codeMap.setdefault(int(wxValue), int(wxValue))

        def _show_modern() -> int:
            mainFrame = getattr(gui, "mainFrame", None)
            needPopupWrap = dialogParent is mainFrame and mainFrame is not None
            if needPopupWrap:
                prePopup = getattr(mainFrame, "prePopup", None)
                if callable(prePopup):
                    with contextlib.suppress(Exception):
                        prePopup()
            try:
                dialog = messageDialogCls(
                    dialogParent, message, title=caption, dialogType=dialogType, buttons=tuple(buttons)
                )
                try:
                    rawResult = dialog.ShowModal()
                finally:
                    destroyFn = getattr(dialog, "Destroy", None)
                    if callable(destroyFn):
                        with contextlib.suppress(Exception):
                            destroyFn()
                try:
                    numericResult = int(getattr(rawResult, "value", rawResult))
                except (TypeError, ValueError):
                    return int(getattr(wx, "CANCEL", 16))
                return codeMap.get(numericResult, numericResult)
            finally:
                if needPopupWrap:
                    postPopup = getattr(mainFrame, "postPopup", None)
                    if callable(postPopup):
                        with contextlib.suppress(Exception):
                            postPopup()

        if not onMainThread and callable(wxCallOnMain):
            return int(wxCallOnMain(_show_modern))
        return _show_modern()

    def _show_legacy() -> int:
        if callable(guiMsgBox):
            return int(guiMsgBox(message, caption, style, dialogParent))
        wxMsgBox = getattr(wx, "MessageBox", None)
        if callable(wxMsgBox):
            mainFrame = getattr(gui, "mainFrame", None)
            needPopupWrap = dialogParent is None and mainFrame is not None
            if needPopupWrap:
                prePopup = getattr(mainFrame, "prePopup", None)
                if callable(prePopup):
                    with contextlib.suppress(Exception):
                        prePopup()
            try:
                return int(wxMsgBox(message, caption, style, dialogParent))
            finally:
                if needPopupWrap:
                    postPopup = getattr(mainFrame, "postPopup", None)
                    if callable(postPopup):
                        with contextlib.suppress(Exception):
                            postPopup()
        return int(getattr(wx, "CANCEL", 16))

    if not onMainThread:
        callAfter = getattr(wx, "CallAfter", None)
        getApp = getattr(wx, "GetApp", None)
        hasApp = callable(getApp) and (getApp() is not None)
        if not callable(callAfter):
            return _show_legacy() if not hasApp else int(getattr(wx, "CANCEL", 16))
        if callable(getApp) and not hasApp:
            return _show_legacy()
        done = threading.Event()
        resultBox: list[int] = []
        errorBox: list[BaseException] = []

        def _runner() -> None:
            try:
                resultBox.append(int(_show_legacy()))
            except BaseException as exc:
                errorBox.append(exc)
            finally:
                done.set()

        try:
            callAfter(_runner)
        except Exception:
            return int(getattr(wx, "CANCEL", 16))
        while not done.wait(0.25):
            try:
                import core

                if getattr(core, "_hasShutdownBeenTriggered", False):
                    return int(getattr(wx, "CANCEL", 16))
            except Exception:
                pass
            if callable(getApp) and getApp() is None:
                return int(getattr(wx, "CANCEL", 16))
        if errorBox:
            raise errorBox[0]
        return resultBox[0] if resultBox else int(getattr(wx, "CANCEL", 16))
    return _show_legacy()
