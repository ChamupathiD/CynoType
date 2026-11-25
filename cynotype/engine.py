"""
Core typing engine, Smart IDE indentation tracker, and OS keystroke driver.
Shared by the desktop app (macOS / Windows) and the CynoType MCP server.
"""

import ctypes
import os
import random
import sys
import threading
import time
from typing import Callable, Optional, Protocol, Tuple

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
APP_NAME = "CynoType"
APP_VERSION = "2.1.0"

_MAC_KEYCODE_ESC = 53
_MAC_KEYCODE_F8 = 100


# ==============================================================================
# Indentation Analyzer (ported from chanul212/Auto-Typer v2)
# ==============================================================================
def analyze_indentation(raw_text: str) -> Tuple[int, Optional[bool], str]:
    """
    Analyze code indentation and return (spaces_per_tab, use_tab_key_or_none, message).
    """
    if not raw_text or not raw_text.strip():
        return 4, None, "Paste code first to detect indentation"

    lines = raw_text.splitlines()
    space_counts = {2: 0, 4: 0}
    tab_count = 0

    for line in lines:
        leading = line[: len(line) - len(line.lstrip(" \t"))]
        if not leading:
            continue
        if leading.startswith("\t"):
            tab_count += 1
        else:
            sp = len(leading)
            if sp % 4 == 0:
                space_counts[4] += 1
            elif sp % 2 == 0:
                space_counts[2] += 1

    if tab_count > (space_counts[2] + space_counts[4]):
        return 4, True, "Detected Tab indentation \u00B7 Tab key enabled"
    if space_counts[2] > space_counts[4]:
        return 2, None, "Detected 2-space indentation (JS / Web)"
    return 4, None, "Detected 4-space indentation (Python / Swift / Java)"


# ==============================================================================
# macOS Privacy Permissions & Quartz Global Emergency Hotkey Listener
# ==============================================================================
def mac_check_permissions():
    """Return (can_type, can_listen, framework_lib) for macOS privacy permissions."""
    try:
        lib = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        can_type = bool(lib.AXIsProcessTrusted())
        can_listen = True
        if hasattr(lib, "CGPreflightListenEventAccess"):
            lib.CGPreflightListenEventAccess.restype = ctypes.c_bool
            can_listen = bool(lib.CGPreflightListenEventAccess())
        return can_type, can_listen, lib
    except Exception:
        return True, True, None


class MacHotkeyListener(threading.Thread):
    """
    Global Esc / F8 listener for macOS using a Quartz event tap.
    Ignores synthetic events posted by CynoType itself (e.g. Esc to dismiss autocomplete).
    """

    def __init__(self, on_hotkey: Callable[[str], None]):
        super().__init__(daemon=True)
        self.on_hotkey = on_hotkey
        self._tap = None
        self._loop = None
        self.started_ok = threading.Event()
        self.failed = threading.Event()

    def run(self):
        try:
            import Quartz
            import CoreFoundation as CF
        except Exception:
            self.failed.set()
            return

        my_pid = os.getpid()

        def callback(proxy, event_type, event, refcon):
            try:
                if event_type in (
                    Quartz.kCGEventTapDisabledByTimeout,
                    Quartz.kCGEventTapDisabledByUserInput,
                ):
                    if self._tap is not None:
                        Quartz.CGEventTapEnable(self._tap, True)
                    return event
                if event_type == Quartz.kCGEventKeyDown:
                    try:
                        pid = Quartz.CGEventGetIntegerValueField(
                            event, Quartz.kCGEventSourceUnixProcessID
                        )
                        if pid == my_pid:
                            return event
                    except Exception:
                        pass
                    code = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    if code == _MAC_KEYCODE_ESC:
                        self.on_hotkey("ESC")
                    elif code == _MAC_KEYCODE_F8:
                        self.on_hotkey("F8")
            except Exception:
                pass
            return event

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if not self._tap:
            self.failed.set()
            return

        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = CF.CFRunLoopGetCurrent()
        CF.CFRunLoopAddSource(self._loop, source, CF.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self.started_ok.set()
        CF.CFRunLoopRun()

    def stop(self):
        try:
            import CoreFoundation as CF

            if self._loop is not None:
                CF.CFRunLoopStop(self._loop)
        except Exception:
            pass


# ==============================================================================
# Pluggable Keystroke Driver Protocol + OS Driver
# ==============================================================================
class KeystrokeDriver(Protocol):
    def press_enter(self) -> None: ...
    def press_tab(self) -> None: ...
    def press_backspace(self) -> None: ...
    def press_escape(self) -> None: ...
    def press_space(self) -> None: ...
    def type_char(self, char: str) -> None: ...
    def release_all_modifiers(self) -> None: ...
    def cleanup(self) -> None: ...


class OSKeystrokeDriver:
    """OS-level keystroke driver backed by pynput with clipboard fallback."""

    def __init__(self):
        from pynput.keyboard import Controller, Key

        self._Key = Key
        # Must be instantiated on the main thread on macOS
        self.keyboard = Controller()
        self._saved_clipboard: Optional[str] = None
        self._used_clipboard_fallback = False

    def press_enter(self) -> None:
        self.keyboard.press(self._Key.enter)
        self.keyboard.release(self._Key.enter)

    def press_tab(self) -> None:
        self.keyboard.press(self._Key.tab)
        self.keyboard.release(self._Key.tab)

    def press_backspace(self) -> None:
        self.keyboard.press(self._Key.backspace)
        self.keyboard.release(self._Key.backspace)

    def press_escape(self) -> None:
        self.keyboard.press(self._Key.esc)
        self.keyboard.release(self._Key.esc)

    def press_space(self) -> None:
        self.keyboard.press(" ")
        self.keyboard.release(" ")

    def type_char(self, char: str) -> None:
        if char == "\r":
            return
        if char == "\n":
            self.press_enter()
            return
        if char == "\t":
            self.press_tab()
            return
        try:
            self.keyboard.type(char)
        except Exception:
            self._paste_character(char)

    def _paste_character(self, char: str) -> None:
        try:
            import pyperclip

            if not self._used_clipboard_fallback:
                try:
                    self._saved_clipboard = pyperclip.paste()
                except Exception:
                    self._saved_clipboard = None
                self._used_clipboard_fallback = True
            pyperclip.copy(char)
            time.sleep(0.02)
            mod = self._Key.cmd if IS_MAC else self._Key.ctrl
            with self.keyboard.pressed(mod):
                self.keyboard.press("v")
                self.keyboard.release("v")
            time.sleep(0.02)
        except Exception as err:
            print(f"[Fallback Paste Warning] Could not paste '{char}': {err}")

    def release_all_modifiers(self) -> None:
        for mod in (self._Key.ctrl, self._Key.shift, self._Key.alt, self._Key.cmd):
            try:
                self.keyboard.release(mod)
            except Exception:
                pass

    def cleanup(self) -> None:
        if self._used_clipboard_fallback and self._saved_clipboard is not None:
            try:
                import pyperclip

                pyperclip.copy(self._saved_clipboard)
            except Exception:
                pass
        self._used_clipboard_fallback = False
        self._saved_clipboard = None


# ==============================================================================
# Core Typing Engine (Smart IDE Indentation + Verbatim Mode)
# ==============================================================================
class TypingEngine:
    """Thread-safe keystroke simulator supporting Smart IDE Mode and Plain Text Mode."""

    def __init__(
        self,
        on_status: Callable[[str, bool], None],
        on_progress: Callable[[float, str], None],
        on_finished: Callable[[int, bool], None],
        on_stopped: Callable[[str, bool], None],
        on_minimize: Callable[[], None],
        driver: Optional[KeystrokeDriver] = None,
    ):
        self.driver: KeystrokeDriver = driver if driver is not None else OSKeystrokeDriver()
        self.on_status = on_status
        self.on_progress = on_progress
        self.on_finished = on_finished
        self.on_stopped = on_stopped
        self.on_minimize = on_minimize

        self.is_running = False
        self.stop_event = threading.Event()
        self.stop_reason: Optional[str] = None
        self.typing_thread: Optional[threading.Thread] = None
        self._ignore_esc_until = 0.0

    def should_ignore_esc(self) -> bool:
        return time.monotonic() < self._ignore_esc_until

    def start(
        self,
        text: str,
        base_delay_ms: float = 45.0,
        randomize: bool = True,
        start_delay: int = 5,
        auto_minimize: bool = True,
        code_mode: bool = True,
        dismiss_autocomplete: bool = True,
        use_tab_key: bool = True,
        spaces_per_tab: int = 4,
    ) -> bool:
        if self.is_running or not text:
            return False

        base_delay = max(0.005, float(base_delay_ms) / 1000.0)
        start_delay = max(0, min(30, int(start_delay)))
        spaces_per_tab = max(1, min(16, int(spaces_per_tab)))

        self.is_running = True
        self.stop_event.clear()
        self.stop_reason = None

        self.typing_thread = threading.Thread(
            target=self._typing_worker,
            args=(
                text,
                base_delay,
                bool(randomize),
                start_delay,
                bool(auto_minimize),
                bool(code_mode),
                bool(dismiss_autocomplete),
                bool(use_tab_key),
                spaces_per_tab,
            ),
            daemon=True,
        )
        self.typing_thread.start()
        return True

    def stop(self, source: str = "User"):
        if self.is_running:
            self.stop_reason = source
            self.stop_event.set()
            self.driver.release_all_modifiers()

    def handle_emergency_hotkey(self, key_name: str):
        if key_name == "ESC" and self.should_ignore_esc():
            return
        if self.is_running:
            self.stop(f"Hotkey ({key_name})")

    @staticmethod
    def _measure_indent_spaces(line: str, spaces_per_tab: int) -> int:
        """Measure leading indentation of a line in equivalent spaces."""
        leading_len = len(line) - len(line.lstrip(" \t"))
        leading = line[:leading_len]
        total = 0
        for ch in leading:
            if ch == "\t":
                total += spaces_per_tab
            elif ch == " ":
                total += 1
        return total

    @staticmethod
    def _is_block_opener(line_text: str) -> bool:
        """
        Check if a code line triggers auto-indent on newline in modern IDEs.
        Handles Python (':'), C/C++/Java/JS/TS/Swift/Rust/Go ('{', '(', '[').
        Safely ignores inline '#' and '//' comments outside string literals.
        """
        stripped = line_text.strip()
        if not stripped:
            return False

        for marker in ("#", "//"):
            if marker in stripped:
                parts = stripped.split(marker, 1)
                left = parts[0].rstrip()
                if left:
                    if left.count('"') % 2 == 0 and left.count("'") % 2 == 0:
                        stripped = left

        stripped = stripped.rstrip()
        return stripped.endswith((":", "{", "(", "["))

    def _type_indent_diff(
        self,
        diff: int,
        spaces_per_tab: int,
        use_tab_key: bool,
        base_delay: float,
    ) -> int:
        """Type extra indentation tabs or spaces when delta > 0."""
        typed = 0
        if diff <= 0:
            return typed

        step_delay = max(0.015, base_delay * 0.7)
        if use_tab_key and spaces_per_tab > 0:
            tabs = diff // spaces_per_tab
            rem_spaces = diff % spaces_per_tab
            for _ in range(tabs):
                if self.stop_event.is_set():
                    return typed
                self.driver.press_tab()
                typed += spaces_per_tab
                if self.stop_event.wait(step_delay):
                    return typed
            for _ in range(rem_spaces):
                if self.stop_event.is_set():
                    return typed
                self.driver.press_space()
                typed += 1
                if self.stop_event.wait(step_delay):
                    return typed
        else:
            for _ in range(diff):
                if self.stop_event.is_set():
                    return typed
                self.driver.press_space()
                typed += 1
                if self.stop_event.wait(step_delay):
                    return typed

        return typed

    def _emit_stopped(self, reason: str, auto_minimize: bool):
        self.driver.cleanup()
        self.is_running = False
        self.on_stopped(reason, auto_minimize)

    def _typing_worker(
        self,
        text: str,
        base_delay: float,
        randomize: bool,
        start_delay: int,
        auto_minimize: bool,
        code_mode: bool,
        dismiss_autocomplete: bool,
        use_tab_key: bool,
        spaces_per_tab: int,
    ):
        clean_text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = clean_text.split("\n")
        total_lines = len(lines)
        total_chars = max(1, len(clean_text))
        typed_count = 0
        update_interval = 1 if total_chars < 300 else max(1, total_chars // 120)

        if auto_minimize:
            self.on_minimize()

        # 1. Countdown phase
        for remaining in range(start_delay, 0, -1):
            if self.stop_event.is_set():
                self._emit_stopped(
                    self.stop_reason or "Stopped during countdown", auto_minimize
                )
                return
            self.on_status(
                f"Starting in {remaining}s \u00B7 Click your target window", True
            )
            if self.stop_event.wait(1.0):
                self._emit_stopped(
                    self.stop_reason or "Stopped during countdown", auto_minimize
                )
                return

        if self.stop_event.is_set():
            self._emit_stopped(
                self.stop_reason or "Stopped before typing", auto_minimize
            )
            return

        # 2. Typing phase
        self.on_progress(0.0, f"Typing line 1 of {total_lines} \u00B7 0%")

        if not code_mode:
            # Plain Text / Verbatim Mode
            for line_idx, line in enumerate(lines):
                if self.stop_event.is_set():
                    self._emit_stopped(
                        self.stop_reason
                        or f"Stopped at line {line_idx + 1} of {total_lines}",
                        auto_minimize,
                    )
                    return

                if line_idx > 0:
                    self.driver.press_enter()
                    typed_count += 1
                    enter_pause = max(0.05, base_delay * 1.3)
                    if randomize:
                        enter_pause += random.uniform(0.02, 0.08)
                    if self.stop_event.wait(enter_pause):
                        self._emit_stopped(
                            self.stop_reason
                            or f"Stopped at line {line_idx + 1} of {total_lines}",
                            auto_minimize,
                        )
                        return

                for char in line:
                    if self.stop_event.is_set():
                        self._emit_stopped(
                            self.stop_reason
                            or f"Stopped at line {line_idx + 1} of {total_lines}",
                            auto_minimize,
                        )
                        return

                    self.driver.type_char(char)
                    typed_count += 1
                    delay = self.calculate_delay(char, base_delay, randomize)

                    if typed_count % update_interval == 0 or typed_count >= total_chars:
                        pct = min(100.0, (typed_count / total_chars) * 100.0)
                        self.on_progress(
                            pct,
                            f"Typing line {line_idx + 1} of {total_lines} \u00B7 {pct:.0f}%",
                        )

                    if self.stop_event.wait(delay):
                        self._emit_stopped(
                            self.stop_reason
                            or f"Stopped at line {line_idx + 1} of {total_lines}",
                            auto_minimize,
                        )
                        return
        else:
            # Code Mode: Smart IDE Indentation Tracking
            current_indent = 0
            last_block_opener = False

            for line_idx, line in enumerate(lines):
                if self.stop_event.is_set():
                    self._emit_stopped(
                        self.stop_reason
                        or f"Stopped at line {line_idx + 1} of {total_lines}",
                        auto_minimize,
                    )
                    return

                if line_idx > 0:
                    if dismiss_autocomplete:
                        self._ignore_esc_until = time.monotonic() + 0.25
                        self.driver.press_escape()
                        time.sleep(0.015)

                    self.driver.press_enter()
                    typed_count += 1

                    enter_pause = max(0.06, base_delay * 1.3)
                    if randomize:
                        enter_pause += random.uniform(0.02, 0.08)

                    if self.stop_event.wait(enter_pause):
                        self._emit_stopped(
                            self.stop_reason
                            or f"Stopped at line {line_idx + 1} of {total_lines}",
                            auto_minimize,
                        )
                        return

                    if last_block_opener:
                        current_indent += spaces_per_tab
                        last_block_opener = False

                if not line:
                    continue

                target_indent = self._measure_indent_spaces(line, spaces_per_tab)
                content = line.lstrip(" \t")
                delta = target_indent - current_indent

                if delta > 0:
                    typed_count += self._type_indent_diff(
                        delta, spaces_per_tab, use_tab_key, base_delay
                    )
                    current_indent = target_indent
                elif delta < 0:
                    unindent = abs(delta)
                    num_backspaces = max(
                        1, (unindent + spaces_per_tab - 1) // spaces_per_tab
                    )
                    backspace_delay = max(0.02, base_delay * 0.6)
                    for _ in range(num_backspaces):
                        if self.stop_event.is_set():
                            self._emit_stopped(
                                self.stop_reason
                                or f"Stopped at line {line_idx + 1} of {total_lines}",
                                auto_minimize,
                            )
                            return
                        self.driver.press_backspace()
                        if self.stop_event.wait(backspace_delay):
                            self._emit_stopped(
                                self.stop_reason
                                or f"Stopped at line {line_idx + 1} of {total_lines}",
                                auto_minimize,
                            )
                            return
                    current_indent = target_indent

                for char in content:
                    if self.stop_event.is_set():
                        self._emit_stopped(
                            self.stop_reason
                            or f"Stopped at line {line_idx + 1} of {total_lines}",
                            auto_minimize,
                        )
                        return

                    self.driver.type_char(char)
                    typed_count += 1
                    delay = self.calculate_delay(char, base_delay, randomize)

                    if typed_count % update_interval == 0 or typed_count >= total_chars:
                        pct = min(100.0, (typed_count / total_chars) * 100.0)
                        self.on_progress(
                            pct,
                            f"Typing line {line_idx + 1} of {total_lines} \u00B7 {pct:.0f}%",
                        )

                    if self.stop_event.wait(delay):
                        self._emit_stopped(
                            self.stop_reason
                            or f"Stopped at line {line_idx + 1} of {total_lines}",
                            auto_minimize,
                        )
                        return

                last_block_opener = self._is_block_opener(line)

        # 3. Finished cleanly
        self.driver.cleanup()
        self.is_running = False
        self.on_finished(len(clean_text), auto_minimize)

    @staticmethod
    def calculate_delay(char: str, base_delay: float, randomize: bool) -> float:
        if not randomize:
            return base_delay

        jitter = random.gauss(0, base_delay * 0.25)
        delay = max(0.005, base_delay + jitter)

        if char in (".", "!", "?"):
            delay += random.uniform(0.14, 0.30)
        elif char in (",", ";", ":", "\u2014", "-"):
            delay += random.uniform(0.06, 0.16)
        elif char == "\n":
            delay += random.uniform(0.16, 0.35)
        elif char == " ":
            delay += random.uniform(0.01, 0.04)
        elif random.random() < 0.03:
            delay += random.uniform(0.08, 0.22)

        return delay
