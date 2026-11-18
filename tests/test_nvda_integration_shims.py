"""Execution tests for real synthDrivers and globalPlugins NVDA integration hooks.

Loads the real addon modules inside an isolated stub-NVDA environment (restoring
sys.modules, sys.path, and builtins._ immediately after import) so runtime behavior
across speech streaming, WavePlayer recovery, fatal fallback, voice dictionaries,
auto-language hooks, SayAll, dialogs, compat shims, and updater integration is
verified directly on production code.
"""

from __future__ import annotations

import builtins
import contextlib
import gc
import importlib.util
import inspect
import logging
import sys
import tempfile
import threading
import unittest
import weakref
from collections import OrderedDict, deque
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

from tests.test_support import ROOT, FakeConf, is_lock_free_from_other_thread

ADDON_ROOT = ROOT / "googleTtsForNvda"


class _FakeHandlerRegistrar:
    def __init__(self, *, weak: bool = False) -> None:
        self._weak = weak
        self._handlers: list[Any] = []

    def register(self, handler: Any) -> None:
        if inspect.isfunction(handler):
            sig = inspect.signature(handler)
            if sig.parameters and list(sig.parameters)[0] == "self":
                raise TypeError("Registering unbound instance methods not supported.")
        self._handlers.append(weakref.ref(handler) if self._weak else handler)

    def unregister(self, handler: Any) -> None:
        if self._weak:
            self._handlers = [ref for ref in self._handlers if ref() is not None and ref() is not handler]
        else:
            with contextlib.suppress(ValueError):
                self._handlers.remove(handler)

    def fire(self) -> None:
        for item in list(self._handlers):
            handler = item() if self._weak else item
            if handler is not None:
                handler(futureEventArg="ready")


def _make_stub_nvda_modules() -> dict[str, ModuleType]:
    class _StubWxBase:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _FakeExtensionPoint:
        def __init__(self) -> None:
            self._handlers: list[Any] = []

        def register(self, fn: Any) -> None:
            self._handlers.append(fn)

        def unregister(self, fn: Any) -> None:
            with contextlib.suppress(ValueError):
                self._handlers.remove(fn)

        def moveToEnd(self, fn: Any, last: bool = False) -> bool:
            return True

        def notify(self, *args: Any, **kwargs: Any) -> None:
            for handler in list(self._handlers):
                handler(*args, **kwargs)

    class _FakeSpeechCommand:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    class _BreakCommand(_FakeSpeechCommand):
        def __init__(self, time: int = 0) -> None:
            super().__init__(time=time)
            self.time = time

    class _IndexCommand(_FakeSpeechCommand):
        def __init__(self, index: Any = None) -> None:
            super().__init__(index=index)
            self.index = index

    class _LangChangeCommand(_FakeSpeechCommand):
        def __init__(self, lang: str | None = None) -> None:
            super().__init__(lang)
            self.lang = lang

    class _PhonemeCommand(_FakeSpeechCommand):
        def __init__(self, ipa: str = "", text: str | None = None) -> None:
            super().__init__(ipa, text=text)
            self.ipa = ipa
            self.text = text

    class _ProsodyCommand(_FakeSpeechCommand):
        def __init__(self, offset: int = 0, multiplier: float = 1) -> None:
            super().__init__(offset=offset, multiplier=multiplier)
            self.offset = offset
            self.multiplier = multiplier

    class _RateCommand(_ProsodyCommand):
        pass

    class _PitchCommand(_ProsodyCommand):
        pass

    class _VolumeCommand(_ProsodyCommand):
        pass

    class _CharacterModeCommand(_FakeSpeechCommand):
        def __init__(self, state: bool = False) -> None:
            super().__init__(state=state)
            self.state = bool(state)

    class _EndUtteranceCommand(_FakeSpeechCommand):
        pass

    class _SuppressUnicodeNormalizationCommand(_FakeSpeechCommand):
        def __init__(self, state: bool = False) -> None:
            super().__init__(state=state)
            self.state = bool(state)

    class _DriverSetting:
        def __init__(
            self,
            id: str = "",
            displayNameWithAccelerator: str = "",
            availableInSettingsRing: bool = False,
            defaultVal: Any = None,
            useConfig: bool = True,
            **kwargs: Any,
        ) -> None:
            self.id = id
            self.displayNameWithAccelerator = displayNameWithAccelerator
            self.availableInSettingsRing = availableInSettingsRing
            self.defaultVal = defaultVal
            self.useConfig = useConfig

    class _BooleanDriverSetting(_DriverSetting):
        pass

    class _NumericDriverSetting(_DriverSetting):
        pass

    class _StringParameterInfo:
        def __init__(self, id: str, displayName: str) -> None:
            self.id = id
            self.displayName = displayName

    class _VoiceInfo(_StringParameterInfo):
        def __init__(self, id: str, displayName: str, language: str | None = None) -> None:
            super().__init__(id, displayName)
            self.language = language

    class _SynthDriver:
        name = "googleTtsForNvda"

        VoiceSetting = classmethod(lambda cls: _DriverSetting("voice", "&Voice"))
        VariantSetting = classmethod(lambda cls: _DriverSetting("variant", "V&ariant"))
        RateSetting = classmethod(lambda cls: _NumericDriverSetting("rate", "&Rate", defaultVal=50))
        RateBoostSetting = classmethod(lambda cls: _BooleanDriverSetting("rateBoost", "Rate &boost", defaultVal=False))
        PitchSetting = classmethod(lambda cls: _NumericDriverSetting("pitch", "&Pitch", defaultVal=50))
        VolumeSetting = classmethod(lambda cls: _NumericDriverSetting("volume", "V&olume", defaultVal=100))

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        @property
        def availableVoices(self) -> Any:
            getter = getattr(self, "_getAvailableVoices", None)
            if callable(getter):
                return getter()
            return getattr(self, "_availableVoices", {})

        @property
        def availableLanguages(self) -> Any:
            getter = getattr(self, "_get_availableLanguages", None)
            if callable(getter):
                return getter()
            return getattr(self, "_availableLanguages", set())

        @property
        def voice(self) -> str:
            return str(self._get_voice())  # type: ignore[attr-defined]

        @voice.setter
        def voice(self, value: str) -> None:
            self._set_voice(value)  # type: ignore[attr-defined]

        @property
        def variant(self) -> str:
            return str(self._get_variant())  # type: ignore[attr-defined]

        @variant.setter
        def variant(self, value: str) -> None:
            self._set_variant(value)  # type: ignore[attr-defined]

        @property
        def language(self) -> str:
            return str(self._get_language())  # type: ignore[attr-defined]

        @property
        def rate(self) -> int:
            return int(self._get_rate())  # type: ignore[attr-defined]

        @rate.setter
        def rate(self, value: int) -> None:
            self._set_rate(value)  # type: ignore[attr-defined]

        @property
        def rateBoost(self) -> bool:
            return bool(self._get_rateBoost())  # type: ignore[attr-defined]

        @rateBoost.setter
        def rateBoost(self, value: bool) -> None:
            self._set_rateBoost(value)  # type: ignore[attr-defined]

        @property
        def pitch(self) -> int:
            return int(self._get_pitch())  # type: ignore[attr-defined]

        @pitch.setter
        def pitch(self, value: int) -> None:
            self._set_pitch(value)  # type: ignore[attr-defined]

        @property
        def volume(self) -> int:
            return int(self._get_volume())  # type: ignore[attr-defined]

        @volume.setter
        def volume(self, value: int) -> None:
            self._set_volume(value)  # type: ignore[attr-defined]

        @property
        def pauseMode(self) -> str:
            return str(self._get_pauseMode())  # type: ignore[attr-defined]

        @pauseMode.setter
        def pauseMode(self, value: str) -> None:
            self._set_pauseMode(value)  # type: ignore[attr-defined]

        @property
        def notice(self) -> str:
            return str(self._get_notice())  # type: ignore[attr-defined]

        @notice.setter
        def notice(self, value: str) -> None:
            self._set_notice(value)  # type: ignore[attr-defined]

        def isSupported(self, settingID: str) -> bool:
            supportedSettings = getattr(self, "supportedSettings", None)
            if supportedSettings is not None:
                return any(getattr(s, "id", None) == settingID for s in supportedSettings)
            return settingID in {"voice", "variant", "rate", "rateBoost", "pitch", "volume", "pauseMode"}

        def loadSettings(self, onlyChanged: bool = False) -> None:
            pass

        def terminate(self) -> None:
            pass

    class _GlobalPlugin:
        def __init__(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    class _AutoSettingsMixin:
        def _getSettingMaker(self, setting: Any, *args: Any, **kwargs: Any) -> Any:
            return lambda *a, **k: None

        def _updateValueForControl(self, setting: Any, settingsStorage: Any = None, *args: Any, **kwargs: Any) -> None:
            pass

        def onDiscard(self, *args: Any, **kwargs: Any) -> None:
            pass

        def refreshGui(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _VoiceSettingsPanel(_AutoSettingsMixin):
        def makeSettings(self, settingsSizer: Any, *args: Any, **kwargs: Any) -> None:
            pass

    class _VoiceDictionaryDialog:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _SettingsPanel(_StubWxBase):
        title = ""

        def makeSettings(self, settingsSizer: Any) -> None:
            pass

    class _NVDASettingsDialog:
        categoryClasses: list[Any] = []

    class _WavePlayer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    addonHandlerMod = ModuleType("addonHandler")
    addonHandlerMod.initTranslation = lambda: builtins.__dict__.setdefault("_", lambda s: s)  # type: ignore[attr-defined]

    configMod = ModuleType("config")
    configMod.conf = FakeConf()  # type: ignore[attr-defined]
    configMod.isInstalledCopy = lambda: False  # type: ignore[attr-defined]

    globalPluginHandlerMod = ModuleType("globalPluginHandler")
    globalPluginHandlerMod.GlobalPlugin = _GlobalPlugin  # type: ignore[attr-defined]

    globalVarsMod = ModuleType("globalVars")
    globalVarsMod.appArgs = SimpleNamespace(secure=False, configPath=None)  # type: ignore[attr-defined]
    globalVarsMod.appDir = str(ADDON_ROOT)  # type: ignore[attr-defined]

    languageHandlerMod = ModuleType("languageHandler")
    languageHandlerMod.getLanguage = lambda: "en"  # type: ignore[attr-defined]
    languageHandlerMod.normalizeLanguage = lambda lang: str(lang or "").replace("-", "_")  # type: ignore[attr-defined]
    languageHandlerMod.getLanguageDescription = lambda lang: str(lang or "")  # type: ignore[attr-defined]

    logHandlerMod = ModuleType("logHandler")
    logger = logging.getLogger("nvda-shim-test")
    logger.debugWarning = logger.warning  # type: ignore[attr-defined]
    logHandlerMod.log = logger  # type: ignore[attr-defined]

    nvwaveMod = ModuleType("nvwave")
    nvwaveMod.WavePlayer = _WavePlayer  # type: ignore[attr-defined]
    nvwaveMod.AudioPurpose = SimpleNamespace(SPEECH="speech")  # type: ignore[attr-defined]
    nvwaveMod.isInError = lambda: False  # type: ignore[attr-defined]

    wxMod = ModuleType("wx")
    for name in (
        "Panel",
        "Dialog",
        "BoxSizer",
        "StaticBoxSizer",
        "TextCtrl",
        "CheckBox",
        "Choice",
        "Slider",
        "Button",
        "StaticText",
        "ListCtrl",
        "Notebook",
        "Timer",
        "Gauge",
    ):
        setattr(wxMod, name, type(name, (_StubWxBase,), {}))
    wxMod.ID_ANY = -1  # type: ignore[attr-defined]
    wxMod.NOT_FOUND = -1  # type: ignore[attr-defined]
    wxMod.ID_OK = 5100  # type: ignore[attr-defined]
    wxMod.ID_CANCEL = 5101  # type: ignore[attr-defined]
    wxMod.ID_YES = 5103  # type: ignore[attr-defined]
    wxMod.ID_NO = 5104  # type: ignore[attr-defined]
    wxMod.OK = 4  # type: ignore[attr-defined]
    wxMod.CANCEL = 16  # type: ignore[attr-defined]
    wxMod.YES = 2  # type: ignore[attr-defined]
    wxMod.NO = 8  # type: ignore[attr-defined]
    wxMod.YES_NO = wxMod.YES | wxMod.NO  # type: ignore[attr-defined]
    wxMod.NO_DEFAULT = 128  # type: ignore[attr-defined]
    wxMod.ICON_INFORMATION = 256  # type: ignore[attr-defined]
    wxMod.ICON_WARNING = 512  # type: ignore[attr-defined]
    wxMod.ICON_ERROR = 1024  # type: ignore[attr-defined]
    wxMod.ICON_QUESTION = 2048  # type: ignore[attr-defined]
    wxMod.EVT_CHOICE = "EVT_CHOICE"  # type: ignore[attr-defined]
    wxMod.EVT_BUTTON = "EVT_BUTTON"  # type: ignore[attr-defined]
    wxMod.EVT_CHECKBOX = "EVT_CHECKBOX"  # type: ignore[attr-defined]
    wxMod.EVT_CHAR_HOOK = "EVT_CHAR_HOOK"  # type: ignore[attr-defined]
    wxMod.EVT_WINDOW_DESTROY = "EVT_WINDOW_DESTROY"  # type: ignore[attr-defined]
    wxMod.EVT_SLIDER = "EVT_SLIDER"  # type: ignore[attr-defined]
    wxMod.EVT_CLOSE = "EVT_CLOSE"  # type: ignore[attr-defined]
    wxMod.EVT_MENU = "EVT_MENU"  # type: ignore[attr-defined]
    wxMod.EVT_KEY_DOWN = "EVT_KEY_DOWN"  # type: ignore[attr-defined]
    wxMod.EVT_SET_FOCUS = "EVT_SET_FOCUS"  # type: ignore[attr-defined]
    wxMod.WXK_PAGEUP = 366  # type: ignore[attr-defined]
    wxMod.WXK_PAGEDOWN = 367  # type: ignore[attr-defined]
    wxMod.SL_HORIZONTAL = 4  # type: ignore[attr-defined]
    wxMod.VERTICAL = 1  # type: ignore[attr-defined]
    wxMod.HORIZONTAL = 2  # type: ignore[attr-defined]
    wxMod.EXPAND = 4  # type: ignore[attr-defined]
    wxMod.ALL = 8  # type: ignore[attr-defined]
    wxMod.RIGHT = 4096  # type: ignore[attr-defined]
    wxMod.ALIGN_RIGHT = 8192  # type: ignore[attr-defined]
    wxMod.TE_READONLY = 16  # type: ignore[attr-defined]
    wxMod.TE_MULTILINE = 32  # type: ignore[attr-defined]
    wxMod.TE_DONTWRAP = 64  # type: ignore[attr-defined]
    wxMod.TE_PROCESS_ENTER = 128  # type: ignore[attr-defined]
    wxMod.BORDER_NONE = 256  # type: ignore[attr-defined]
    wxMod.TE_WORDWRAP = 512  # type: ignore[attr-defined]
    wxMod.GetApp = lambda: object()  # type: ignore[attr-defined]
    wxMod.GetTopLevelWindows = lambda: []  # type: ignore[attr-defined]
    wxMod.CallLater = lambda delay, fn, *args, **kwargs: None  # type: ignore[attr-defined]
    wxMod.CallAfter = lambda fn, *args, **kwargs: fn(*args, **kwargs)  # type: ignore[attr-defined]
    wxMod.IsMainThread = lambda: True  # type: ignore[attr-defined]

    guiMod = ModuleType("gui")
    guiHelperMod = ModuleType("gui.guiHelper")
    guiHelperMod.BORDER_FOR_DIALOGS = 10  # type: ignore[attr-defined]
    guiHelperMod.BoxSizerHelper = type("BoxSizerHelper", (), {})  # type: ignore[attr-defined]
    guiHelperMod.LabeledControlHelper = type("LabeledControlHelper", (), {})  # type: ignore[attr-defined]
    nvdaControlsMod = ModuleType("gui.nvdaControls")
    nvdaControlsMod.SelectOnFocusSpinCtrl = type("SelectOnFocusSpinCtrl", (), {})  # type: ignore[attr-defined]
    nvdaControlsMod.DPIScaledDialog = _StubWxBase  # type: ignore[attr-defined]
    settingsDialogsMod = ModuleType("gui.settingsDialogs")
    settingsDialogsMod.AutoSettingsMixin = _AutoSettingsMixin  # type: ignore[attr-defined]
    settingsDialogsMod.VoiceSettingsPanel = _VoiceSettingsPanel  # type: ignore[attr-defined]
    settingsDialogsMod.SettingsPanel = _SettingsPanel  # type: ignore[attr-defined]
    settingsDialogsMod.NVDASettingsDialog = _NVDASettingsDialog  # type: ignore[attr-defined]
    settingsDialogsMod.VoiceDictionaryDialog = _VoiceDictionaryDialog  # type: ignore[attr-defined]
    guiMod.guiHelper = guiHelperMod  # type: ignore[attr-defined]
    guiMod.nvdaControls = nvdaControlsMod  # type: ignore[attr-defined]
    guiMod.settingsDialogs = settingsDialogsMod  # type: ignore[attr-defined]
    guiMod.DPIScaledDialog = _StubWxBase  # type: ignore[attr-defined]
    guiMod.messageBox = lambda *a, **k: wxMod.CANCEL  # type: ignore[attr-defined]
    guiMod.mainFrame = SimpleNamespace(  # type: ignore[attr-defined]
        popupSettingsDialog=lambda *a, **k: None,
        prePopup=lambda *a, **k: None,
        postPopup=lambda *a, **k: None,
        sysTrayIcon=SimpleNamespace(
            toolsMenu=SimpleNamespace(Append=lambda *a, **k: None, Remove=lambda *a, **k: None),
            Bind=lambda *a, **k: None,
            Unbind=lambda *a, **k: None,
        ),
    )

    speechPkg = ModuleType("speech")
    speechPkg.__path__ = []  # type: ignore[attr-defined]
    speechExtensionsMod = ModuleType("speech.extensions")
    speechExtensionsMod.filter_speechSequence = _FakeExtensionPoint()  # type: ignore[attr-defined]
    speechShortcutKeysMod = ModuleType("speech.shortcutKeys")
    speechShortcutKeysMod.shouldUseSpellingFunctionality = lambda text: False  # type: ignore[attr-defined]
    speechInnerMod = ModuleType("speech.speech")
    speechInnerMod.speak = lambda *a, **k: None  # type: ignore[attr-defined]
    speechInnerMod.processText = lambda *a, **k: ""  # type: ignore[attr-defined]
    speechInnerMod.getSpellingSpeech = lambda *a, **k: iter(())  # type: ignore[attr-defined]
    speechInnerMod.getSingleCharDescription = lambda *a, **k: iter(())  # type: ignore[attr-defined]
    speechCommandsMod = ModuleType("speech.commands")
    for cname, cls in (
        ("BreakCommand", _BreakCommand),
        ("CharacterModeCommand", _CharacterModeCommand),
        ("EndUtteranceCommand", _EndUtteranceCommand),
        ("IndexCommand", _IndexCommand),
        ("LangChangeCommand", _LangChangeCommand),
        ("PhonemeCommand", _PhonemeCommand),
        ("PitchCommand", _PitchCommand),
        ("RateCommand", _RateCommand),
        ("SuppressUnicodeNormalizationCommand", _SuppressUnicodeNormalizationCommand),
        ("VolumeCommand", _VolumeCommand),
    ):
        setattr(speechCommandsMod, cname, cls)
    speechSayAllMod = ModuleType("speech.sayAll")
    speechSayAllMod.SayAllHandler = SimpleNamespace(speechWithoutPausesInstance=SimpleNamespace())  # type: ignore[attr-defined]
    speechLangHandlingMod = ModuleType("speech.languageHandling")
    speechLangHandlingMod.shouldMakeLangChangeCommand = lambda: True  # type: ignore[attr-defined]
    speechLangHandlingMod.shouldSwitchVoice = lambda *a, **k: False  # type: ignore[attr-defined]
    speechManagerMod = ModuleType("speech.manager")
    speechManagerMod.shouldSwitchVoice = lambda *a, **k: False  # type: ignore[attr-defined]
    speechPkg.extensions = speechExtensionsMod  # type: ignore[attr-defined]
    speechPkg.shortcutKeys = speechShortcutKeysMod  # type: ignore[attr-defined]
    speechPkg.speech = speechInnerMod  # type: ignore[attr-defined]
    speechPkg.speak = speechInnerMod.speak  # type: ignore[attr-defined]
    speechPkg.processText = speechInnerMod.processText  # type: ignore[attr-defined]
    speechPkg.getSpellingSpeech = speechInnerMod.getSpellingSpeech  # type: ignore[attr-defined]
    speechPkg.getSingleCharDescription = speechInnerMod.getSingleCharDescription  # type: ignore[attr-defined]
    speechPkg.commands = speechCommandsMod  # type: ignore[attr-defined]
    speechPkg.sayAll = speechSayAllMod  # type: ignore[attr-defined]
    speechPkg.languageHandling = speechLangHandlingMod  # type: ignore[attr-defined]
    speechPkg.manager = speechManagerMod  # type: ignore[attr-defined]

    speechDictHandlerMod = ModuleType("speechDictHandler")
    speechDictDefsMod = ModuleType("speechDictHandler.definitions")
    speechDictHandlerMod.definitions = speechDictDefsMod  # type: ignore[attr-defined]
    speechDictHandlerMod.loadVoiceDict = lambda synth: None  # type: ignore[attr-defined]
    speechDictHandlerMod.dictionaries = {}  # type: ignore[attr-defined]
    speechDictDefsMod.loadVoiceDict = speechDictHandlerMod.loadVoiceDict  # type: ignore[attr-defined]

    synthDriverHandlerMod = ModuleType("synthDriverHandler")
    synthDriverHandlerMod.SynthDriver = _SynthDriver  # type: ignore[attr-defined]
    synthDriverHandlerMod.VoiceInfo = _VoiceInfo  # type: ignore[attr-defined]
    synthDriverHandlerMod.synthIndexReached = _FakeExtensionPoint()  # type: ignore[attr-defined]
    synthDriverHandlerMod.synthDoneSpeaking = _FakeExtensionPoint()  # type: ignore[attr-defined]
    synthDriverHandlerMod.getSynth = lambda: None  # type: ignore[attr-defined]
    synthDriverHandlerMod.setSynth = lambda *a, **k: True  # type: ignore[attr-defined]
    synthDriverHandlerMod.findAndSetNextSynth = lambda *a, **k: True  # type: ignore[attr-defined]

    autoSettingsPkg = ModuleType("autoSettingsUtils")
    autoSettingsPkg.__path__ = []  # type: ignore[attr-defined]
    autoDriverSettingMod = ModuleType("autoSettingsUtils.driverSetting")
    autoDriverSettingMod.DriverSetting = _DriverSetting  # type: ignore[attr-defined]
    autoDriverSettingMod.BooleanDriverSetting = _BooleanDriverSetting  # type: ignore[attr-defined]
    autoDriverSettingMod.NumericDriverSetting = _NumericDriverSetting  # type: ignore[attr-defined]
    autoUtilsMod = ModuleType("autoSettingsUtils.utils")
    autoUtilsMod.StringParameterInfo = _StringParameterInfo  # type: ignore[attr-defined]
    autoSettingsPkg.driverSetting = autoDriverSettingMod  # type: ignore[attr-defined]
    autoSettingsPkg.utils = autoUtilsMod  # type: ignore[attr-defined]

    uiMod = ModuleType("ui")
    uiMod.message = lambda *a, **k: None  # type: ignore[attr-defined]

    return {
        "addonHandler": addonHandlerMod,
        "config": configMod,
        "globalPluginHandler": globalPluginHandlerMod,
        "globalVars": globalVarsMod,
        "languageHandler": languageHandlerMod,
        "logHandler": logHandlerMod,
        "nvwave": nvwaveMod,
        "wx": wxMod,
        "gui": guiMod,
        "gui.guiHelper": guiHelperMod,
        "gui.nvdaControls": nvdaControlsMod,
        "gui.settingsDialogs": settingsDialogsMod,
        "speech": speechPkg,
        "speech.extensions": speechExtensionsMod,
        "speech.shortcutKeys": speechShortcutKeysMod,
        "speech.speech": speechInnerMod,
        "speech.commands": speechCommandsMod,
        "speech.sayAll": speechSayAllMod,
        "speech.languageHandling": speechLangHandlingMod,
        "speech.manager": speechManagerMod,
        "speechDictHandler": speechDictHandlerMod,
        "speechDictHandler.definitions": speechDictDefsMod,
        "synthDriverHandler": synthDriverHandlerMod,
        "autoSettingsUtils": autoSettingsPkg,
        "autoSettingsUtils.driverSetting": autoDriverSettingMod,
        "autoSettingsUtils.utils": autoUtilsMod,
        "ui": uiMod,
    }


def _load_addon_nvda_modules() -> tuple[ModuleType, ModuleType, dict[str, ModuleType], dict[str, ModuleType]]:
    """Import real synthDrivers.googleTtsForNvda and globalPlugins.googleTtsForNvda under stubs."""
    stubs = _make_stub_nvda_modules()
    savedModules = dict(sys.modules)
    savedPath = list(sys.path)
    hadUnderscore = hasattr(builtins, "_")
    savedUnderscore = getattr(builtins, "_", None)

    try:
        sys.modules.update(stubs)
        addonStr = str(ADDON_ROOT)
        if addonStr not in sys.path:
            sys.path.insert(0, addonStr)

        for modName in list(sys.modules):
            if modName == "synthDrivers" or modName.startswith("synthDrivers."):
                sys.modules.pop(modName, None)
            if modName == "globalPlugins" or modName.startswith("globalPlugins."):
                sys.modules.pop(modName, None)

        def _load_pkg_init(pkgName: str, initPath: Path) -> ModuleType:
            spec = importlib.util.spec_from_file_location(
                pkgName,
                initPath,
                submodule_search_locations=[str(initPath.parent)],
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load {initPath}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[pkgName] = mod
            spec.loader.exec_module(mod)
            return mod

        synthPkgRoot = ModuleType("synthDrivers")
        synthPkgRoot.__path__ = [str(ADDON_ROOT / "synthDrivers")]  # type: ignore[attr-defined]
        sys.modules["synthDrivers"] = synthPkgRoot

        globalPkgRoot = ModuleType("globalPlugins")
        globalPkgRoot.__path__ = [str(ADDON_ROOT / "globalPlugins")]  # type: ignore[attr-defined]
        sys.modules["globalPlugins"] = globalPkgRoot

        synthMod = _load_pkg_init(
            "synthDrivers.googleTtsForNvda",
            ADDON_ROOT / "synthDrivers" / "googleTtsForNvda" / "__init__.py",
        )
        pluginMod = _load_pkg_init(
            "globalPlugins.googleTtsForNvda",
            ADDON_ROOT / "globalPlugins" / "googleTtsForNvda" / "__init__.py",
        )
        addonModules = {
            modName: mod
            for modName, mod in sys.modules.items()
            if modName == "synthDrivers"
            or modName.startswith("synthDrivers.")
            or modName == "globalPlugins"
            or modName.startswith("globalPlugins.")
        }
        return synthMod, pluginMod, stubs, addonModules
    finally:
        sys.modules.clear()
        sys.modules.update(savedModules)
        sys.path[:] = savedPath
        if hadUnderscore:
            builtins._ = savedUnderscore  # type: ignore[attr-defined]
        else:
            builtins.__dict__.pop("_", None)


class RealNvdaIntegrationShimTests(unittest.TestCase):
    """Verify real production functions in synthDrivers/__init__.py and globalPlugins/__init__.py."""

    synthMod: ModuleType
    pluginMod: ModuleType
    stubs: dict[str, ModuleType]
    _addonModules: dict[str, ModuleType]
    _sysModulesPatcher: Any
    _hadUnderscore: bool
    _savedUnderscore: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls._hadUnderscore = hasattr(builtins, "_")
        cls._savedUnderscore = getattr(builtins, "_", None)
        cls.synthMod, cls.pluginMod, cls.stubs, cls._addonModules = _load_addon_nvda_modules()
        if not hasattr(builtins, "_"):
            builtins._ = lambda text: text  # type: ignore[attr-defined]

    def setUp(self) -> None:
        self._sysModulesPatcher = mock.patch.dict(sys.modules, self._addonModules)
        self._sysModulesPatcher.start()

    def tearDown(self) -> None:
        self._sysModulesPatcher.stop()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._hadUnderscore:
            builtins._ = cls._savedUnderscore  # type: ignore[attr-defined]
        else:
            builtins.__dict__.pop("_", None)

    @staticmethod
    def _default_speech_conf(plugin: Any, **extra_speech: Any) -> dict[str, Any]:
        return {
            **extra_speech,
            plugin.SYNTH_NAME: {
                "capPitchChange": 30,
                "sayCapForCapitals": False,
                "beepForCapitals": False,
                "useSpellingFunctionality": True,
            },
        }

    @staticmethod
    def _make_auto_synth(plugin: Any, *, profile: dict[str, Any] | None = None, **attrs: Any) -> Any:
        fields: dict[str, Any] = {
            "name": plugin.SYNTH_NAME,
            "voice": "en-US",
            "_auto_language_detection_enabled": lambda: True,
            "_auto_language_candidates": lambda: ["en-US"],
            "_auto_language_preferred": lambda candidates, fallback: "en-US",
            "_auto_language_candidate_for_language": lambda lang, candidates: "en-US",
            "_auto_language_profile_for_language": lambda lang: dict(profile or {}),
            "_language_root": lambda lang: str(lang or "").split("-", 1)[0].lower(),
            "_language_matches": lambda a, b: a == b,
        }
        fields.update(attrs)
        return SimpleNamespace(**fields)

    @staticmethod
    def _make_speaker(voice_id: str, name: str, language: str, package_id: str) -> Any:
        return SimpleNamespace(
            id=voice_id,
            speaker=voice_id.split(":", 1)[-1],
            name=name,
            language=language,
            packageId=package_id,
        )

    @staticmethod
    def _make_fake_player(**overrides: Any) -> Any:
        defaults: dict[str, Any] = {
            "feed": lambda _pcm: None,
            "sync": lambda: None,
            "idle": lambda: None,
            "pause": lambda _sw: None,
            "stop": lambda: None,
            "close": lambda: None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @contextlib.contextmanager
    def _patched_conf(self, **sections: Any) -> Iterator[Any]:
        config = self.stubs["config"]
        sentinel = object()
        saved = {key: config.conf.get(key, sentinel) for key in sections}
        try:
            for key, value in sections.items():
                if value is None:
                    config.conf.pop(key, None)
                else:
                    config.conf[key] = value
            yield config.conf
        finally:
            for key, old_value in saved.items():
                if old_value is sentinel:
                    config.conf.pop(key, None)
                else:
                    config.conf[key] = old_value

    def _make_voice_package(
        self,
        pkg_id: str,
        *,
        file_id: str = "fr",
        url: str | None = None,
        sha: str = "0" * 64,
        compressed_size: int = 100,
        speakers: tuple[dict[str, str], ...] = (),
        dependent_voice_id: str | None = None,
    ) -> Any:
        return self.synthMod.catalog.VoicePackage(
            id=pkg_id,
            fileId=file_id,
            url=url or f"https://example.invalid/{file_id}.zvoice",
            sha256Checksum=sha,
            compressedSize=compressed_size,
            remote=True,
            speakers=speakers,
            dependentVoiceId=dependent_voice_id,
        )

    def _make_update_check_fixture(self, mode: Any, *, version: str = "1.1") -> tuple[Any, Any, Any]:
        guiMod = self.stubs["gui"]
        updaterMod = self.pluginMod.updater
        updateGuiMod = self.pluginMod.updateGui
        controller = updateGuiMod._UpdateCheckController(mode, guiMod.mainFrame)
        fakeUpdate = updaterMod.UpdateInfo(
            version=version,
            url="https://example.invalid/addon.nvda-addon",
            size=10,
            sha256="0" * 64,
            minimumNVDAVersion="2024.1",
            lastTestedNVDAVersion="2026.2",
            releaseNotes="",
        )
        checkResult = updaterMod.UpdateCheckResult(
            currentVersion="1.0",
            update=fakeUpdate,
            available=True,
            manifestPath=Path("fake-manifest.json"),
        )
        return controller, fakeUpdate, checkResult

    def _make_real_driver(
        self,
        *,
        speakersByLanguage: dict[str, list[Any]] | None = None,
        bridgeSpeak: Any = None,
        player: Any = None,
        pauseMode: str | None = None,
        rate: int = 50,
    ) -> Any:
        driver = self.synthMod.SynthDriver.__new__(self.synthMod.SynthDriver)
        if speakersByLanguage is None:
            speakersByLanguage = {
                "en-US": [self._make_speaker("en1", "English 1", "en-US", "pkg-en")],
                "vi-VN": [self._make_speaker("vi1", "Vietnamese 1", "vi-VN", "pkg-vi")],
            }
        allSpeakers = [sp for langSpeakers in speakersByLanguage.values() for sp in langSpeakers]
        driver._speakersByLanguage = speakersByLanguage

        def _speaker_for_voice(voiceId: str) -> Any:
            for speaker in allSpeakers:
                if speaker.id == voiceId:
                    return speaker
            raise KeyError(voiceId)

        def _package_for_voice(voiceId: str) -> Any:
            speaker = _speaker_for_voice(voiceId)
            return SimpleNamespace(id=speaker.packageId, dependentVoiceId=None)

        def _language_for_voice(voiceId: str) -> str:
            return str(_speaker_for_voice(voiceId).language)

        driver.catalog = SimpleNamespace(
            speakers=allSpeakers,
            speaker_for_voice=_speaker_for_voice,
            package_for_voice=_package_for_voice,
            language_for_voice=_language_for_voice,
        )
        driver._speakersByPackage = driver._build_speakers_by_package()
        driver._speakerVoiceInfos = driver._build_speaker_voice_infos()
        driver._variantsByLanguage = {}
        driver._availableVoices = driver._build_available_voices()
        driver._store_available_languages(set(speakersByLanguage))
        firstVoice = next(iter(driver._availableVoices), "")
        driver._SynthDriver__voice = firstVoice
        driver._availableVariants = driver._build_available_variants(firstVoice)
        driver._SynthDriver__variant = next(iter(driver._availableVariants), "")
        driver._rate = rate
        driver._rateBoost = False
        driver._pitch = 50
        driver._volume = 100
        driver._pauseMode = pauseMode if pauseMode is not None else self.synthMod._PAUSE_MODE_DO_NOT_SHORTEN
        driver._isPaused = False
        driver._playerOutputDevice = "default"
        driver._player = player or self._make_fake_player()
        driver._bridge = SimpleNamespace(
            speak=bridgeSpeak or (lambda *a, **k: {"success": True, "done": True, "segmentEnds": 0}),
            preload_voice=lambda *a, **k: None,
            ensure_connection=lambda *a, **k: None,
            cancel_current=lambda: None,
            terminate=lambda: None,
            maybe_recycle_runtime=lambda **k: False,
            safe_for_standby_release=lambda: True,
        )
        driver._speechCondition = threading.Condition()
        driver._speechQueue = deque()
        driver._activeCancelEvent = None
        driver._shutdownEvent = threading.Event()
        driver._fallbackTriggered = False
        driver._consecutiveRuntimeErrors = 0
        driver._cacheLock = threading.RLock()
        driver._shortAudioCache = OrderedDict()
        driver._shortAudioCacheBytes = 0
        driver._shortAudioCacheHits = 0
        driver._shortAudioCacheMisses = 0
        driver._shortAudioCacheEvictions = 0
        driver._audioChunksSinceDeviceCheck = 0
        driver._warmupThread = None
        driver._warmupCancelEvent = threading.Event()
        return driver

    @contextlib.contextmanager
    def _auto_profile_env(
        self,
        *,
        synth: Any = None,
        speechConf: dict[str, Any] | None = None,
        pluginConf: dict[str, Any] | None = None,
        speakFn: Any = None,
        processTextFn: Any = None,
        getSpellingSpeechFn: Any = None,
        getSingleCharDescriptionFn: Any = None,
        shouldUseSpellingFunctionalityFn: Any = None,
        sayAllSpeakFn: Any = None,
        speakWithoutPausesFn: Any = None,
        shouldMakeLangChangeCommandFn: Any = None,
        shouldSwitchVoiceFn: Any = None,
        managerShouldSwitchVoiceFn: Any = None,
        patchHooks: bool = True,
    ) -> Iterator[None]:
        plugin = self.pluginMod
        config = self.stubs["config"]
        speechPkg = self.stubs["speech"]
        speechModule = self.stubs["speech.speech"]
        shortcutKeys = self.stubs["speech.shortcutKeys"]
        sayAll = self.stubs["speech.sayAll"]
        langHandling = self.stubs["speech.languageHandling"]
        speechManager = self.stubs["speech.manager"]
        synthDriverHandler = self.stubs["synthDriverHandler"]

        originalConfSpeech = config.conf.get("speech")
        originalConfSection = config.conf.get(plugin.CONFIG_SECTION)
        originalGetSynth = synthDriverHandler.getSynth
        originalPkgSpeak = speechPkg.speak
        originalPkgProcessText = speechPkg.processText
        originalPkgSpelling = speechPkg.getSpellingSpeech
        originalPkgSingleChar = getattr(speechPkg, "getSingleCharDescription", None)
        originalSpeak = speechModule.speak
        originalProcessText = speechModule.processText
        originalGetSpelling = speechModule.getSpellingSpeech
        originalGetSingleChar = speechModule.getSingleCharDescription
        originalShouldUseSpelling = shortcutKeys.shouldUseSpellingFunctionality
        sayAllInst = sayAll.SayAllHandler.speechWithoutPausesInstance
        hadSayAllSpeak = hasattr(sayAllInst, "speak")
        originalSayAllSpeak = getattr(sayAllInst, "speak", None)
        hadSayAllWithoutPauses = hasattr(sayAllInst, "speakWithoutPauses")
        originalSayAllWithoutPauses = getattr(sayAllInst, "speakWithoutPauses", None)
        originalShouldMakeLang = langHandling.shouldMakeLangChangeCommand
        originalShouldSwitchVoice = langHandling.shouldSwitchVoice
        originalMgrShouldSwitchVoice = speechManager.shouldSwitchVoice
        try:
            if speechConf is not None:
                config.conf["speech"] = speechConf
            if pluginConf is not None:
                config.conf[plugin.CONFIG_SECTION] = pluginConf
            if synth is not None:
                synthDriverHandler.getSynth = lambda: synth
            if speakFn is not None:
                speechModule.speak = speakFn
                speechPkg.speak = speakFn
            if processTextFn is not None:
                speechModule.processText = processTextFn
                speechPkg.processText = processTextFn
            if getSpellingSpeechFn is not None:
                speechModule.getSpellingSpeech = getSpellingSpeechFn
                speechPkg.getSpellingSpeech = getSpellingSpeechFn
            if getSingleCharDescriptionFn is not None:
                speechModule.getSingleCharDescription = getSingleCharDescriptionFn
                speechPkg.getSingleCharDescription = getSingleCharDescriptionFn
            if shouldUseSpellingFunctionalityFn is not None:
                shortcutKeys.shouldUseSpellingFunctionality = shouldUseSpellingFunctionalityFn
            if sayAllSpeakFn is not None:
                sayAllInst.speak = sayAllSpeakFn
            if speakWithoutPausesFn is not None:
                sayAllInst.speakWithoutPauses = speakWithoutPausesFn
            if shouldMakeLangChangeCommandFn is not None:
                langHandling.shouldMakeLangChangeCommand = shouldMakeLangChangeCommandFn
            if shouldSwitchVoiceFn is not None:
                langHandling.shouldSwitchVoice = shouldSwitchVoiceFn
            if managerShouldSwitchVoiceFn is not None:
                speechManager.shouldSwitchVoice = managerShouldSwitchVoiceFn
            if patchHooks:
                plugin._patch_auto_language_voice_dictionary()
            yield
        finally:
            if patchHooks:
                plugin._unpatch_auto_language_voice_dictionary()
            plugin._autoProfileSpeakDepth = 0
            if originalConfSpeech is None:
                config.conf.pop("speech", None)
            else:
                config.conf["speech"] = originalConfSpeech
            if originalConfSection is None:
                config.conf.pop(plugin.CONFIG_SECTION, None)
            else:
                config.conf[plugin.CONFIG_SECTION] = originalConfSection
            synthDriverHandler.getSynth = originalGetSynth
            speechPkg.speak = originalPkgSpeak
            speechPkg.processText = originalPkgProcessText
            speechPkg.getSpellingSpeech = originalPkgSpelling
            if originalPkgSingleChar is not None:
                speechPkg.getSingleCharDescription = originalPkgSingleChar
            speechModule.speak = originalSpeak
            speechModule.processText = originalProcessText
            speechModule.getSpellingSpeech = originalGetSpelling
            speechModule.getSingleCharDescription = originalGetSingleChar
            shortcutKeys.shouldUseSpellingFunctionality = originalShouldUseSpelling
            if hadSayAllSpeak:
                sayAllInst.speak = originalSayAllSpeak
            elif hasattr(sayAllInst, "speak"):
                delattr(sayAllInst, "speak")
            if hadSayAllWithoutPauses:
                sayAllInst.speakWithoutPauses = originalSayAllWithoutPauses
            elif hasattr(sayAllInst, "speakWithoutPauses"):
                delattr(sayAllInst, "speakWithoutPauses")
            langHandling.shouldMakeLangChangeCommand = originalShouldMakeLang
            langHandling.shouldSwitchVoice = originalShouldSwitchVoice
            speechManager.shouldSwitchVoice = originalMgrShouldSwitchVoice

    def test_synth_driver_set_voice_reloads_voice_dict_and_immediate_audio_error_recovery(self) -> None:
        driver = self.synthMod.SynthDriver.__new__(self.synthMod.SynthDriver)
        driver._SynthDriver__voice = "en-US"
        driver._SynthDriver__variant = "en-a"
        driver._availableVoices = {
            "en-US": self.stubs["synthDriverHandler"].VoiceInfo("en-US", "English", "en-US"),
            "vi-VN": self.stubs["synthDriverHandler"].VoiceInfo("vi-VN", "Vietnamese", "vi-VN"),
        }
        driver._availableVariants = {"en-a": self.stubs["synthDriverHandler"].VoiceInfo("en-a", "A", "en-US")}
        driver._build_available_variants = lambda voice: {
            f"{voice}-1": self.stubs["synthDriverHandler"].VoiceInfo(f"{voice}-1", f"{voice} 1", voice)
        }
        reloadCalls: list[bool] = []
        warmCalls: list[float] = []
        driver._reload_voice_dict = lambda: reloadCalls.append(True)
        driver._warm_current_voice_async = lambda delay=0: warmCalls.append(delay)

        driver._set_voice("vi-VN")
        self.assertEqual([True], reloadCalls)
        self.assertEqual("vi-VN-1", driver._SynthDriver__variant)
        self.assertEqual(1, len(warmCalls))

        # Immediate audio device error recovery in _feed_audio and _feed_silence (before 50 chunks)
        recreateCalls: list[str] = []
        fedPayloads: list[bytes] = []
        fakePlayer = SimpleNamespace(feed=lambda data: fedPayloads.append(data))
        driver._player = fakePlayer
        driver._audioChunksSinceDeviceCheck = 1
        driver._ensure_current_output_device = lambda: None
        driver._audio_device_error = lambda: True
        driver._recreate_wave_player = lambda: recreateCalls.append("recreated")

        driver._feed_audio(b"\x01\x02")
        self.assertEqual(["recreated"], recreateCalls)
        self.assertEqual(0, driver._audioChunksSinceDeviceCheck)
        self.assertEqual([b"\x01\x02"], fedPayloads)

        recreateCalls.clear()
        driver._audioChunksSinceDeviceCheck = 2
        driver._feed_silence(10)
        self.assertEqual(["recreated"], recreateCalls)
        self.assertEqual(0, driver._audioChunksSinceDeviceCheck)
        self.assertEqual(2, len(fedPayloads))

    def test_hide_google_tts_auto_profile_controls_never_hides_other_synth_and_restores_on_switch(self) -> None:
        plugin = self.pluginMod
        config = self.stubs["config"]
        synthDriverHandler = self.stubs["synthDriverHandler"]
        originalGetSynth = synthDriverHandler.getSynth
        config.conf[plugin.CONFIG_SECTION] = {plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True}
        activeGoogleSynth = SimpleNamespace(
            name=plugin.SYNTH_NAME,
            _auto_language_detection_enabled=lambda: True,
        )
        synthDriverHandler.getSynth = lambda: activeGoogleSynth
        try:

            class _FakeControl:
                def __init__(self, sizer: Any = None) -> None:
                    self.shown = True
                    self.enabled = True
                    self._sizer = sizer

                def GetContainingSizer(self) -> Any:
                    return self._sizer

                def Hide(self) -> None:
                    self.shown = False

                def Show(self) -> None:
                    self.shown = True

                def Enable(self, val: bool = True) -> None:
                    self.enabled = bool(val)

                def Disable(self) -> None:
                    self.enabled = False

            class _FakeSizer:
                def __init__(self, control: _FakeControl) -> None:
                    self.control = control

                def ShowItems(self, show: bool) -> None:
                    self.control.shown = bool(show)

            capControl = _FakeControl()
            capControl._sizer = _FakeSizer(capControl)
            panel = SimpleNamespace(
                driver=SimpleNamespace(name="otherSynth"),
                capPitchChangeEdit=capControl,
                sayCapForCapsCheckBox=_FakeControl(),
                beepForCapsCheckBox=_FakeControl(),
                useSpellingFunctionalityCheckBox=_FakeControl(),
                Layout=lambda: None,
                GetParent=lambda: None,
            )

            # Opening settings for another synth with Google auto-language enabled must leave controls visible/enabled.
            plugin._hide_google_tts_auto_profile_speech_controls(panel)
            self.assertTrue(panel.capPitchChangeEdit.shown)
            self.assertTrue(panel.capPitchChangeEdit.enabled)
            self.assertTrue(panel.sayCapForCapsCheckBox.shown)
            self.assertTrue(panel.useSpellingFunctionalityCheckBox.enabled)

            # Switching panel to Google TTS hides & disables all 4 controls.
            panel.driver = activeGoogleSynth
            plugin._hide_google_tts_auto_profile_speech_controls(panel)
            self.assertFalse(panel.capPitchChangeEdit.shown)
            self.assertFalse(panel.capPitchChangeEdit.enabled)
            self.assertFalse(panel.sayCapForCapsCheckBox.shown)
            self.assertFalse(panel.beepForCapsCheckBox.enabled)
            self.assertFalse(panel.useSpellingFunctionalityCheckBox.shown)

            # Switching panel back to another synth restores & re-enables all 4 controls.
            panel.driver = SimpleNamespace(name="otherSynth")
            plugin._hide_google_tts_auto_profile_speech_controls(panel)
            self.assertTrue(panel.capPitchChangeEdit.shown)
            self.assertTrue(panel.capPitchChangeEdit.enabled)
            self.assertTrue(panel.sayCapForCapsCheckBox.shown)
            self.assertTrue(panel.beepForCapsCheckBox.enabled)
            self.assertTrue(panel.useSpellingFunctionalityCheckBox.shown)
        finally:
            synthDriverHandler.getSynth = originalGetSynth

    def test_on_discard_unbinds_choice_controls_from_sizer_dict(self) -> None:
        plugin = self.pluginMod
        plugin._patch_read_only_text_setting()
        try:
            autoMixin = self.stubs["gui.settingsDialogs"].AutoSettingsMixin

            class _ChoiceControl:
                def __init__(self) -> None:
                    self.unbound: list[Any] = []

                def Unbind(self, event: Any) -> None:
                    self.unbound.append(event)

            variantCtrl = _ChoiceControl()
            extraChoiceCtrl = _ChoiceControl()
            loadCalls: list[bool] = []
            # Extra choice exists in sizerDict (previously visible) even though supportedSettings omits it now.
            panel = SimpleNamespace(
                getSettings=lambda: SimpleNamespace(
                    name=plugin.SYNTH_NAME,
                    supportedSettings=(
                        self.stubs["autoSettingsUtils.driverSetting"].DriverSetting("variant", "Variant"),
                    ),
                    loadSettings=lambda: loadCalls.append(True),
                ),
                sizerDict={"variant": object(), "extraChoice": object()},
                variantList=variantCtrl,
                extraChoiceList=extraChoiceCtrl,
            )
            autoMixin.onDiscard(panel)
            self.assertEqual(["EVT_CHOICE"], variantCtrl.unbound)
            self.assertEqual(["EVT_CHOICE"], extraChoiceCtrl.unbound)
            self.assertEqual([True], loadCalls)

            # refreshGui hides unsupported sizers and calls updateDriverSettings() on live Google TTS settings.
            hiddenSizers: list[str] = []
            updatedDriverSettings: list[bool] = []
            refreshPanel = SimpleNamespace(
                _currentSettingsRef=lambda: True,
                getSettings=lambda: SimpleNamespace(
                    name=plugin.SYNTH_NAME,
                    supportedSettings=(
                        self.stubs["autoSettingsUtils.driverSetting"].DriverSetting("variant", "Variant"),
                    ),
                ),
                sizerDict={"variant": "sizer-variant", "rate": "sizer-rate"},
                settingsSizer=SimpleNamespace(Hide=lambda sizer: hiddenSizers.append(sizer)),
                updateDriverSettings=lambda: updatedDriverSettings.append(True),
                Layout=lambda: None,
                GetParent=lambda: None,
            )
            autoMixin.refreshGui(refreshPanel)
            self.assertEqual(["sizer-rate"], hiddenSizers)
            self.assertEqual([True], updatedDriverSettings)
        finally:
            plugin._unpatch_read_only_text_setting()

    def test_voice_dict_variant_resets_when_switching_synth_and_syncs_nvda_2026_def_path(self) -> None:
        plugin = self.pluginMod
        speechDictHandler = self.stubs["speechDictHandler"]
        with tempfile.TemporaryDirectory() as tmpdir:
            dic1 = str(Path(tmpdir) / "g1.dic")
            dic2 = str(Path(tmpdir) / "g2.dic")
            otherDic = str(Path(tmpdir) / "other.dic")
            Path(dic1).write_text("a\tb\t0\t0\n", encoding="utf-8")
            Path(dic2).write_text("c\td\t0\t0\n", encoding="utf-8")
            Path(otherDic).write_text("e\tf\t0\t0\n", encoding="utf-8")

            class _FakeVoiceDict(list[Any]):
                def __init__(self) -> None:
                    super().__init__()
                    self.fileName = ""

            voiceDict = _FakeVoiceDict()
            voiceDef = SimpleNamespace(name="voice", source="VOICE", dictionary=voiceDict, path="")
            speechDictHandler.definitions._speechDictDefinitions = [voiceDef]
            speechDictHandler.dictionaries = {"voice": voiceDict}

            loadCalls: list[str] = []
            proxyVariants: list[str | None] = []

            def fake_original_load_voice_dict(synth: Any) -> None:
                voiceId = getattr(synth, "voice", "")
                loadCalls.append(voiceId)
                if getattr(synth, "name", "") == plugin.SYNTH_NAME:
                    proxyVariants.append(getattr(synth, "variant", None))
                if voiceId == "v-g1":
                    voiceDict[:] = [("a", "b")]
                    voiceDict.fileName = dic1
                    voiceDef.path = dic1
                elif voiceId == "v-g2":
                    voiceDict[:] = [("c", "d")]
                    voiceDict.fileName = dic2
                    voiceDef.path = dic2
                else:
                    voiceDict[:] = [("e", "f")]
                    voiceDict.fileName = otherDic
                    voiceDef.path = otherDic

            speechDictHandler.loadVoiceDict = fake_original_load_voice_dict
            speechDictHandler.definitions.loadVoiceDict = fake_original_load_voice_dict
            plugin._voiceDictCache.clear()
            plugin._activeLoadedVoiceDictVariant = None

            googleSynth = SimpleNamespace(
                name=plugin.SYNTH_NAME,
                voice="vi-VN",
                variant="v-g1",
                availableVoices={"v-g1": object(), "v-g2": object()},
                _speaker_voice_infos=lambda: {"v-g1": object(), "v-g2": object()},
            )
            otherSynth = SimpleNamespace(name="otherSynth", voice="en")

            plugin._patch_google_tts_voice_dictionary_loading()
            try:
                # 1. Initial load on Google variant v-g1
                speechDictHandler.loadVoiceDict(googleSynth)
                self.assertEqual("v-g1", plugin._activeLoadedVoiceDictVariant)
                self.assertEqual(dic1, voiceDef.path)

                # 2. Switching to non-Google synth clears _activeLoadedVoiceDictVariant
                speechDictHandler.loadVoiceDict(otherSynth)
                self.assertIsNone(plugin._activeLoadedVoiceDictVariant)
                self.assertEqual(otherDic, voiceDef.path)

                # 3. Switching back to Google variant v-g1 hits cache AND updates both fileName & voiceDef.path
                speechDictHandler.loadVoiceDict(googleSynth)
                self.assertEqual("v-g1", plugin._activeLoadedVoiceDictVariant)
                self.assertEqual(dic1, voiceDict.fileName)
                self.assertEqual(dic1, voiceDef.path)
                self.assertEqual([("a", "b")], list(voiceDict))
                self.assertEqual(["v-g1", "en"], loadCalls)

                # 4. Switching to v-g2 loads from disk once, then alternating 5x stays cached with 0 extra disk reads
                googleSynth.variant = "v-g2"
                speechDictHandler.loadVoiceDict(googleSynth)
                self.assertEqual("v-g2", plugin._activeLoadedVoiceDictVariant)
                self.assertEqual(dic2, voiceDef.path)
                self.assertEqual(["v-g1", "en", "v-g2"], loadCalls)
                for _ in range(5):
                    googleSynth.variant = "v-g1"
                    speechDictHandler.loadVoiceDict(googleSynth)
                    self.assertEqual([("a", "b")], list(voiceDict))
                    self.assertEqual(dic1, voiceDict.fileName)
                    googleSynth.variant = "v-g2"
                    speechDictHandler.loadVoiceDict(googleSynth)
                    self.assertEqual([("c", "d")], list(voiceDict))
                    self.assertEqual(dic2, voiceDict.fileName)
                self.assertEqual(["v-g1", "en", "v-g2"], loadCalls)

                # 5. Modifying dic1 on disk invalidates the cached stat signature and triggers a reload
                Path(dic1).write_text("changed\tval\t0\t0\n", encoding="utf-8")
                googleSynth.variant = "v-g1"
                speechDictHandler.loadVoiceDict(googleSynth)
                self.assertEqual(["v-g1", "en", "v-g2", "v-g1"], loadCalls)
                self.assertEqual(["v-g1", "v-g2", "v-g1"], proxyVariants)

                # 6. Legacy NVDA 2024.4.2 without _speechDictDefinitions resolves dictionaries["voice"] directly
                savedDefs = speechDictHandler.definitions._speechDictDefinitions
                delattr(speechDictHandler.definitions, "_speechDictDefinitions")
                try:
                    resolvedDef, resolvedDict = plugin._resolve_nvda_voice_speech_dict()
                    self.assertIsNone(resolvedDef)
                    self.assertIs(voiceDict, resolvedDict)
                finally:
                    speechDictHandler.definitions._speechDictDefinitions = savedDefs
            finally:
                plugin._unpatch_google_tts_voice_dictionary_loading()

    def test_spelling_overlay_lock_and_config_restored_before_first_yield(self) -> None:
        plugin = self.pluginMod
        config = self.stubs["config"]
        speechModule = self.stubs["speech.speech"]
        speechConf = self._default_speech_conf(plugin)
        pluginConf = {
            plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
            plugin.CONFIG_AUTO_LANGUAGE_PROFILES: '{"en-US": {"enabled": true, "voice": "en1", "capPitchChange": 50}}',
        }
        fakeSynth = self._make_auto_synth(
            plugin,
            profile={"capPitchChange": 55, "sayCapForCapitals": True},
        )

        def fake_spelling(text: str, locale: str | None = None, useCharacterDescriptions: bool = False) -> Any:
            self.assertEqual(55, config.conf["speech"][plugin.SYNTH_NAME]["capPitchChange"])
            yield "item1"
            yield "item2"

        def fake_single_char(text: str, locale: str | None = None) -> Any:
            self.assertEqual(55, config.conf["speech"][plugin.SYNTH_NAME]["capPitchChange"])
            yield "char1"
            yield "char2"

        with self._auto_profile_env(
            synth=fakeSynth,
            speechConf=speechConf,
            pluginConf=pluginConf,
            getSpellingSpeechFn=fake_spelling,
            getSingleCharDescriptionFn=fake_single_char,
        ):
            gen = speechModule.getSpellingSpeech("A", locale="en-US")
            first = next(gen)
            self.assertEqual("item1", first)
            # Even BEFORE advancing generator to completion, config & lock must ALREADY be restored!
            self.assertEqual(30, config.conf["speech"][plugin.SYNTH_NAME]["capPitchChange"])

            self.assertTrue(is_lock_free_from_other_thread(plugin._speechConfigOverlayLock))

            gen2 = speechModule.getSingleCharDescription("A", locale="en-US")
            self.assertEqual("char1", next(gen2))
            self.assertEqual(30, config.conf["speech"][plugin.SYNTH_NAME]["capPitchChange"])
            self.assertTrue(is_lock_free_from_other_thread(plugin._speechConfigOverlayLock))

    def test_synth_driver_available_properties_and_compat_overrides(self) -> None:
        self.assertTrue(self.synthMod.SynthDriver.check("unexpectedArg", futureFlag=True))

        driver = self.synthMod.SynthDriver.__new__(self.synthMod.SynthDriver)
        driver._speakersByLanguage = {
            "en-US": [SimpleNamespace(id="en1", speaker="a", name="English 1", language="en-US", packageId="pkg-en")],
            "vi-VN": [
                SimpleNamespace(id="vi1", speaker="a", name="Vietnamese 1", language="vi-VN", packageId="pkg-vi")
            ],
        }
        driver.catalog = SimpleNamespace(
            speakers=[
                driver._speakersByLanguage["en-US"][0],
                driver._speakersByLanguage["vi-VN"][0],
            ],
            language_for_voice=lambda v: "vi-VN" if v == "vi1" else "en-US",
        )
        driver._variantsByLanguage = {}
        driver._availableVoices = driver._build_available_voices()
        driver._SynthDriver__voice = "en-US"
        driver._SynthDriver__variant = "en1"

        # Base _SynthDriver defines setterless @property availableLanguages; storing must succeed without AttributeError.
        driver._store_available_languages({"en-US", "vi-VN"})
        self.assertEqual({"en-US", "vi-VN"}, driver.availableLanguages)
        self.assertEqual({"en-US", "vi-VN"}, driver._get_availableLanguages("extra", futureKw=1))
        self.assertIn("en-US", driver._getAvailableVoices("extra", futureKw=1))
        self.assertIn("en1", driver._getAvailableVariants("extra", futureKw=1))
        self.assertTrue(driver._get_availableNotices("extra", futureKw=1))
        self.assertIn(self.synthMod._PAUSE_MODE_DO_NOT_SHORTEN, driver._get_availablePausemodes("extra", futureKw=1))
        self.assertTrue(driver.isSupported("voice", extra=1))
        self.assertTrue(driver.isSupported("rate", "extraArg", futureKw=99))
        self.assertFalse(driver.isSupported("unknownSetting", "extraArg", futureKw=99))

        # Test a subclass whose availableLanguages is a normal attribute instead of a setterless property.
        class _PlainAttrDriver(self.synthMod.SynthDriver):
            availableLanguages: Any = set()  # type: ignore[assignment]

        plainDriver = _PlainAttrDriver.__new__(_PlainAttrDriver)
        plainDriver._store_available_languages({"fr-FR"})
        self.assertEqual({"fr-FR"}, plainDriver.availableLanguages)

        # Verify _set_variant reloads voice dict and warms new variant
        reloadCalls: list[bool] = []
        warmCalls: list[float] = []
        driver._reload_voice_dict = lambda: reloadCalls.append(True)
        driver._warm_current_voice_async = lambda delay=0: warmCalls.append(delay)
        driver._set_voice("vi-VN")
        self.assertEqual("vi1", driver._SynthDriver__variant)
        driver._set_variant("vi1")
        self.assertEqual([True, True], reloadCalls)
        self.assertEqual(2, len(warmCalls))

        # Verify supportedSettings, isSupported, languageIsSupported under auto-language ON and OFF
        with self._patched_conf() as conf:
            conf[self.pluginMod.CONFIG_SECTION] = {self.pluginMod.CONFIG_AUTO_LANGUAGE_DETECTION: False}
            self.assertIn("voice", [s.id for s in driver.supportedSettings])
            self.assertIn("rate", [s.id for s in driver.supportedSettings])
            self.assertTrue(driver.isSupported("voice"))
            self.assertFalse(driver.isSupported("notice"))
            self.assertTrue(driver.languageIsSupported(None))
            self.assertTrue(driver.languageIsSupported("en-US"))
            self.assertFalse(driver.languageIsSupported("fr-FR"))

            conf[self.pluginMod.CONFIG_SECTION] = {
                self.pluginMod.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                self.pluginMod.CONFIG_AUTO_LANGUAGE_PROFILES: '{"vi-VN": {"enabled": true, "voice": "vi1"}}',
            }
            self.assertEqual(["notice", "pauseMode"], [s.id for s in driver.supportedSettings])
            self.assertTrue(driver.isSupported("voice"))
            self.assertTrue(driver.isSupported("notice"))
            self.assertTrue(driver.isSupported("pauseMode"))
            self.assertFalse(driver.isSupported("rate"))
            self.assertFalse(driver.isSupported("pitch"))
            self.assertTrue(driver.languageIsSupported(None))
            self.assertTrue(driver.languageIsSupported("vi-VN"))
            self.assertFalse(driver.languageIsSupported("en-US"))

            # Verify real _ensure_config_compat injects missing defaults without clobbering custom values
            conf["speech"] = {driver.name: {"voice": "en-US", "variant": "en1", "rate": 40, "pitch": 50, "volume": 100}}
            driver._ensure_config_compat()
            self.assertFalse(conf["speech"][driver.name]["rateBoost"])
            self.assertEqual(self.synthMod._PAUSE_MODE_DO_NOT_SHORTEN, conf["speech"][driver.name]["pauseMode"])
            self.assertEqual(40, conf["speech"][driver.name]["rate"])

            conf["speech"] = {
                driver.name: {
                    "voice": "en-US",
                    "variant": "en1",
                    "rate": 70,
                    "rateBoost": True,
                    "pitch": 60,
                    "volume": 80,
                    "pauseMode": self.synthMod._PAUSE_MODE_SHORTEN_ALL,
                }
            }
            driver._ensure_config_compat()
            self.assertTrue(conf["speech"][driver.name]["rateBoost"])
            self.assertEqual(self.synthMod._PAUSE_MODE_SHORTEN_ALL, conf["speech"][driver.name]["pauseMode"])
            self.assertEqual(70, conf["speech"][driver.name]["rate"])

        # Verify loadSettings compatibility with both zero-arg and single-arg base SynthDriver.loadSettings.
        baseCls = self.stubs["synthDriverHandler"].SynthDriver
        originalBaseLoad = baseCls.loadSettings
        calls: list[Any] = []
        try:

            def load_zero_arg(self_arg: Any) -> None:
                calls.append(("zero", self_arg is driver))

            baseCls.loadSettings = load_zero_arg
            driver.loadSettings(True, "extra", futureKw=1)
            self.assertEqual([("zero", True)], calls)

            calls.clear()

            def load_one_arg(self_arg: Any, onlyChanged: bool = False) -> None:
                calls.append(("one", onlyChanged))

            baseCls.loadSettings = load_one_arg
            driver.loadSettings(onlyChanged=True, futureKw=1)
            self.assertEqual([("one", True)], calls)
        finally:
            baseCls.loadSettings = originalBaseLoad

    def test_schedule_ui_callback_retains_strong_ref_and_complies_with_handler_registrar(self) -> None:
        wxMod = self.stubs["wx"]
        originalGetApp = wxMod.GetApp
        wxMod.GetApp = lambda: None

        fakeCore = ModuleType("core")
        fakeCore.postNvdaStartup = _FakeHandlerRegistrar(weak=True)  # type: ignore[attr-defined]
        received: list[tuple[Any, ...]] = []
        self.synthMod._PENDING_POST_STARTUP_HANDLERS.clear()
        try:
            with mock.patch.dict(sys.modules, {"core": fakeCore}):
                self.synthMod.SynthDriver._schedule_ui_callback(
                    lambda *a, **k: received.append((a, k)),
                    "hello",
                    flag=True,
                )
            gc.collect()
            gc.collect()
            self.assertEqual(1, len(self.synthMod._PENDING_POST_STARTUP_HANDLERS))
            self.assertEqual(1, len(fakeCore.postNvdaStartup._handlers))  # type: ignore[attr-defined]
            self.assertIsNotNone(fakeCore.postNvdaStartup._handlers[0]())  # type: ignore[attr-defined]

            fakeCore.postNvdaStartup.fire()  # type: ignore[attr-defined]
            self.assertEqual([(("hello",), {"flag": True})], received)
            self.assertEqual(0, len(self.synthMod._PENDING_POST_STARTUP_HANDLERS))
        finally:
            self.synthMod._PENDING_POST_STARTUP_HANDLERS.clear()
            wxMod.GetApp = originalGetApp

    def test_create_wave_player_compat_fallback(self) -> None:
        driver = self.synthMod.SynthDriver.__new__(self.synthMod.SynthDriver)
        originalWavePlayer = self.synthMod.WavePlayer
        try:
            modernCalls: list[dict[str, Any]] = []

            class _ModernWavePlayer:
                def __init__(
                    self,
                    channels: int,
                    samplesPerSec: int,
                    bitsPerSample: int,
                    outputDevice: str = "default",
                    wantDucking: bool = True,
                ) -> None:
                    modernCalls.append(
                        {
                            "channels": channels,
                            "samplesPerSec": samplesPerSec,
                            "bitsPerSample": bitsPerSample,
                            "outputDevice": outputDevice,
                        }
                    )

            self.synthMod.WavePlayer = _ModernWavePlayer
            driver._create_wave_player("speaker-1")
            self.assertEqual("speaker-1", modernCalls[0]["outputDevice"])

            legacyCalls: list[tuple[int, int, int]] = []

            class _LegacyWavePlayer:
                def __init__(self, channels: int, samplesPerSec: int, bitsPerSample: int) -> None:
                    legacyCalls.append((channels, samplesPerSec, bitsPerSample))

            self.synthMod.WavePlayer = _LegacyWavePlayer
            driver._create_wave_player("speaker-1")
            self.assertEqual([(1, self.synthMod.SAMPLE_RATE, 16)], legacyCalls)

            strictCalls: list[tuple[int, int, int]] = []

            def strict_positional_factory(channels: int, samplesPerSec: int, bitsPerSample: int, /) -> Any:
                strictCalls.append((channels, samplesPerSec, bitsPerSample))
                return SimpleNamespace()

            self.synthMod.WavePlayer = strict_positional_factory
            driver._create_wave_player("speaker-1")
            self.assertEqual([(1, self.synthMod.SAMPLE_RATE, 16)], strictCalls)

            noKwargCalls: list[tuple[Any, ...]] = []

            def no_kwarg_factory(*args: Any, **kwargs: Any) -> Any:
                if kwargs:
                    raise TypeError("keyword arguments rejected")
                noKwargCalls.append(args)
                return SimpleNamespace()

            self.synthMod.WavePlayer = no_kwarg_factory
            driver._create_wave_player("speaker-1")
            self.assertEqual([(1, self.synthMod.SAMPLE_RATE, 16)], noKwargCalls)
        finally:
            self.synthMod.WavePlayer = originalWavePlayer

    def test_set_synth_wrapper_compat_and_unpatch_restores_metadata(self) -> None:
        plugin = self.pluginMod
        synthDriverHandler = self.stubs["synthDriverHandler"]
        originalSetSynth = synthDriverHandler.setSynth
        originalWrapperName = plugin._set_synth_with_google_tts_voice_prompt.__name__
        try:
            # 1. NVDA 2026+ signature with keyword-only _leftToTry and unknown future kwargs filtered out.
            nvda2026Calls: list[tuple[Any, bool, Any]] = []

            def nvda_2026_set_synth(name: str, isFallback: bool = False, *, _leftToTry: Any = None) -> bool:
                """Set synth 2026 doc."""
                nvda2026Calls.append((name, isFallback, _leftToTry))
                return True

            synthDriverHandler.setSynth = nvda_2026_set_synth
            plugin._patch_synth_selection()
            self.assertIs(nvda_2026_set_synth, getattr(synthDriverHandler.setSynth, "__wrapped__", None))
            self.assertEqual("nvda_2026_set_synth", synthDriverHandler.setSynth.__name__)

            synthDriverHandler.setSynth("otherSynth", True, _leftToTry=["fallbackSynthA"])
            synthDriverHandler.setSynth(synthName="otherSynth", unknownFutureKw="ignored")
            self.assertEqual(
                [
                    ("otherSynth", True, ["fallbackSynthA"]),
                    ("otherSynth", False, None),
                ],
                nvda2026Calls,
            )
            plugin._unpatch_synth_selection()
            self.assertIs(nvda_2026_set_synth, synthDriverHandler.setSynth)
            self.assertEqual(originalWrapperName, plugin._set_synth_with_google_tts_voice_prompt.__name__)
            self.assertFalse(hasattr(plugin._set_synth_with_google_tts_voice_prompt, "__wrapped__"))

            # 2. Legacy NVDA 2024 signature without _leftToTry.
            nvda2024Calls: list[tuple[Any, bool]] = []

            def nvda_2024_set_synth(name: str, isFallback: bool = False) -> bool:
                nvda2024Calls.append((name, isFallback))
                return True

            synthDriverHandler.setSynth = nvda_2024_set_synth
            plugin._patch_synth_selection()
            synthDriverHandler.setSynth("fallbackSynthB", True, _leftToTry=["silentSynth"])
            self.assertEqual([("fallbackSynthB", True)], nvda2024Calls)
            plugin._unpatch_synth_selection()

            # 3. Cooperating foreign *args, **kwargs wrapper must not receive synthesized optional defaults.
            foreignCalls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

            def foreign_wrapper(*args: Any, **kwargs: Any) -> bool:
                foreignCalls.append((args, dict(kwargs)))
                return True

            synthDriverHandler.setSynth = foreign_wrapper
            plugin._patch_synth_selection()
            synthDriverHandler.setSynth("otherSynth")
            synthDriverHandler.setSynth("otherSynth", False, _leftToTry=["fallbackSynthB"])
            self.assertEqual(
                [
                    (("otherSynth",), {}),
                    (("otherSynth",), {"isFallback": False, "_leftToTry": ["fallbackSynthB"]}),
                ],
                foreignCalls,
            )
        finally:
            plugin._unpatch_synth_selection()
            synthDriverHandler.setSynth = originalSetSynth

    def test_auto_profile_wrappers_filter_kwargs_and_preserve_metadata(self) -> None:
        plugin = self.pluginMod
        speechModule = self.stubs["speech.speech"]
        shortcutKeys = self.stubs["speech.shortcutKeys"]
        sayAll = self.stubs["speech.sayAll"]
        charModeCls = self.stubs["speech.commands"].CharacterModeCommand
        fakeSynth = self._make_auto_synth(
            plugin,
            profile={"useSpellingFunctionality": True},
            supportedCommands=set(),  # CharacterModeCommand omitted -> shouldUseSpellingFunctionality must be False!
        )
        speakCalls: list[tuple[Any, Any]] = []

        def nvda_2024_speak(speechSequence: Any, symbolLevel: Any = None) -> None:
            """Original NVDA speak."""
            speakCalls.append((speechSequence, symbolLevel))

        def nvda_process_text(locale: str, text: str) -> str:
            return f"{locale}:{text}"

        def nvda_spelling(text: str, locale: str | None = None, useCharacterDescriptions: bool = False) -> Any:
            yield f"{locale}:{text}:{useCharacterDescriptions}"

        def nvda_speak_without_pauses(speechSequence: Any) -> bool:
            return bool(speechSequence)

        self.assertNotEqual(
            "self",
            next(iter(inspect.signature(plugin._filter_auto_language_speech_sequence).parameters)),
        )

        with self._auto_profile_env(
            synth=fakeSynth,
            speechConf=self._default_speech_conf(plugin),
            pluginConf={
                plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                plugin.CONFIG_AUTO_LANGUAGE_PROFILES: '{"en-US": {"enabled": true, "voice": "en1", "useSpellingFunctionality": true}}',
            },
            speakFn=nvda_2024_speak,
            processTextFn=nvda_process_text,
            getSpellingSpeechFn=nvda_spelling,
            speakWithoutPausesFn=nvda_speak_without_pauses,
            shouldUseSpellingFunctionalityFn=lambda text: True,
        ):
            self.assertIs(nvda_2024_speak, getattr(speechModule.speak, "__wrapped__", None))
            self.assertEqual("nvda_2024_speak", speechModule.speak.__name__)
            self.assertIs(nvda_process_text, getattr(speechModule.processText, "__wrapped__", None))
            self.assertIs(nvda_spelling, getattr(speechModule.getSpellingSpeech, "__wrapped__", None))

            # Unsupported NVDA 2026+ kwargs like endsUtterance/futurePriority must be dropped safely.
            speechModule.speak(["hi"], symbolLevel=10, priority=2, endsUtterance=False)
            self.assertEqual([(["hi"], 10)], speakCalls)

            # CharacterModeCommand absent in synth.supportedCommands (NVDA 2027.1-dev rule).
            self.assertFalse(shortcutKeys.shouldUseSpellingFunctionality("A"))
            fakeSynth.supportedCommands = {charModeCls}
            self.assertTrue(shortcutKeys.shouldUseSpellingFunctionality("A"))

            # Omitted optional locale arg in getSpellingSpeech works cleanly without TypeError.
            self.assertEqual(["en:A:False"], list(speechModule.getSpellingSpeech("A")))
            self.assertTrue(sayAll.SayAllHandler.speechWithoutPausesInstance.speakWithoutPauses(["line"]))

        # Verify trailing EndUtteranceCommand remains at the very end of getSpellingSpeech / getSingleCharDescription
        # and speech.getSingleCharDescription is patched on the package level as well.
        endUtteranceCls = self.stubs["speech.commands"].EndUtteranceCommand
        speechPkg = self.stubs["speech"]

        def nvda_spelling_with_end_utterance(
            text: str, locale: str | None = None, useCharacterDescriptions: bool = False
        ) -> Any:
            yield f"{locale}:{text}"
            yield endUtteranceCls()

        def nvda_single_char_desc(char: str, locale: str | None = None) -> Any:
            yield f"desc:{locale}:{char}"
            yield endUtteranceCls()

        with self._auto_profile_env(
            synth=SimpleNamespace(
                **{
                    **fakeSynth.__dict__,
                    "_auto_language_candidates": lambda: ["en-US", "vi-VN"],
                }
            ),
            speechConf=self._default_speech_conf(plugin, autoLanguageSwitching=False),
            pluginConf={
                plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                plugin.CONFIG_AUTO_LANGUAGE_PROFILES: '{"en-US": {"enabled": true, "voice": "en1"}, "vi-VN": {"enabled": true, "voice": "vi1"}}',
            },
            getSpellingSpeechFn=nvda_spelling_with_end_utterance,
            getSingleCharDescriptionFn=nvda_single_char_desc,
        ):
            self.assertIs(speechModule.getSingleCharDescription, speechPkg.getSingleCharDescription)
            spelledItems = list(speechPkg.getSpellingSpeech("A"))
            self.assertIsInstance(spelledItems[-1], endUtteranceCls)
            self.assertIsInstance(spelledItems[-2], plugin._GoogleTtsProfileLangCommand)
            self.assertIsNone(spelledItems[-2].language)

            charDescItems = list(speechPkg.getSingleCharDescription("A"))
            self.assertIsInstance(charDescItems[-1], endUtteranceCls)
            self.assertIsInstance(charDescItems[-2], plugin._GoogleTtsProfileLangCommand)
            self.assertIsNone(charDescItems[-2].language)

            # Verify _google_lang_change_command(None) resets inSpellingSequence in _filter_auto_language_speech_sequence.
            filteredSeq = plugin._filter_auto_language_speech_sequence(
                [
                    plugin._google_lang_change_command("vi-VN", spelling=True),
                    "A",
                    plugin._google_lang_change_command(None),
                    "Hello world",
                ]
            )
            self.assertIn("Hello world", [item for item in filteredSeq if isinstance(item, str)])

        # Verify SynthDriver._feed_silence disables WASAPI leading-silence trimming before feeding silence bytes.
        trimCalls: list[tuple[str, Any]] = []
        fakePlayer = self._make_fake_player(
            startTrimmingLeadingSilence=lambda trim: trimCalls.append(("trim", trim)),
            feed=lambda data: trimCalls.append(("feed", len(data))),
        )
        driver = self._make_real_driver(player=fakePlayer)
        driver._feed_silence(50)
        self.assertEqual("trim", trimCalls[0][0])
        self.assertIs(False, trimCalls[0][1])
        self.assertEqual("feed", trimCalls[1][0])

    def test_global_plugin_terminate_compat_and_post_nvda_startup_compat(self) -> None:
        plugin = self.pluginMod
        basePluginCls = self.stubs["globalPluginHandler"].GlobalPlugin
        startupRegistrar = _FakeHandlerRegistrar(weak=False)
        registeredHandlers = startupRegistrar._handlers
        fakeCore = ModuleType("core")
        fakeCore.postNvdaStartup = startupRegistrar  # type: ignore[attr-defined]
        fakeStandby = SimpleNamespace(
            initialize=lambda: None,
            terminate=lambda: None,
            note_synth_active=lambda: None,
            refresh_async=lambda *a, **k: None,
        )
        fakeUpdater = SimpleNamespace(cleanup_update_files=lambda: None)
        fakeUpdateGui = SimpleNamespace(start_automatic_update_check=lambda: None)
        terminateCalls: list[str] = []
        originalBaseTerminate = basePluginCls.terminate
        try:
            basePluginCls.terminate = lambda self_arg: terminateCalls.append("zero_arg_terminate")  # type: ignore[method-assign]
            with (
                mock.patch.dict(sys.modules, {"core": fakeCore}),
                mock.patch.object(plugin, "standby", fakeStandby),
                mock.patch.object(plugin, "updater", fakeUpdater),
                mock.patch.object(plugin, "updateGui", fakeUpdateGui),
            ):
                gp = None
                try:
                    gp = plugin.GlobalPlugin()
                    self.assertEqual([gp._on_post_nvda_startup], registeredHandlers)
                    gp._on_post_nvda_startup("extraArg", futureFlag=True)
                finally:
                    if gp is not None:
                        gp.terminate("unexpectedArg", futureKw=1)
                    else:
                        plugin._unpatch_synth_selection()
                        plugin._unpatch_read_only_text_setting()
                        plugin._unpatch_voice_dictionary_dialog()
                        plugin._unpatch_google_tts_voice_dictionary_loading()
                        plugin._unregister_auto_language_speech_filter()
                        plugin._unpatch_auto_language_voice_dictionary()
                self.assertEqual(["zero_arg_terminate"], terminateCalls)
                self.assertEqual([], registeredHandlers)
        finally:
            basePluginCls.terminate = originalBaseTerminate  # type: ignore[method-assign]

    def test_settings_panel_compat_and_missing_layout_event_fallback(self) -> None:
        settingsMod = self.pluginMod.settings
        config = self.stubs["config"]
        synthDriverHandler = self.stubs["synthDriverHandler"]
        globalVars = self.stubs["globalVars"]
        panel = settingsMod.GoogleTtsSettingsPanel.__new__(settingsMod.GoogleTtsSettingsPanel)
        focused = []
        panel.runtimeChoice = SimpleNamespace(SetFocus=lambda: focused.append("focused"))
        panel.postInit("extraArg", futureKw=True)
        self.assertEqual(["focused"], focused)

        layoutCalls: list[str] = []
        panel._settingsSizer = SimpleNamespace(Layout=lambda: layoutCalls.append("sizer"))
        panel.Layout = lambda: layoutCalls.append("panel")
        # Removing _sendLayoutUpdatedEvent simulates legacy or stripped SettingsPanel implementations.
        if hasattr(panel, "_sendLayoutUpdatedEvent"):
            delattr(panel, "_sendLayoutUpdatedEvent")
        panel._refresh_settings_layout()
        self.assertEqual(["sizer", "panel"], layoutCalls)

        panel.autoUpdateCheck = SimpleNamespace(GetValue=lambda: True)
        panel.updateFeatureStatusText = SimpleNamespace(
            SetValue=lambda v: None,
            HasFocus=lambda: False,
            SetFocus=lambda: None,
        )
        panel.checkUpdatesButton = SimpleNamespace(
            HasFocus=lambda: False,
            Enable=lambda val: None,
            IsEnabled=lambda: True,
            IsShown=lambda: True,
            SetFocus=lambda: None,
        )
        panel._restoreFocusAfterUpdateCheck = False
        with mock.patch.object(settingsMod, "resize_read_only_text_for_content"):
            panel._refresh_update_controls("unexpectedArg", futureKw=True)

        # Verify _refresh_synth_settings_ring handles both zero-arg and one-arg loadSettings.
        originalGetSynth = synthDriverHandler.getSynth
        originalRing = getattr(globalVars, "settingsRing", None)
        loadCalls: list[Any] = []
        ringCalls: list[Any] = []
        fakeSynth = SimpleNamespace(
            name=self.pluginMod.SYNTH_NAME,
            loadSettings=lambda: loadCalls.append("zero"),
            _reload_voice_dict=lambda: loadCalls.append("reloaded_dict"),
            _warm_current_voice_async=lambda: None,
        )
        synthDriverHandler.getSynth = lambda: fakeSynth
        globalVars.settingsRing = SimpleNamespace(updateSupportedSettings=lambda s: ringCalls.append(s))
        try:
            config.conf[self.pluginMod.CONFIG_SECTION] = {}
            panel._refresh_synth_settings_ring(reloadSpeechSettings=True)
            self.assertEqual(["zero", "reloaded_dict"], loadCalls)
            self.assertEqual([fakeSynth], ringCalls)

            loadCalls.clear()
            fakeSynth.loadSettings = lambda onlyChanged=False: loadCalls.append(("one", onlyChanged))
            panel._refresh_synth_settings_ring(reloadSpeechSettings=True)
            self.assertEqual([("one", True), "reloaded_dict"], loadCalls)
        finally:
            synthDriverHandler.getSynth = originalGetSynth
            globalVars.settingsRing = originalRing

    def test_voice_manager_confirm_dialogs_accept_yes_and_id_yes(self) -> None:
        voiceManagerMod = self.pluginMod.voiceManager
        wxMod = self.stubs["wx"]
        guiMod = self.stubs["gui"]
        originalMsgBox = guiMod.messageBox
        dialog = voiceManagerMod.VoiceManagerDialog.__new__(voiceManagerMod.VoiceManagerDialog)
        dialog._package_list_text = lambda pkgs: "English (US)"
        pkg = SimpleNamespace(id="en-us")
        try:
            with mock.patch.dict(sys.modules, {"gui": guiMod}):
                for returnVal, expected in ((wxMod.YES, True), (wxMod.ID_YES, True), (wxMod.NO, False)):
                    guiMod.messageBox = lambda *a, _rv=returnVal, **k: _rv
                    self.assertEqual(expected, dialog._confirm_remove_last_inactive_voice([pkg]))
                    self.assertEqual(expected, dialog._confirm_remove_last_active_voice([pkg]))
        finally:
            guiMod.messageBox = originalMsgBox

    def test_show_message_box_nvda_2024_through_main_and_thread_marshalling(self) -> None:
        from enum import Enum, IntEnum

        uiUtils = self.pluginMod.uiUtils
        wxMod = self.stubs["wx"]
        guiMod = self.stubs["gui"]
        wxMod.CANCEL_DEFAULT = 256  # type: ignore[attr-defined]
        wxMod.HELP = 32768  # type: ignore[attr-defined]
        wxMod.ID_HELP = 5009  # type: ignore[attr-defined]
        wxMod.ICON_NONE = 0  # type: ignore[attr-defined]

        class _FakeDialogType(Enum):
            STANDARD = 1
            WARNING = 2
            ERROR = 3

        class _FakeReturnCode(IntEnum):
            OK = 5100
            CANCEL = 5101
            YES = 5103
            NO = 5104
            HELP = 5009

        class _ButtonSpec:
            def __init__(self, label: str, defaultFocus: bool = False) -> None:
                self.label = label
                self.defaultFocus = defaultFocus

            def _replace(self, *, defaultFocus: bool = False) -> _ButtonSpec:
                return _ButtonSpec(self.label, defaultFocus=defaultFocus)

        class _FakeDefaultButton:
            OK = _ButtonSpec("OK")
            YES = _ButtonSpec("YES")
            NO = _ButtonSpec("NO")
            CANCEL = _ButtonSpec("CANCEL")
            HELP = _ButtonSpec("HELP")

        dialogCalls: list[dict[str, Any]] = []
        legacyMsgBoxCalls: list[tuple[Any, ...]] = []

        class _FakeMessageDialog:
            modalResult = _FakeReturnCode.YES

            def __init__(self, parent: Any, message: str, *, title: str, dialogType: Any, buttons: Any) -> None:
                dialogCalls.append(
                    {
                        "onMain": wxMod.IsMainThread(),
                        "parent": parent,
                        "message": message,
                        "title": title,
                        "dialogType": dialogType,
                        "buttons": buttons,
                    }
                )

            def ShowModal(self) -> Any:
                return self.modalResult

        originalIsMainThread = wxMod.IsMainThread
        originalGuiMessage = getattr(guiMod, "message", None)
        originalGuiMessageBox = guiMod.messageBox
        originalWxCallOnMainHelper = getattr(guiMod.guiHelper, "wxCallOnMain", None)
        originalMainFrame = guiMod.mainFrame
        fakeCore = ModuleType("core")
        fakeCore._hasShutdownBeenTriggered = False  # type: ignore[attr-defined]
        try:
            with mock.patch.dict(sys.modules, {"gui": guiMod, "core": fakeCore}):
                # 1. Modern NVDA (2025.3+ - main) on main thread with dead preferred parent -> falls back to live mainFrame.
                messageMod = ModuleType("gui.message")
                messageMod.MessageDialog = _FakeMessageDialog  # type: ignore[attr-defined]
                messageMod.DialogType = _FakeDialogType  # type: ignore[attr-defined]
                messageMod.DefaultButton = _FakeDefaultButton  # type: ignore[attr-defined]
                messageMod.ReturnCode = _FakeReturnCode  # type: ignore[attr-defined]

                def _legacy_message_box(*args: Any, **kwargs: Any) -> int:
                    legacyMsgBoxCalls.append(args)
                    return int(wxMod.CANCEL)

                messageMod.messageBox = _legacy_message_box  # type: ignore[attr-defined]
                marshalledViaMessage: list[str] = []

                def _marshal_via_message(fn: Any) -> Any:
                    marshalledViaMessage.append("message")
                    return fn()

                messageMod.wxCallOnMain = _marshal_via_message  # type: ignore[attr-defined]
                guiMod.message = messageMod  # type: ignore[attr-defined]
                guiMod.messageBox = messageMod.messageBox  # type: ignore[attr-defined]

                deadParent = SimpleNamespace(IsBeingDeleted=lambda: True)
                liveMainFrame = SimpleNamespace(IsBeingDeleted=lambda: False, GetId=lambda: 1)
                guiMod.mainFrame = liveMainFrame  # type: ignore[attr-defined]

                wxMod.IsMainThread = lambda: True  # type: ignore[attr-defined]
                res = uiUtils.show_message_box(
                    "Question?",
                    "Caption",
                    wxMod.YES_NO | wxMod.NO_DEFAULT | wxMod.CANCEL | wxMod.HELP | wxMod.ICON_WARNING,
                    parent=deadParent,
                )
                self.assertEqual(wxMod.YES, res)
                self.assertEqual(1, len(dialogCalls))
                self.assertIs(liveMainFrame, dialogCalls[0]["parent"])
                self.assertEqual(_FakeDialogType.WARNING, dialogCalls[0]["dialogType"])
                self.assertTrue(any(b.label == "NO" and b.defaultFocus for b in dialogCalls[0]["buttons"]))
                self.assertTrue(any(b.label == "HELP" for b in dialogCalls[0]["buttons"]))

                # 2. Off-main thread marshals through gui.message.wxCallOnMain.
                wxMod.IsMainThread = lambda: False  # type: ignore[attr-defined]
                _FakeMessageDialog.modalResult = _FakeReturnCode.CANCEL
                res2 = uiUtils.show_message_box("Worker msg", "Caption", wxMod.OK)
                self.assertEqual(wxMod.CANCEL, res2)
                self.assertEqual(["message"], marshalledViaMessage)

                # 3. Off-main thread falls back to gui.guiHelper.wxCallOnMain if gui.message lacks it.
                marshalledViaHelper: list[str] = []
                delattr(messageMod, "wxCallOnMain")

                def _marshal_via_helper(fn: Any) -> Any:
                    marshalledViaHelper.append("helper")
                    return fn()

                guiMod.guiHelper.wxCallOnMain = _marshal_via_helper  # type: ignore[attr-defined]
                _FakeMessageDialog.modalResult = _FakeReturnCode.NO
                res3 = uiUtils.show_message_box("Worker msg 2", "Caption", wxMod.YES_NO)
                self.assertEqual(wxMod.NO, res3)
                self.assertEqual(["helper"], marshalledViaHelper)

                # 4. Off-main thread without any wxCallOnMain falls back to gui.messageBox rather than MessageDialog.
                delattr(guiMod.guiHelper, "wxCallOnMain")
                dialogCalls.clear()
                legacyMsgBoxCalls.clear()
                res4 = uiUtils.show_message_box("No marshal", "Caption", wxMod.OK)
                self.assertEqual(wxMod.CANCEL, res4)
                self.assertEqual([], dialogCalls)
                self.assertEqual(1, len(legacyMsgBoxCalls))

                # 5. Legacy NVDA 2024 without MessageDialog uses gui.messageBox directly.
                wxMod.IsMainThread = lambda: True  # type: ignore[attr-defined]
                delattr(guiMod, "message")
                legacyMsgBoxCalls.clear()
                res5 = uiUtils.show_message_box("Legacy", "Caption", wxMod.OK)
                self.assertEqual(wxMod.CANCEL, res5)
                self.assertEqual(1, len(legacyMsgBoxCalls))

                # 6. Shutdown triggered short-circuits to wx.CANCEL without displaying any dialog.
                fakeCore._hasShutdownBeenTriggered = True  # type: ignore[attr-defined]
                legacyMsgBoxCalls.clear()
                self.assertEqual(wxMod.CANCEL, uiUtils.show_message_box("Shutting down", "Caption", wxMod.OK))
                self.assertEqual([], legacyMsgBoxCalls)

                # 7. synthDrivers._show_message_box fallback obeys the same off-main-thread marshalling & fallback rules.
                fakeCore._hasShutdownBeenTriggered = False  # type: ignore[attr-defined]
                guiMod.message = messageMod  # type: ignore[attr-defined]
                with mock.patch.dict(sys.modules, {"globalPlugins.googleTtsForNvda.uiUtils": None}):
                    wxMod.IsMainThread = lambda: False  # type: ignore[attr-defined]
                    legacyMsgBoxCalls.clear()
                    dialogCalls.clear()
                    self.assertEqual(
                        wxMod.CANCEL, self.synthMod._show_message_box("Driver no marshal", "Cap", wxMod.OK)
                    )
                    self.assertEqual(1, len(legacyMsgBoxCalls))

                    helperCalls: list[str] = []

                    def _driver_marshal_via_helper(fn: Any) -> Any:
                        helperCalls.append("driverHelper")
                        return fn()

                    guiMod.guiHelper.wxCallOnMain = _driver_marshal_via_helper  # type: ignore[attr-defined]
                    _FakeMessageDialog.modalResult = _FakeReturnCode.YES
                    resDriver = self.synthMod._show_message_box(
                        "Driver marshalled",
                        "Cap",
                        wxMod.YES_NO | wxMod.HELP,
                        parent=SimpleNamespace(GetId=lambda: (_ for _ in ()).throw(RuntimeError("deleted"))),
                    )
                    self.assertEqual(wxMod.YES, resDriver)
                    self.assertEqual(["driverHelper"], helperCalls)
                    self.assertIs(liveMainFrame, dialogCalls[0]["parent"])
        finally:
            wxMod.IsMainThread = originalIsMainThread
            guiMod.messageBox = originalGuiMessageBox
            guiMod.mainFrame = originalMainFrame
            if originalGuiMessage is None:
                with contextlib.suppress(AttributeError):
                    delattr(guiMod, "message")
            else:
                guiMod.message = originalGuiMessage  # type: ignore[attr-defined]
            if originalWxCallOnMainHelper is None:
                with contextlib.suppress(AttributeError):
                    delattr(guiMod.guiHelper, "wxCallOnMain")
            else:
                guiMod.guiHelper.wxCallOnMain = originalWxCallOnMainHelper  # type: ignore[attr-defined]

    def test_is_windows_store_nvda_handles_missing_is_appx_attribute(self) -> None:
        config = self.stubs["config"]
        hadAttr = hasattr(config, "isAppX")
        prevVal = getattr(config, "isAppX", None)
        try:
            with contextlib.suppress(AttributeError):
                delattr(config, "isAppX")
            with mock.patch.dict(sys.modules, {"config": config}):
                self.assertFalse(self.pluginMod.uiUtils.is_windows_store_nvda())
                config.isAppX = True  # type: ignore[attr-defined]
                self.assertTrue(self.pluginMod.uiUtils.is_windows_store_nvda())
        finally:
            if hadAttr:
                config.isAppX = prevVal  # type: ignore[attr-defined]
            else:
                with contextlib.suppress(AttributeError):
                    delattr(config, "isAppX")

    def test_load_settings_reloads_voice_dict_and_voice_for_language_empty_catalog(self) -> None:
        driver = self.synthMod.SynthDriver.__new__(self.synthMod.SynthDriver)
        driver._speakersByLanguage = {}
        driver._variantsByLanguage = {}
        driver._availableVoices = {}
        driver._availableLanguages = set()
        driver.catalog = SimpleNamespace(
            speakers=[],
            speaker_for_voice=lambda v: (_ for _ in ()).throw(KeyError(v)),
            language_for_voice=lambda v: (_ for _ in ()).throw(KeyError(v)),
        )
        driver._SynthDriver__voice = ""
        driver._SynthDriver__variant = ""

        # Empty catalog and missing fallback must return "" cleanly rather than raising KeyError.
        self.assertEqual("", driver._voice_for_language("fr-FR", "missing-voice"))

        reloadCalls: list[bool] = []
        driver._reload_voice_dict = lambda: reloadCalls.append(True)
        driver._ensure_config_compat = lambda: None
        driver.loadSettings(onlyChanged=True)
        self.assertEqual([True], reloadCalls)

    def test_open_voice_manager_and_settings_respect_secure_mode_and_dead_dialog(self) -> None:
        plugin = self.pluginMod
        globalVars = self.stubs["globalVars"]
        guiMod = self.stubs["gui"]
        originalSecure = globalVars.appArgs.secure
        originalPopup = guiMod.mainFrame.popupSettingsDialog
        originalDialogRef = plugin._dialog
        popupCalls: list[Any] = []
        createdDialogs: list[Any] = []

        class _FakeVoiceManagerDialog:
            def __init__(self, parent: Any, onDestroy: Any, initialPage: str = "installed") -> None:
                self.parent = parent
                self.onDestroy = onDestroy
                self.initialPage = initialPage
                self.shown = False
                createdDialogs.append(self)

            def Show(self) -> None:
                self.shown = True

            def Destroy(self) -> None:
                self.shown = False

        try:
            guiMod.mainFrame.popupSettingsDialog = lambda *a, **k: popupCalls.append(a)
            with mock.patch.object(plugin, "VoiceManagerDialog", _FakeVoiceManagerDialog):
                globalVars.appArgs.secure = True
                plugin._open_voice_manager()
                plugin._open_google_tts_settings()
                self.assertEqual([], createdDialogs)
                self.assertEqual([], popupCalls)

                # Dead dialog reference in normal mode must be cleared and replaced by a new live instance.
                globalVars.appArgs.secure = False
                plugin._dialog = SimpleNamespace(IsBeingDeleted=lambda: True)
                plugin._open_voice_manager(initialPage="download")
                self.assertEqual(1, len(createdDialogs))
                self.assertEqual("download", createdDialogs[0].initialPage)
                self.assertTrue(createdDialogs[0].shown)

                plugin._open_google_tts_settings()
                self.assertEqual(1, len(popupCalls))
        finally:
            plugin._close_voice_manager()
            plugin._dialog = originalDialogRef
            globalVars.appArgs.secure = originalSecure
            guiMod.mainFrame.popupSettingsDialog = originalPopup

    def test_spelling_overlay_partial_config_and_nvda_2026_five_arg_signature(self) -> None:
        plugin = self.pluginMod
        config = self.stubs["config"]
        speechModule = self.stubs["speech.speech"]
        fakeSynth = self._make_auto_synth(plugin, profile={"capPitchChange": 60})
        seenConfigs: list[dict[str, Any]] = []
        spellingKwargs: list[tuple[bool, bool]] = []

        def nvda_2026_spelling(
            text: str,
            locale: str | None = None,
            useCharacterDescriptions: bool = False,
            endsUtterance: bool = True,
            useCharMode: bool = True,
        ) -> Any:
            seenConfigs.append(dict(config.conf["speech"][plugin.SYNTH_NAME]))
            spellingKwargs.append((endsUtterance, useCharMode))
            yield f"{text}:{endsUtterance}:{useCharMode}"

        def nvda_single_char(text: str, locale: str | None = None) -> Any:
            seenConfigs.append(dict(config.conf["speech"][plugin.SYNTH_NAME]))
            yield text

        # Partial config missing optional character keys (sayCapForCapitals, beepForCapitals, useSpellingFunctionality).
        with self._auto_profile_env(
            synth=fakeSynth,
            speechConf={plugin.SYNTH_NAME: {"capPitchChange": 25}},
            pluginConf={
                plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                plugin.CONFIG_AUTO_LANGUAGE_PROFILES: '{"en-US": {"enabled": true, "voice": "en1", "capPitchChange": 60}}',
            },
            getSpellingSpeechFn=nvda_2026_spelling,
            getSingleCharDescriptionFn=nvda_single_char,
        ):
            gen = speechModule.getSpellingSpeech("A", locale="en-US", endsUtterance=False, useCharMode=False)
            self.assertEqual(["A:False:False"], list(gen))
            self.assertEqual([(False, False)], spellingKwargs)
            self.assertEqual(60, seenConfigs[0]["capPitchChange"])
            self.assertFalse(seenConfigs[0]["sayCapForCapitals"])
            self.assertEqual({"capPitchChange": 25}, config.conf["speech"][plugin.SYNTH_NAME])

            genChar = speechModule.getSingleCharDescription("B", locale="en-US")
            self.assertEqual(["B"], list(genChar))
            self.assertEqual({"capPitchChange": 25}, config.conf["speech"][plugin.SYNTH_NAME])

    def test_voice_manager_user_friendly_error_handles_http_exception(self) -> None:
        import http.client

        dialog = self.pluginMod.voiceManager.VoiceManagerDialog.__new__(self.pluginMod.voiceManager.VoiceManagerDialog)
        message = dialog._user_friendly_error_message(http.client.IncompleteRead(b"part"))
        self.assertIn("internet connection", message.lower())

    def test_fatal_fallback_extraction_debounce_and_speech_loop_resilience(self) -> None:
        synthDriverHandler = self.stubs["synthDriverHandler"]
        uiUtils = self.pluginMod.uiUtils

        class _NvdaStrictLogger:
            def __init__(self) -> None:
                self.exceptions: list[str] = []
                self.errors: list[str] = []

            def debug(self, *args: Any, **kwargs: Any) -> None:
                pass

            def error(self, msg: str = "", *args: Any, **kwargs: Any) -> None:
                self.errors.append(msg % args if args else msg)

            def exception(self, msg: str = "", exc_info: bool = True, **kwargs: Any) -> None:
                self.exceptions.append(msg)

        # 1. Real _trigger_fatal_fallback debounces duplicate calls, clears queue, cancels, switches synth, shows dialog once.
        driver = self._make_real_driver()
        driver._speechQueue.append((["hi"], "en1", 50, False, 50, 100, "0", threading.Event()))
        cancelCalls: list[bool] = []
        driver.cancel = lambda *a, **k: cancelCalls.append(True)
        fallbackCalls: list[str] = []
        dialogCalls: list[tuple[Any, ...]] = []
        fallbackLog = _NvdaStrictLogger()
        with (
            mock.patch.object(self.synthMod, "log", fallbackLog),
            mock.patch.dict(
                sys.modules,
                {
                    "synthDriverHandler": synthDriverHandler,
                    "globalPlugins.googleTtsForNvda.uiUtils": uiUtils,
                },
            ),
            mock.patch.object(synthDriverHandler, "findAndSetNextSynth", side_effect=fallbackCalls.append),
            mock.patch.object(
                uiUtils,
                "show_runtime_error_dialog",
                side_effect=lambda *a, **k: dialogCalls.append((a, k)),
            ),
        ):
            driver._trigger_fatal_fallback("Fatal error 1")
            driver._trigger_fatal_fallback("Fatal error 2")

        self.assertTrue(driver._fallbackTriggered)
        self.assertEqual([True], cancelCalls)
        self.assertEqual(0, len(driver._speechQueue))
        self.assertEqual(["googleTtsForNvda"], fallbackCalls)
        self.assertEqual(1, len(dialogCalls))
        self.assertEqual({"message": "Fatal error 1", "delayMs": 150}, dialogCalls[0][1])

        # 2. Real _speak_worker error message extraction & NVDA strict logger.exception(msg, exc_info=True) signature.

        cdpErr = self.synthMod.bridge.CdpError(
            "The Chromium browser runtime connection closed unexpectedly.",
            "WebSocket closed abruptly",
        )
        defaultMsg = "Google TTS For NVDA could not start speech in the Chromium browser runtime."
        for excToRaise, expectedMsg, expectedLogSubstr in (
            (cdpErr, "The Chromium browser runtime connection closed unexpectedly.", "WebSocket closed abruptly"),
            (RuntimeError(""), defaultMsg, "Google TTS speech failed."),
        ):
            wDriver = self._make_real_driver(bridgeSpeak=lambda *a, _e=excToRaise, **k: (_ for _ in ()).throw(_e))
            seenFallback: list[str] = []
            wDriver._trigger_fatal_fallback = seenFallback.append
            strictLog = _NvdaStrictLogger()
            with (
                mock.patch.object(self.synthMod, "log", strictLog),
                mock.patch.object(
                    self.synthMod.ChromeTtsBridge,
                    "browser_runtime_available",
                    classmethod(lambda cls: False),
                ),
            ):
                wDriver._speak_worker(["Hello."], "en1", 50, False, 50, 100, "0", threading.Event())
            self.assertEqual([expectedMsg], seenFallback)
            self.assertTrue(any(expectedLogSubstr in entry for entry in strictLog.exceptions))

        # 3. Real _speech_loop catches unexpected worker AttributeError, triggers fallback, exits cleanly, clears state.
        loopDriver = self._make_real_driver()
        loopFallbacks: list[str] = []

        def _trigger_loop_fallback(msg: str) -> None:
            loopFallbacks.append(msg)
            loopDriver._shutdownEvent.set()

        loopDriver._speak_worker = lambda *a, **k: (_ for _ in ()).throw(AttributeError("worker crash"))
        loopDriver._trigger_fatal_fallback = _trigger_loop_fallback
        cancelEvent = threading.Event()
        loopDriver._speechQueue.append((["hi"], "en1", 50, False, 50, 100, "0", cancelEvent))

        loopLogger = _NvdaStrictLogger()
        with mock.patch.object(self.synthMod, "log", loopLogger):
            t = threading.Thread(target=loopDriver._speech_loop)
            t.start()
            t.join(timeout=2.0)
        self.assertFalse(t.is_alive())
        self.assertEqual([defaultMsg], loopFallbacks)
        self.assertEqual(["Unexpected exception in speech loop worker."], loopLogger.exceptions)
        self.assertIsNone(loopDriver._activeCancelEvent)

    def test_auto_language_speech_filter_coalescing_access_keys_and_speak_depth(self) -> None:
        plugin = self.pluginMod
        speechModule = self.stubs["speech.speech"]
        langHandling = self.stubs["speech.languageHandling"]
        speechManager = self.stubs["speech.manager"]
        cmds = self.stubs["speech.commands"]
        driver = self._make_real_driver()

        uiaSplitSentence = [
            cmds.LangChangeCommand("en_US"),
            "Đây là máy tính model th",
            cmds.LangChangeCommand("vi_VN"),
            "ế",
            cmds.LangChangeCommand("en_US"),
            " h",
            cmds.LangChangeCommand("vi_VN"),
            "ệ",
            cmds.LangChangeCommand("en_US"),
            " 11",
        ]
        uiaSplitWord = [
            cmds.LangChangeCommand("en_US"),
            "th",
            cmds.LangChangeCommand("vi_VN"),
            "ế",
            cmds.LangChangeCommand("en_US"),
            " ",
        ]
        uiShortcutSeq = [
            "Use automatic language profiles",
            "check box",
            "not checked",
            "Key+",
            cmds.CharacterModeCommand(True),
            "u",
            cmds.CharacterModeCommand(False),
        ]

        # 1. Single active profile: UIA-split & NFD coalesce with NO LangChangeCommand and speak gating returns False.
        inside1Profile: list[bool] = []
        with self._auto_profile_env(
            synth=driver,
            speechConf={"autoDialectSwitching": False, "autoLanguageSwitching": False},
            pluginConf={
                plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                plugin.CONFIG_AUTO_LANGUAGE_PROFILES: '{"vi-VN": {"enabled": true, "voice": "vi1"}}',
            },
            speakFn=lambda seq, **kw: inside1Profile.append(langHandling.shouldMakeLangChangeCommand()),
            shouldMakeLangChangeCommandFn=lambda: False,
        ):
            self.assertFalse(langHandling.shouldMakeLangChangeCommand())
            self.assertEqual(
                ["Đây là máy tính model thế hệ 11"],
                plugin._filter_auto_language_speech_sequence(uiaSplitSentence),
            )
            self.assertEqual(
                ["Đây là máy tính model thế hệ 11"],
                plugin._filter_auto_language_speech_sequence(
                    ["Đây la\u0300 ma\u0301y ti\u0301nh model thê\u0301 hê\u0323 11"]
                ),
            )
            speechModule.speak(["hi"])
            self.assertEqual([False], inside1Profile)
            self.assertFalse(langHandling.shouldSwitchVoice())
            self.assertFalse(speechManager.shouldSwitchVoice())

        # 2. Two active profiles: UIA coalescing, UI shortcut char-mode, SuppressUnicodeNormalization, speak depth gating.
        inside2Profiles: list[bool] = []
        with self._auto_profile_env(
            synth=driver,
            speechConf={"autoDialectSwitching": False, "autoLanguageSwitching": False},
            pluginConf={
                plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                plugin.CONFIG_AUTO_LANGUAGE_PROFILES: (
                    '{"en-US": {"enabled": true, "voice": "en1"}, "vi-VN": {"enabled": true, "voice": "vi1"}}'
                ),
                plugin.CONFIG_AUTO_LANGUAGE_PREFERRED: "vi-VN",
            },
            speakFn=lambda seq, **kw: inside2Profiles.append(langHandling.shouldMakeLangChangeCommand()),
            shouldMakeLangChangeCommandFn=lambda: False,
        ):
            multiResult = plugin._filter_auto_language_speech_sequence(uiaSplitSentence)
            self.assertEqual(2, len(multiResult))
            self.assertIsInstance(multiResult[0], cmds.LangChangeCommand)
            self.assertEqual("vi_VN", multiResult[0].lang)
            self.assertEqual("Đây là máy tính model thế hệ 11", multiResult[1])

            wordResult = plugin._filter_auto_language_speech_sequence(uiaSplitWord)
            self.assertEqual(2, len(wordResult))
            self.assertEqual("vi_VN", wordResult[0].lang)
            self.assertEqual("thế ", wordResult[1])

            shortcutResult = plugin._filter_auto_language_speech_sequence(uiShortcutSeq)
            shortcutLangChanges = [x for x in shortcutResult if isinstance(x, cmds.LangChangeCommand)]
            self.assertEqual(1, len(shortcutLangChanges))
            self.assertEqual("en_US", shortcutLangChanges[0].lang)

            suppressSeq = [
                plugin._google_lang_change_command("en-US", spelling=True),
                cmds.SuppressUnicodeNormalizationCommand(True),
                "a",
                cmds.SuppressUnicodeNormalizationCommand(False),
                "Use automatic language profiles",
                cmds.CharacterModeCommand(True),
                "u",
                cmds.CharacterModeCommand(False),
            ]
            suppressResult = plugin._filter_auto_language_speech_sequence(suppressSeq)
            suppressLangChanges = [x for x in suppressResult if isinstance(x, cmds.LangChangeCommand)]
            self.assertEqual(["en_US"], [c.lang for c in suppressLangChanges])

            self.assertFalse(langHandling.shouldMakeLangChangeCommand())
            speechModule.speak(["hi"])
            self.assertEqual([True], inside2Profiles)
            self.assertFalse(langHandling.shouldMakeLangChangeCommand())
            self.assertTrue(langHandling.shouldSwitchVoice())
            self.assertTrue(speechManager.shouldSwitchVoice())

    def test_synth_get_language_process_text_spelling_and_auto_voice_dict(self) -> None:
        plugin = self.pluginMod
        speechModule = self.stubs["speech.speech"]
        speechDictHandler = self.stubs["speechDictHandler"]
        driver = self._make_real_driver(
            speakersByLanguage={
                "en-US": [self._make_speaker("en1", "English 1", "en-US", "p1")],
                "vi-VN": [self._make_speaker("vi1", "Vietnamese 1", "vi-VN", "p2")],
                "cmn-CN": [self._make_speaker("cmn1", "Chinese 1", "cmn-CN", "p3")],
            }
        )

        # 1. SynthDriver._get_language() under auto ON (1 & 2 profiles) and OFF.
        with mock.patch.object(
            self.synthMod.language_utils,
            "nvda_locale_exists",
            side_effect=lambda loc: loc in {"en_US", "vi_VN"},
        ):
            with self._auto_profile_env(
                synth=driver,
                pluginConf={
                    plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                    plugin.CONFIG_AUTO_LANGUAGE_PROFILES: '{"vi-VN": {"enabled": true, "voice": "vi1"}}',
                },
                patchHooks=False,
            ):
                self.assertEqual("vi_VN", driver._get_language())

            with self._auto_profile_env(
                synth=driver,
                pluginConf={
                    plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                    plugin.CONFIG_AUTO_LANGUAGE_PROFILES: (
                        '{"en-US": {"enabled": true, "voice": "en1"}, "vi-VN": {"enabled": true, "voice": "vi1"}}'
                    ),
                    plugin.CONFIG_AUTO_LANGUAGE_PREFERRED: "vi-VN",
                },
                patchHooks=False,
            ):
                self.assertEqual("vi_VN", driver._get_language())

            with self._auto_profile_env(
                synth=driver,
                pluginConf={
                    plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                    plugin.CONFIG_AUTO_LANGUAGE_PROFILES: (
                        '{"en-US": {"enabled": true, "voice": "en1"}, "vi-VN": {"enabled": true, "voice": "vi1"}}'
                    ),
                    plugin.CONFIG_AUTO_LANGUAGE_PREFERRED: "en-US",
                },
                patchHooks=False,
            ):
                self.assertEqual("vi-VN", plugin._auto_language_for_spelling_text(driver, "đ"))
                self.assertEqual("en-US", plugin._auto_language_for_spelling_text(driver, "a"))

            driver2 = self._make_real_driver(
                speakersByLanguage={
                    "en-US": driver._speakersByLanguage["en-US"],
                    "cmn-CN": driver._speakersByLanguage["cmn-CN"],
                }
            )
            with self._auto_profile_env(
                synth=driver2,
                pluginConf={
                    plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                    plugin.CONFIG_AUTO_LANGUAGE_PROFILES: (
                        '{"en-US": {"enabled": true, "voice": "en1"}, "cmn-CN": {"enabled": true, "voice": "cmn1"}}'
                    ),
                    plugin.CONFIG_AUTO_LANGUAGE_PREFERRED: "en-US",
                },
                patchHooks=False,
            ):
                self.assertEqual("cmn-CN", plugin._auto_language_for_spelling_text(driver2, "你"))
                self.assertEqual("cmn-CN", plugin._auto_language_for_spelling_text(driver2, "中文"))
                self.assertEqual("en-US", plugin._auto_language_for_spelling_text(driver2, "a"))

            with self._auto_profile_env(
                synth=driver,
                pluginConf={plugin.CONFIG_AUTO_LANGUAGE_DETECTION: False},
                patchHooks=False,
            ):
                self.assertEqual("en_US", driver._get_language())

        # 2. Real processText normalization & temporary auto-profile voice dict switching + restore.
        loadedVariants: list[str] = []
        originalLoadVoiceDict = speechDictHandler.loadVoiceDict
        originalDefsLoadVoiceDict = speechDictHandler.definitions.loadVoiceDict
        try:

            def fake_dict_loader(synthArg: Any) -> None:
                loadedVariants.append(getattr(synthArg, "voice", ""))

            speechDictHandler.loadVoiceDict = fake_dict_loader
            speechDictHandler.definitions.loadVoiceDict = fake_dict_loader
            plugin._voiceDictCache.clear()
            plugin._activeLoadedVoiceDictVariant = "en1"

            seenProcessCalls: list[tuple[str, str, str | None]] = []

            def orig_process_text(locale: str, text: str) -> str:
                seenProcessCalls.append((locale, text, plugin._activeLoadedVoiceDictVariant))
                return text

            with mock.patch.object(
                self.synthMod.language_utils,
                "nvda_locale_exists",
                side_effect=lambda loc: loc in {"en_US", "vi_VN"},
            ):
                # Auto OFF still normalizes NFD and math bold characters.
                with self._auto_profile_env(
                    synth=driver,
                    speechConf={"unicodeNormalization": False, plugin.SYNTH_NAME: {}},
                    pluginConf={plugin.CONFIG_AUTO_LANGUAGE_DETECTION: False},
                    processTextFn=orig_process_text,
                ):
                    self.assertEqual("thế hệ", speechModule.processText("en_US", "thê\u0301 hê\u0323"))
                    self.assertEqual(
                        "Hello",
                        speechModule.processText("en_US", "\U0001d407\U0001d41e\U0001d425\U0001d425\U0001d428"),
                    )

                # Auto ON temporarily swaps voice dict to vi1 during processText and restores en1 afterward.
                seenProcessCalls.clear()
                loadedVariants.clear()
                with self._auto_profile_env(
                    synth=driver,
                    speechConf={"unicodeNormalization": False, plugin.SYNTH_NAME: {}},
                    pluginConf={
                        plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                        plugin.CONFIG_AUTO_LANGUAGE_PROFILES: (
                            '{"en-US": {"enabled": true, "voice": "en1"}, "vi-VN": {"enabled": true, "voice": "vi1"}}'
                        ),
                        plugin.CONFIG_AUTO_LANGUAGE_PREFERRED: "en-US",
                    },
                    processTextFn=orig_process_text,
                ):
                    out = speechModule.processText("en_US", "Đây là tiếng Việt")
                    self.assertEqual("Đây là tiếng Việt", out)
                    self.assertEqual([("vi_VN", "Đây là tiếng Việt", "vi1")], seenProcessCalls)
                    self.assertEqual(["vi1", "en1"], loadedVariants)
                    self.assertEqual("en1", plugin._activeLoadedVoiceDictVariant)
        finally:
            speechDictHandler.loadVoiceDict = originalLoadVoiceDict
            speechDictHandler.definitions.loadVoiceDict = originalDefsLoadVoiceDict
            plugin._voiceDictCache.clear()
            plugin._activeLoadedVoiceDictVariant = None

        # 3. Real getSpellingSpeech respects NVDA unicodeNormalization while always normalizing UCD 17.0 symbols.
        def orig_spelling(text: str, locale: str | None = None, useCharacterDescriptions: bool = False) -> Any:
            yield text

        for normEnabled, rawInput, expectedText in (
            (False, "e\u0302\u0301", "ế"),
            (True, "e\u0302\u0301", "e\u0302\u0301"),
            (True, "\U0001f150", "A"),
            (False, "\U0001f150", "A"),
        ):
            with self._auto_profile_env(
                synth=driver,
                speechConf={"unicodeNormalization": normEnabled, plugin.SYNTH_NAME: {}},
                pluginConf={plugin.CONFIG_AUTO_LANGUAGE_DETECTION: False},
                getSpellingSpeechFn=orig_spelling,
            ):
                self.assertEqual([expectedText], list(speechModule.getSpellingSpeech(rawInput, "en_US")))

    def test_iter_speech_chunks_pause_modes_flush_threshold_char_indexes_and_zero_rate(self) -> None:
        cmds = self.stubs["speech.commands"]
        driver = self._make_real_driver()
        mode0 = self.synthMod._PAUSE_MODE_DO_NOT_SHORTEN
        mode1 = self.synthMod._PAUSE_MODE_SHORTEN_END_ONLY
        mode2 = self.synthMod._PAUSE_MODE_SHORTEN_ALL

        with self._patched_conf(
            **{self.pluginMod.CONFIG_SECTION: {self.pluginMod.CONFIG_AUTO_LANGUAGE_DETECTION: False}}
        ):
            # 1. Pause mode resolution: mode 1 never shortens punctuated utterances or breaks.
            for mode, text, expectShortMode in (
                (mode0, "OK button", mode0),
                (mode0, "Hello world.", mode0),
                (mode1, "OK button", mode1),
                (mode1, 'He said "Hello"', mode1),
                (mode1, "Hello world.", mode0),
                (mode1, 'He said "Hello."', mode0),
                (mode1, "Downloads (5)", mode0),
                (mode2, "OK button", mode2),
                (mode2, "Hello world.", mode2),
            ):
                chunks = list(driver._iter_speech_chunks([text], "en1", 50, False, 50, 100, mode, threading.Event()))
                textChunks = [c for c in chunks if c[0] == "text"]
                endPauses = [c for c in chunks if c[0] == "end_pause"]
                self.assertEqual(expectShortMode, textChunks[-1][1][4], msg=(mode, text))
                self.assertEqual([], endPauses, msg=(mode, text))

            for mode, expectedMid in ((mode0, mode0), (mode1, mode0), (mode2, mode2)):
                brkChunks = list(
                    driver._iter_speech_chunks(
                        ["Hello", cmds.BreakCommand(100), "world."],
                        "en1",
                        50,
                        False,
                        50,
                        100,
                        mode,
                        threading.Event(),
                    )
                )
                brkText = [c for c in brkChunks if c[0] == "text"]
                self.assertEqual(expectedMid, brkText[0][1][4], msg=mode)

            # Inline non-break prosody/lang switches still trim unpunctuated WASM padding in mode 0/1,
            # while punctuated boundaries stay unshortened in mode 0/1.
            inlineChunks = list(
                driver._iter_speech_chunks(
                    ["Hello", cmds.PitchCommand(20), "world."],
                    "en1",
                    50,
                    False,
                    50,
                    100,
                    mode0,
                    threading.Event(),
                )
            )
            inlineText = [c for c in inlineChunks if c[0] == "text"]
            self.assertEqual([mode1, mode0], [c[1][4] for c in inlineText])
            punctInlineChunks = list(
                driver._iter_speech_chunks(
                    ["Hello.", cmds.RateCommand(70), "World."],
                    "en1",
                    50,
                    False,
                    50,
                    100,
                    mode1,
                    threading.Event(),
                )
            )
            punctInlineText = [c for c in punctInlineChunks if c[0] == "text"]
            self.assertEqual([mode0, mode0], [c[1][4] for c in punctInlineText])

            multiChunks = list(
                driver._iter_speech_chunks(
                    ["First sentence is clearly long enough. Second sentence follows."],
                    "en1",
                    50,
                    False,
                    50,
                    100,
                    mode1,
                    threading.Event(),
                )
            )
            self.assertIn(("break", 45), multiChunks)
            multiShort = list(
                driver._iter_speech_chunks(
                    ["First sentence is clearly long enough. Second sentence follows."],
                    "en1",
                    50,
                    False,
                    50,
                    100,
                    mode2,
                    threading.Event(),
                )
            )
            self.assertIn(("break", 15), multiShort)

            # 2. CharacterModeCommand spacing keeps IndexCommand offset aligned after CharacterModeCommand(False).
            charSeq = [
                "Key+",
                cmds.CharacterModeCommand(True),
                "u",
                "v",
                cmds.CharacterModeCommand(False),
                cmds.IndexCommand(1),
                "button",
            ]
            charChunks = list(driver._iter_speech_chunks(charSeq, "en1", 50, False, 50, 100, mode0, threading.Event()))
            self.assertEqual(1, len(charChunks))
            spokenText, _opts, idxList, _hidden, _psm = charChunks[0][1]
            self.assertEqual("Key+ u v button", spokenText)
            self.assertEqual([(1, 8)], idxList)
            self.assertEqual(" button", spokenText[idxList[0][1] :])

            # 3. _FLUSH_GROUP_CHARS_THRESHOLD splits long single sentence only in PAUSE_MODE_SHORTEN_ALL.
            longSentence = (
                "First clause has plenty of readable words right here, "
                "and the second clause keeps adding descriptive detail across many phrases so it exceeds "
                "one hundred fifty characters cleanly before pausing right here, "
                "while the third clause concludes with enough trailing content to require another segment later cleanly."
            )
            shortInAll = list(
                driver._iter_speech_chunks(
                    ["This is short, concise, and clear."], "en1", 50, False, 50, 100, mode2, threading.Event()
                )
            )
            self.assertEqual(1, sum(1 for k, _p in shortInAll if k == "text"))

            longInAll = list(
                driver._iter_speech_chunks([longSentence], "en1", 50, False, 50, 100, mode2, threading.Event())
            )
            self.assertGreaterEqual(sum(1 for k, _p in longInAll if k == "text"), 2)
            self.assertIn(("break", 15), longInAll)

            longInDoNot = list(
                driver._iter_speech_chunks([longSentence], "en1", 50, False, 50, 100, mode0, threading.Event())
            )
            self.assertEqual(1, sum(1 for k, _p in longInDoNot if k == "text"))

            # 4. Zero percent rate (rate=0) applies break & EOU scaling before _finish_request_audio().
            workerEvents: list[tuple[str, Any]] = []
            zDriver = self._make_real_driver(rate=0)
            zDriver._speak_text = lambda *a, **k: workerEvents.append(("speak", a[0]))
            zDriver._feed_silence = lambda ms: workerEvents.append(("silence", ms))
            zDriver._finish_request_audio = lambda: workerEvents.append(("finish", None))
            zDriver._speak_worker(
                [cmds.BreakCommand(100), "Hello."], "en1", 0, False, 50, 100, mode1, threading.Event()
            )
            # rate=0 -> break factor 1.8 (100*1.8=180ms) before "Hello." (mode 1 keeps period pause unchanged).
            self.assertEqual(
                [("silence", 180), ("speak", "Hello."), ("finish", None)],
                workerEvents,
            )

    def test_speak_text_pre_cancel_unpunctuated_boundary_terminate_cache_and_pause(self) -> None:
        mode0 = self.synthMod._PAUSE_MODE_DO_NOT_SHORTEN
        fedChunks: list[bytes] = []
        playerCalls: list[tuple[str, Any]] = []
        fakePlayer = self._make_fake_player(
            feed=lambda pcm: fedChunks.append(pcm),
            pause=lambda sw: playerCalls.append(("pause", sw)),
            stop=lambda: playerCalls.append(("stop", None)),
        )
        seg0BytesAfterEnd: list[int] = []

        def fake_bridge_speak(
            text: str,
            options: dict[str, Any],
            onAudio: Any,
            cancelEvent: threading.Event,
            *,
            onSegmentEnd: Any = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            audible = b"\x10\x20" * 1920  # 3840 bytes = exact LIVE_MULTI_SEGMENT_LEAD_MS (80ms) at 24kHz
            silence = b"\x00\x00" * 24000  # 48000 bytes = 1000ms at 24kHz
            onAudio(audible)
            onAudio(silence)
            if onSegmentEnd is not None:
                onSegmentEnd()
            seg0BytesAfterEnd.append(sum(len(c) for c in fedChunks))
            onAudio(audible)
            onAudio(silence)
            return {"success": True, "done": True, "segmentEnds": 1}

        driver = self._make_real_driver(bridgeSpeak=fake_bridge_speak, player=fakePlayer)
        opts = driver._speech_options(50, 50, 100, "en1", False)

        # 1. Pre-cancelled event skips _bridge.speak entirely.
        cancelled = threading.Event()
        cancelled.set()
        driver._speak_text("Hello.", opts, cancelled)
        self.assertEqual([], fedChunks)

        # 2. Unpunctuated hidden boundary shortens mid-phrase silence (<5000B) yet preserves final 1000ms silence (48000B).
        driver._speak_text(
            "Part one part two.",
            opts,
            threading.Event(),
            hiddenSegments=["Part one ", "part two."],
            pauseShorteningMode=mode0,
        )
        self.assertEqual(1, len(seg0BytesAfterEnd))
        midPhraseSilenceBytes = seg0BytesAfterEnd[0] - 3840
        finalTrailingSilenceBytes = sum(len(c) for c in fedChunks) - seg0BytesAfterEnd[0] - 3840
        self.assertGreater(midPhraseSilenceBytes, 0)
        self.assertLess(midPhraseSilenceBytes, 5000)
        self.assertEqual(48000, finalTrailingSilenceBytes)

        # terminate() clears _shortAudioCache and acquires _warmup_lock cleanly.
        termDriver = self._make_real_driver()
        termDriver._shortAudioCache[("dummy-key",)] = b"\x01\x00" * 100
        termDriver._shortAudioCacheBytes = 200
        termDriver.terminate()
        self.assertEqual(0, len(termDriver._shortAudioCache))
        self.assertEqual(0, termDriver._shortAudioCacheBytes)

        # 3. pause(True/False) updates _isPaused and forwards to player.
        driver.pause(True)
        self.assertTrue(driver._isPaused)
        driver.pause(False)
        self.assertFalse(driver._isPaused)
        self.assertEqual([("pause", True), ("pause", False)], playerCalls)

    def test_sayall_wrapper_and_live_catalog_refresh(self) -> None:
        plugin = self.pluginMod
        driver = self._make_real_driver()
        cmds = self.stubs["speech.commands"]
        sayAll = self.stubs["speech.sayAll"].SayAllHandler.speechWithoutPausesInstance
        sayAllSeen: list[tuple[str, list[Any]]] = []

        def _record_sayall_speak(seq: Any, **k: Any) -> None:
            sayAllSeen.append(("speak", list(seq)))

        def _record_sayall_without_pauses(seq: Any, **k: Any) -> bool:
            sayAllSeen.append(("withoutPauses", list(seq)))
            return True

        uiaSplit = [cmds.LangChangeCommand("en_US"), "th", cmds.LangChangeCommand("vi_VN"), "ế"]
        with self._auto_profile_env(
            synth=driver,
            pluginConf={
                plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True,
                plugin.CONFIG_AUTO_LANGUAGE_PROFILES: '{"vi-VN": {"enabled": true, "voice": "vi1"}}',
            },
            sayAllSpeakFn=_record_sayall_speak,
            speakWithoutPausesFn=_record_sayall_without_pauses,
        ):
            sayAll.speak(uiaSplit)
            self.assertTrue(sayAll.speakWithoutPauses(uiaSplit))
        self.assertEqual([("speak", uiaSplit), ("withoutPauses", ["thế"])], sayAllSeen)

        globalVars = self.stubs["globalVars"]
        synthDriverHandler = self.stubs["synthDriverHandler"]
        originalGetSynth = synthDriverHandler.getSynth
        originalRing = getattr(globalVars, "settingsRing", None)
        ringUpdates: list[Any] = []
        dictReloads: list[bool] = []
        driver._reload_voice_dict = lambda: dictReloads.append(True)
        newFrenchPkg = self._make_voice_package(
            "fr-fr-x-multi",
            speakers=({"speaker": "fr1", "name": "French 1", "gender": "female"},),
        )
        newFullCatalog = self.synthMod.VoiceCatalog([newFrenchPkg])
        vmDialog = plugin.voiceManager.VoiceManagerDialog.__new__(plugin.voiceManager.VoiceManagerDialog)
        try:
            synthDriverHandler.getSynth = lambda: driver
            globalVars.settingsRing = SimpleNamespace(updateSupportedSettings=lambda s: ringUpdates.append(s))
            with (
                mock.patch.object(self.synthMod.VoiceCatalog, "load", return_value=newFullCatalog),
                mock.patch.object(self.synthMod.voice_store, "installed_packages", return_value=[newFrenchPkg]),
            ):
                self.assertTrue(vmDialog._refresh_current_google_synth_catalog())
            self.assertIn("fr-FR", driver.availableVoices)
            self.assertEqual("fr-FR", driver._SynthDriver__voice)
            self.assertEqual("fr-fr-x-multi:fr1", driver._SynthDriver__variant)
            self.assertEqual([driver], ringUpdates)
            self.assertEqual([True], dictReloads)
        finally:
            synthDriverHandler.getSynth = originalGetSynth
            globalVars.settingsRing = originalRing

    def test_updater_finish_download_prepopup_and_postpopup_flow(self) -> None:
        plugin = self.pluginMod
        guiMod = self.stubs["gui"]
        updaterMod = plugin.updater
        updateGuiMod = plugin.updateGui
        popupEvents: list[str] = []
        origPre = guiMod.mainFrame.prePopup
        origPost = guiMod.mainFrame.postPopup
        origMainIsBeingDeleted = getattr(guiMod.mainFrame, "IsBeingDeleted", None)
        origMainGetId = getattr(guiMod.mainFrame, "GetId", None)
        hadGuiAddon = hasattr(guiMod, "addonGui")
        origGuiAddon = getattr(guiMod, "addonGui", None)
        try:
            with self._patched_conf(**{plugin.CONFIG_SECTION: {updateGuiMod.CONFIG_AUTO_UPDATE_CHECK: "yes"}}) as conf:
                self.assertTrue(updateGuiMod.automatic_update_check_enabled())
                conf[plugin.CONFIG_SECTION][updateGuiMod.CONFIG_AUTO_UPDATE_CHECK] = "off"
                self.assertFalse(updateGuiMod.automatic_update_check_enabled())

                guiMod.mainFrame.IsBeingDeleted = lambda: False
                guiMod.mainFrame.GetId = lambda: 1
                guiMod.mainFrame.prePopup = lambda: popupEvents.append("prePopup")
                guiMod.mainFrame.postPopup = lambda: popupEvents.append("postPopup")
                fakeAddonGui = ModuleType("gui.addonGui")

                def _install_fake_addon(parent: Any, path: Path) -> bool:
                    popupEvents.append(f"install:{path}")
                    return True

                fakeAddonGui.installAddon = _install_fake_addon  # type: ignore[attr-defined]
                fakeAddonGui.promptUserForRestart = lambda: popupEvents.append("restart")  # type: ignore[attr-defined]
                guiMod.addonGui = fakeAddonGui  # type: ignore[attr-defined]

                controller, fakeUpdate, fakeCheckResult = self._make_update_check_fixture(
                    updateGuiMod._CHECK_MODE_MANUAL, version="1.1"
                )
                fakeDownloaded = updaterMod.DownloadedUpdate(update=fakeUpdate, path=Path("fake.nvda-addon"))
                with (
                    mock.patch.dict(sys.modules, {"gui": guiMod, "gui.addonGui": fakeAddonGui}),
                    mock.patch.object(updaterMod, "remove_update_manifest"),
                    mock.patch.object(updaterMod, "remove_downloaded_update"),
                ):
                    controller._finish_download(fakeDownloaded, fakeCheckResult, guiMod.mainFrame)
                self.assertEqual(["prePopup", "install:fake.nvda-addon", "postPopup", "restart"], popupEvents)
        finally:
            guiMod.mainFrame.prePopup = origPre
            guiMod.mainFrame.postPopup = origPost
            if origMainIsBeingDeleted is None:
                with contextlib.suppress(AttributeError):
                    delattr(guiMod.mainFrame, "IsBeingDeleted")
            else:
                guiMod.mainFrame.IsBeingDeleted = origMainIsBeingDeleted
            if origMainGetId is None:
                with contextlib.suppress(AttributeError):
                    delattr(guiMod.mainFrame, "GetId")
            else:
                guiMod.mainFrame.GetId = origMainGetId
            if hadGuiAddon:
                guiMod.addonGui = origGuiAddon  # type: ignore[attr-defined]
            elif hasattr(guiMod, "addonGui"):
                delattr(guiMod, "addonGui")

    def test_auto_profile_lang_command_notice_ring_and_pending_remove_reset(self) -> None:
        plugin = self.pluginMod
        wxMod = self.stubs["wx"]
        driver = self._make_real_driver(
            speakersByLanguage={
                "en-US": [self._make_speaker("en1", "English 1", "en-US", "pkg-en")],
                "vi-VN": [self._make_speaker("vi1", "Vietnamese 1", "vi-VN", "pkg-vi")],
                "fr-FR": [self._make_speaker("fr-fr-x-multi:fr1", "French 1", "fr-FR", "fr-fr-x-multi")],
            }
        )
        newFrenchPkg = self._make_voice_package(
            "fr-fr-x-multi",
            speakers=({"speaker": "fr1", "name": "French 1", "gender": "female"},),
        )
        langDetectorMod = self.synthMod.language_detector
        profileCmd = langDetectorMod.GoogleTtsProfileLangCommand("vi-VN")
        self.assertIsInstance(profileCmd, langDetectorMod.GoogleTtsProfileLangCommand)
        self.assertEqual("vi-VN", profileCmd.language)

        origAutoEnabled = driver._auto_language_detection_enabled
        origVoiceForLang = driver._voice_for_language
        origAutoDetect = driver._auto_detect_profile_for_text
        try:
            driver._auto_language_detection_enabled = lambda: True
            driver._voice_for_language = lambda lang, base: "fr-fr-x-multi:fr1" if lang == "vi-VN" else base
            driver._auto_detect_profile_for_text = (
                lambda text, activeVoice, activeLanguage, baseVoice, rate, rateBoost, pitch, volume: {
                    "voice": activeVoice,
                    "rate": rate,
                    "rateBoost": rateBoost,
                    "pitch": pitch,
                    "volume": volume,
                }
            )
            chunks = list(
                driver._iter_speech_chunks(
                    [profileCmd, "Xin chào"],
                    "en-us-x-multi:tpc",
                    50,
                    False,
                    50,
                    100,
                    "doNotShorten",
                    threading.Event(),
                )
            )
            textPayloads = [payload for kind, payload in chunks if kind == "text"]
            self.assertEqual(1, len(textPayloads))
            self.assertEqual("French 1", textPayloads[0][1]["voiceName"])
        finally:
            driver._auto_language_detection_enabled = origAutoEnabled
            driver._voice_for_language = origVoiceForLang
            driver._auto_detect_profile_for_text = origAutoDetect

        self.assertFalse(self.synthMod.SynthDriver._AUTO_LANGUAGE_NOTICE_SETTING.availableInSettingsRing)

        vmDialog2 = plugin.voiceManager.VoiceManagerDialog.__new__(plugin.voiceManager.VoiceManagerDialog)
        vmDialog2._pendingRemoveAfterSynthSwitch = [newFrenchPkg]
        vmDialog2.installedList = None
        vmDialog2.installedPackages = []
        vmDialog2._is_google_synth_current = lambda: True
        vmDialog2._checked_packages = lambda lst, pkgs: []
        vmDialog2.set_status = lambda *a, **k: None
        vmDialog2.on_remove_selected(None)  # type: ignore[arg-type]
        self.assertIsNone(vmDialog2._pendingRemoveAfterSynthSwitch)

        vmReset = plugin.voiceManager.VoiceManagerDialog.__new__(plugin.voiceManager.VoiceManagerDialog)
        vmReset._allUsableInstalledPackages = [newFrenchPkg]
        with self._patched_conf(
            speech={plugin.SYNTH_NAME: {"voice": "en-US", "variant": "en-us-x-multi:stale"}}
        ) as conf:
            resetVariant = vmReset._reset_configured_voice_if_removed({"other-pkg"})
            self.assertEqual("fr-fr-x-multi:fr1", resetVariant)
            self.assertEqual("fr-fr-x-multi:fr1", conf["speech"][plugin.SYNTH_NAME]["variant"])
            self.assertEqual("fr-FR", conf["speech"][plugin.SYNTH_NAME]["voice"])

            conf[plugin.CONFIG_SECTION] = {
                plugin.CONFIG_AUTO_LANGUAGE_DETECTION: "false",
                plugin.CONFIG_AUTO_LANGUAGE_CANDIDATES: "",
                plugin.CONFIG_AUTO_LANGUAGE_PREFERRED: "",
                plugin.CONFIG_AUTO_LANGUAGE_PROFILES: "{}",
            }
            self.assertFalse(vmReset._reset_auto_language_profile_variants_if_removed({"other-pkg"}))

        busyPkgs = [newFrenchPkg]
        vmBusy = plugin.voiceManager.VoiceManagerDialog.__new__(plugin.voiceManager.VoiceManagerDialog)
        vmBusy._pendingRemoveAfterSynthSwitch = busyPkgs
        vmBusy._closing = False
        vmBusy.IsBeingDeleted = lambda: False
        vmBusy.GetId = lambda: 1
        vmBusy.IsShown = lambda: True
        vmBusy._is_google_synth_current = lambda: False
        vmBusy.isBusy = True
        removedBatches: list[list[Any]] = []
        callLaterDelays: list[int] = []
        vmBusy._remove_packages = lambda pkgs: removedBatches.append(list(pkgs))
        origCallLater = wxMod.CallLater
        try:
            wxMod.CallLater = lambda delay, fn, *a, **k: callLaterDelays.append(delay)
            vmBusy._remove_after_synth_switch(busyPkgs, 2)
            self.assertEqual([500], callLaterDelays)
            self.assertIs(busyPkgs, vmBusy._pendingRemoveAfterSynthSwitch)
            self.assertEqual([], removedBatches)
            vmBusy.isBusy = False
            vmBusy._remove_after_synth_switch(busyPkgs, 2)
            self.assertIsNone(vmBusy._pendingRemoveAfterSynthSwitch)
            self.assertEqual([busyPkgs], removedBatches)
        finally:
            wxMod.CallLater = origCallLater

    def test_settings_bounded_int_empty_save_runtime_error_marshal_and_modal_dialog_checks(self) -> None:
        plugin = self.pluginMod
        wxMod = self.stubs["wx"]
        driver = self._make_real_driver()
        settingsMod = plugin.settingsModule
        self.assertEqual(51, settingsMod._bounded_profile_int("50.6", 50))
        self.assertEqual(50, settingsMod._bounded_profile_int(float("nan"), 50))
        self.assertEqual(50, settingsMod._bounded_profile_int(float("inf"), 50))
        self.assertEqual(50, settingsMod._bounded_profile_int(True, 50))
        self.assertEqual(-30, settingsMod._bounded_profile_int("-30.2", 30, -100, 100))

        panelEmpty = settingsMod.GoogleTtsSettingsPanel.__new__(settingsMod.GoogleTtsSettingsPanel)
        panelEmpty._languageValues = []
        panelEmpty._savedAutoLanguageDetection = True
        ringRefreshCalls: list[bool] = []
        panelEmpty._configured_auto_language_detection = lambda: True
        panelEmpty._refresh_synth_settings_ring = lambda reloadSpeechSettings=False: ringRefreshCalls.append(
            reloadSpeechSettings
        )
        with self._patched_conf(**{plugin.CONFIG_SECTION: {plugin.CONFIG_AUTO_LANGUAGE_DETECTION: True}}) as conf:
            panelEmpty._save_auto_language_settings()
            self.assertFalse(conf[plugin.CONFIG_SECTION][plugin.CONFIG_AUTO_LANGUAGE_DETECTION])
            self.assertFalse(panelEmpty._savedAutoLanguageDetection)
            self.assertEqual([True], ringRefreshCalls)

        origIsMainThread = getattr(wxMod, "IsMainThread", None)
        origCallAfter = getattr(wxMod, "CallAfter", None)
        msgBoxCalls: list[str] = []
        try:
            wxMod.IsMainThread = lambda: False

            def _failing_call_after(*_a: Any, **_k: Any) -> None:
                raise RuntimeError("wx app shutting down")

            wxMod.CallAfter = _failing_call_after
            with mock.patch.object(
                plugin.uiUtils, "show_message_box", side_effect=lambda m, *a, **k: msgBoxCalls.append(m)
            ):
                plugin.uiUtils.show_runtime_error_dialog("Background error")
            self.assertEqual([], msgBoxCalls)
        finally:
            wxMod.IsMainThread = origIsMainThread
            wxMod.CallAfter = origCallAfter

        class SynthesizerSelectionDialog(wxMod.Dialog):
            def IsShown(self) -> bool:
                return True

            def IsModal(self) -> bool:
                return True

        origTopWindows = wxMod.GetTopLevelWindows
        try:
            wxMod.GetTopLevelWindows = lambda: [SynthesizerSelectionDialog()]
            self.assertFalse(driver._has_blocking_modal_dialog())
        finally:
            wxMod.GetTopLevelWindows = origTopWindows

    def test_synth_driver_empty_suffix_index_flush_and_paused_prefix(self) -> None:
        driver = self._make_real_driver()
        opts = driver._speech_options(50, 50, 100, "en1", False)
        mode0 = self.synthMod._PAUSE_MODE_DO_NOT_SHORTEN
        seg0Key = driver._segment_cache_key(
            "Hello",
            opts,
            mode0,
            hasPreviousSegment=False,
            hasNextSegment=True,
        )
        self.assertIsNotNone(seg0Key)
        driver._put_cached_audio(seg0Key, b"\x10\x20" * 64)

        notifiedIndexes: list[int] = []
        with mock.patch.object(
            self.stubs["synthDriverHandler"].synthIndexReached,
            "notify",
            side_effect=lambda **kw: notifiedIndexes.append(kw["index"]),
        ):
            # seg1 ("") cannot be cached, so cachedPrefix="Hello" (5 chars) leaves text="" with index (77, 6).
            driver._speak_text(
                "Hello",
                opts,
                threading.Event(),
                indexes=[(77, 6)],
                hiddenSegments=["Hello", ""],
                pauseShorteningMode=mode0,
            )
        self.assertEqual([77], notifiedIndexes)

    def test_batch3_ui_utils_update_gui_voice_manager_settings_and_auto_spelling_regressions(self) -> None:
        plugin = self.pluginMod
        guiMod = self.stubs["gui"]

        # 1. open_synthesizer_dialog calls popupSettingsDialog even when modal blocks, returning False.
        popupCalls: list[Any] = []
        origPopup = getattr(guiMod.mainFrame, "popupSettingsDialog", None)
        fakeDialogCls = type("SynthesizerSelectionDialog", (), {})
        origSynthDlg = getattr(guiMod.settingsDialogs, "SynthesizerSelectionDialog", None)
        try:
            guiMod.mainFrame.popupSettingsDialog = lambda cls: popupCalls.append(cls)
            guiMod.settingsDialogs.SynthesizerSelectionDialog = fakeDialogCls
            with (
                mock.patch.dict(sys.modules, self.stubs),
                mock.patch.object(plugin.uiUtils, "is_modal_dialog_open", return_value=True),
            ):
                self.assertFalse(plugin.uiUtils.open_synthesizer_dialog())
            self.assertEqual([fakeDialogCls], popupCalls)
        finally:
            if origPopup is None:
                with contextlib.suppress(AttributeError):
                    delattr(guiMod.mainFrame, "popupSettingsDialog")
            else:
                guiMod.mainFrame.popupSettingsDialog = origPopup
            if origSynthDlg is None:
                with contextlib.suppress(AttributeError):
                    delattr(guiMod.settingsDialogs, "SynthesizerSelectionDialog")
            else:
                guiMod.settingsDialogs.SynthesizerSelectionDialog = origSynthDlg

        # 2. Automatic update check skips notification if a modal dialog is already open.
        updateGuiMod = plugin.updateGui
        removedManifests: list[Any] = []
        promptCalls: list[Any] = []
        controller, _fakeUpdate, checkResult = self._make_update_check_fixture(
            updateGuiMod._CHECK_MODE_AUTOMATIC, version="9.9"
        )
        with (
            mock.patch.object(updateGuiMod, "is_windows_store_nvda", return_value=False),
            mock.patch.object(updateGuiMod, "is_modal_dialog_open", return_value=True),
            mock.patch.object(plugin.updater, "remove_update_manifest", side_effect=removedManifests.append),
            mock.patch.object(controller, "_show_update_available_dialog", side_effect=promptCalls.append),
            mock.patch.object(updateGuiMod, "_finish_update_check"),
        ):
            controller._finish_check(checkResult)
        self.assertEqual([checkResult], removedManifests)
        self.assertEqual([], promptCalls)

        # 3. VoiceManagerDialog: stale pending remove cleared on new check; dependent failure blocks base removal; non-vetoable on_close destroys.
        basePkg = self._make_voice_package(
            "fr-fr-x-multi",
            speakers=({"speaker": "fr1", "name": "French 1", "gender": "female"},),
        )
        depPkg = self._make_voice_package(
            "fr-fr-x-extra",
            file_id="fr-extra",
            sha="1" * 64,
            compressed_size=50,
            dependent_voice_id=basePkg.id,
        )
        vm = plugin.voiceManager.VoiceManagerDialog.__new__(plugin.voiceManager.VoiceManagerDialog)
        vm.installedList = None
        vm.installedPackages = [basePkg]
        vm._allUsableInstalledPackages = [basePkg]
        vm._pendingRemoveAfterSynthSwitch = [depPkg]
        vm._pendingRemoveTimer = None
        vm._closing = False
        vm._is_google_synth_current = lambda: False
        vm._checked_packages = lambda lst, pkgs: [basePkg]
        vm._with_installed_dependents = lambda pkgs: list(pkgs)
        vm._removes_all_usable_voices = lambda pkgs: True
        vm._dependents_first = lambda pkgs: list(pkgs)
        vm._confirm_remove_last_inactive_voice = lambda pkgs: True
        removedTargets: list[list[Any]] = []
        vm._remove_packages = lambda pkgs: removedTargets.append(list(pkgs))
        vm.on_remove_selected(None)  # type: ignore[arg-type]
        self.assertIsNone(vm._pendingRemoveAfterSynthSwitch)
        self.assertEqual([[basePkg]], removedTargets)

        # _remove_packages skips basePkg when depPkg failed removal.
        vmDeps = plugin.voiceManager.VoiceManagerDialog.__new__(plugin.voiceManager.VoiceManagerDialog)
        vmDeps._closing = False
        vmDeps.isBusy = False
        vmDeps._user_friendly_error_message = lambda exc: str(exc)
        vmDeps._refresh_buttons = lambda: None
        vmDeps._notify_synth_catalog_changed = lambda: None
        vmDeps.reload_catalog = lambda **kw: None
        vmDeps.set_status = lambda *a, **k: None
        vmDeps.installedPackages = [depPkg, basePkg]
        vmDeps._allUsableInstalledPackages = [basePkg]
        vmDeps.progressBar = SimpleNamespace(SetRange=lambda _n: None, SetValue=lambda _n: None)
        removedOrder: list[str] = []
        donePayload: list[Any] = []

        def fake_remove_pkg(pkg: Any) -> None:
            removedOrder.append(pkg.id)
            if pkg.id == depPkg.id:
                raise OSError("locked")

        vmDeps._run_worker = lambda work, done, _msg: donePayload.append(work())
        with (
            mock.patch.object(plugin.voiceManager.log, "error") as mockRemoveLogError,
            mock.patch.object(plugin.voiceManager.voice_store, "remove_package", side_effect=fake_remove_pkg),
        ):
            plugin.voiceManager.VoiceManagerDialog._remove_packages(vmDeps, [depPkg, basePkg])
        self.assertEqual([depPkg.id], removedOrder)
        self.assertEqual(0, donePayload[0]["succeeded"])
        self.assertEqual([depPkg.id, basePkg.id], [pid for pid, _msg in donePayload[0]["failed"]])
        self.assertEqual("locked", donePayload[0]["failed"][1][1])
        mockRemoveLogError.assert_any_call(
            "Cannot remove %s because dependent package %s could not be removed.",
            basePkg.id,
            depPkg.id,
        )

        supportedDepPkg = self._make_voice_package(
            "fr-fr-x-multi-lemonbalm",
            file_id="fr-lemonbalm",
            sha="2" * 64,
            compressed_size=50,
            dependent_voice_id=basePkg.id,
        )
        unsupportedDepPkg = self._make_voice_package(
            "fr-fr-x-multi-locomel",
            file_id="fr-locomel",
            sha="3" * 64,
            compressed_size=50,
            dependent_voice_id=basePkg.id,
        )
        vm._allInstalledPackages = [basePkg, supportedDepPkg, unsupportedDepPkg]
        self.assertEqual(
            [supportedDepPkg],
            plugin.voiceManager.VoiceManagerDialog._direct_installed_dependents(vm, basePkg),
        )

        # on_close with CanVeto() == False calls prepare_for_shutdown and Destroy() even while busy.
        vmClose = plugin.voiceManager.VoiceManagerDialog.__new__(plugin.voiceManager.VoiceManagerDialog)
        vmClose.isBusy = True
        closeEvents: list[str] = []
        vmClose.prepare_for_shutdown = lambda: closeEvents.append("shutdown")
        vmClose.Destroy = lambda: closeEvents.append("destroy")
        vmClose.on_close(SimpleNamespace(CanVeto=lambda: False, Veto=lambda: closeEvents.append("veto")))  # type: ignore[arg-type]
        self.assertEqual(["shutdown", "destroy"], closeEvents)

        # 4. Settings panel onPanelActivated refreshes changed speaker catalog while preserving edits.
        settingsMod = plugin.settingsModule
        panel = settingsMod.GoogleTtsSettingsPanel.__new__(settingsMod.GoogleTtsSettingsPanel)
        enSpeakerOld = SimpleNamespace(id="en-us:old", name="Old EN", language="en-US")
        enSpeakerNew = SimpleNamespace(id="en-us:new", name="New EN", language="en-US")
        frSpeaker = SimpleNamespace(id="fr-fr:fr1", name="French 1", language="fr-FR")
        panel._speakersByLanguage = {"en-US": [enSpeakerOld]}
        panel._languageValues = ["en-US"]
        panel._languageCounts = {"en-US": 1}
        panel._savedAutoLanguageCandidates = ["en-US"]
        panel._speechDefaults = {
            "voice": "en-us:old",
            "rate": 50,
            "rateBoost": False,
            "pitch": 50,
            "volume": 100,
            "capPitchChange": 30,
            "sayCapForCapitals": False,
            "beepForCapitals": False,
            "useSpellingFunctionality": True,
        }
        panel._autoLanguageProfiles = {"en-US": {"enabled": True, "voice": "en-us:old", "rate": 65}}
        panel._selectedAutoLanguageProfileIndex = 0
        panel._installed_speakers_by_language = lambda: {"en-US": [enSpeakerOld, enSpeakerNew], "fr-FR": [frSpeaker]}
        choicesAdded: list[str] = []
        selBox = [0]
        panel.autoProfileLanguageChoice = SimpleNamespace(
            Clear=lambda: choicesAdded.clear(),
            Append=lambda label: choicesAdded.append(label),
            SetSelection=lambda idx: selBox.__setitem__(0, idx),
            GetSelection=lambda: selBox[0],
        )
        panel._store_selected_auto_language_profile = lambda idx: None
        panel._selected_auto_language_profile = lambda idx: (
            panel._languageValues[idx] if 0 <= idx < len(panel._languageValues) else None
        )
        panel._ensure_auto_language_profiles = lambda: (
            settingsMod.GoogleTtsSettingsPanel._ensure_auto_language_profiles(panel)
        )
        panel._refresh_preferred_language_choices = lambda: None
        panel._load_selected_auto_language_profile = lambda: None
        panel._refresh_auto_language_controls = lambda: None
        panel._refresh_installed_catalog_in_panel()
        self.assertEqual(["en-US", "fr-FR"], panel._languageValues)
        self.assertEqual(65, panel._autoLanguageProfiles["en-US"]["rate"])

        # 5. _update_value_for_control refreshes voice choices too.
        plugin._patch_read_only_text_setting()
        try:
            autoMixin = self.stubs["gui.settingsDialogs"].AutoSettingsMixin
            vOld = SimpleNamespace(id="en-US", displayName="English")
            vNew = SimpleNamespace(id="vi-VN", displayName="Vietnamese")
            cleared: list[bool] = []
            appended: list[str] = []
            voicePanel = SimpleNamespace(
                _voices=[vOld],
                voiceList=SimpleNamespace(Clear=lambda: cleared.append(True), Append=lambda s: appended.append(s)),
            )
            voiceSetting = SimpleNamespace(id="voice")
            voiceStorage = SimpleNamespace(name=plugin.SYNTH_NAME, availableVoices={"en-US": vOld, "vi-VN": vNew})
            autoMixin._updateValueForControl(voicePanel, voiceSetting, voiceStorage)
            self.assertEqual([True], cleared)
            self.assertEqual(["English", "Vietnamese"], appended)
            self.assertEqual([vOld, vNew], voicePanel._voices)
        finally:
            plugin._unpatch_read_only_text_setting()

        # 6. Auto-language spelling sequence survives EndUtteranceCommand & returns from foreign char ("aあb").
        cmds = self.stubs["speech.commands"]
        fakeSpellSynth = SimpleNamespace(
            name=plugin.SYNTH_NAME,
            voice="en-US",
            availableVoices={"en-US": object(), "ja-JP": object()},
            _auto_language_detection_enabled=lambda: True,
            _auto_language_candidates=lambda: ["en-US", "ja-JP"],
            _auto_language_preferred=lambda _candidates, _base: "en-US",
        )
        with self._auto_profile_env(synth=fakeSpellSynth, patchHooks=False):
            seq = [
                plugin._GoogleTtsProfileLangCommand("en-US", spelling=True),
                cmds.CharacterModeCommand(True),
                "a",
                cmds.EndUtteranceCommand(),
                cmds.CharacterModeCommand(True),
                "あ",
                cmds.EndUtteranceCommand(),
                cmds.CharacterModeCommand(True),
                "b",
            ]
            out = plugin._filter_auto_language_speech_sequence(seq)
        langTags = [
            x.language if isinstance(x, plugin._GoogleTtsProfileLangCommand) else plugin._google_lang_change_language(x)
            for x in out
            if isinstance(x, (cmds.LangChangeCommand, plugin._GoogleTtsProfileLangCommand))
        ]
        self.assertEqual(["en-US", "ja-JP", "en-US"], langTags)


if __name__ == "__main__":
    unittest.main()
