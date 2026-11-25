"""
================================================================================
CynoType (Auto Typer) — Natural Human & Smart IDE Typing Simulator
Native macOS Apple Design Science UI + Cross-Platform Engine.
================================================================================

Features:
- Smart IDE Mode (from chanul212/Auto-Typer v2): tracks cursor indentation across
  lines in VS Code, Cursor, PyCharm, Xcode, and online IDEs — prevents double
  indentation without ever collapsing or deleting lines.
- Plain Text Mode: verbatim line-by-line typing preserving exact input.
- Auto-Detect Indentation: inspects pasted code for Tabs, 2-space, or 4-space style.
- Clean 2D code editor with no-wrap default, wrap toggle, Tab / Shift+Tab line
  indenting, dynamic tab stops, and true-to-source clipboard paste.
- Natural human typing rhythm with Gaussian jitter and punctuation pauses.
- Configurable start countdown, window auto-minimize & auto-restore, and Reset All.
- Global emergency stop (Esc or F8) via macOS Quartz Event Tap (ignores simulated
  Esc keys sent when dismissing IDE autocomplete popups).
- Native macOS Cocoa window with vibrancy, translucent glass panels, and CynoType icon.
================================================================================
"""

import base64
import json
import os
import queue
import subprocess
import sys
import time

try:
    import pyperclip
    from pynput import keyboard
    from pynput.keyboard import Key
except ImportError as _err:
    print(f"Missing library: {_err}\nRun:  pip install -r requirements.txt")
    sys.exit(1)

from cynotype.engine import (
    APP_NAME,
    APP_VERSION,
    IS_MAC,
    IS_WIN,
    MacHotkeyListener,
    TypingEngine,
    analyze_indentation,
    mac_check_permissions,
)


def resource_path(rel_path: str) -> str:
    """Resolve an asset path both from source and inside a PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


def build_ui_html(platform_class: str) -> str:
    """Load assets/ui.html and inline the CynoType brand mark and platform class."""
    html_path = resource_path("assets/ui.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    logo_data_uri = ""
    mark_path = resource_path("assets/logo_mark.png")
    if os.path.exists(mark_path):
        with open(mark_path, "rb") as img_f:
            b64 = base64.b64encode(img_f.read()).decode("ascii")
            logo_data_uri = f"data:image/png;base64,{b64}"

    return html.replace("__LOGO_MARK_DATA__", logo_data_uri).replace(
        "__PLATFORM_CLASS__", platform_class
    )


# ==============================================================================
# Native macOS Cocoa + Vibrancy + WKWebView Application
# ==============================================================================
def run_macos_app():
    import objc
    import AppKit
    import Foundation
    from PyObjCTools import AppHelper

    # Load Apple's built-in WebKit framework dynamically (no extra pip dependency!)
    wk_ns = {}
    objc.loadBundle(
        "WebKit",
        wk_ns,
        bundle_path="/System/Library/Frameworks/WebKit.framework",
    )
    WKWebView = wk_ns["WKWebView"]
    WKWebViewConfiguration = wk_ns["WKWebViewConfiguration"]

    class TitlebarDragView(AppKit.NSView):
        """Invisible top titlebar overlay so the seamless Liquid Glass window drags natively."""

        def mouseDownCanMoveWindow(self):
            return True

    class CynoAppDelegate(AppKit.NSObject):
        def init(self):
            self = objc.super(CynoAppDelegate, self).init()
            if self is None:
                return None
            self.window = None
            self.webview = None
            self.engine = None
            self.hotkey_listener = None
            return self

        def _js(self, call_expr: str):
            def _run():
                if self.webview is not None:
                    self.webview.evaluateJavaScript_completionHandler_(call_expr, None)

            AppHelper.callAfter(_run)

        def _setup_menu_bar(self):
            menubar = AppKit.NSMenu.alloc().init()

            # Application menu
            app_menu_item = AppKit.NSMenuItem.alloc().init()
            menubar.addItem_(app_menu_item)
            app_menu = AppKit.NSMenu.alloc().initWithTitle_(APP_NAME)
            app_menu.addItemWithTitle_action_keyEquivalent_(
                f"About {APP_NAME}", "orderFrontStandardAboutPanel:", ""
            )
            app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
            app_menu.addItemWithTitle_action_keyEquivalent_(
                f"Hide {APP_NAME}", "hide:", "h"
            )
            app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
            app_menu.addItemWithTitle_action_keyEquivalent_(
                f"Quit {APP_NAME}", "terminate:", "q"
            )
            app_menu_item.setSubmenu_(app_menu)

            # Edit menu (enables native Cmd+Z / Cmd+X / Cmd+C / Cmd+V / Cmd+A in WKWebView)
            edit_menu_item = AppKit.NSMenuItem.alloc().init()
            menubar.addItem_(edit_menu_item)
            edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
            edit_menu.addItemWithTitle_action_keyEquivalent_("Undo", "undo:", "z")
            edit_menu.addItemWithTitle_action_keyEquivalent_("Redo", "redo:", "Z")
            edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
            edit_menu.addItemWithTitle_action_keyEquivalent_("Cut", "cut:", "x")
            edit_menu.addItemWithTitle_action_keyEquivalent_("Copy", "copy:", "c")
            edit_menu.addItemWithTitle_action_keyEquivalent_("Paste", "paste:", "v")
            edit_menu.addItemWithTitle_action_keyEquivalent_(
                "Select All", "selectAll:", "a"
            )
            edit_menu_item.setSubmenu_(edit_menu)

            # Window menu
            win_menu_item = AppKit.NSMenuItem.alloc().init()
            menubar.addItem_(win_menu_item)
            win_menu = AppKit.NSMenu.alloc().initWithTitle_("Window")
            win_menu.addItemWithTitle_action_keyEquivalent_(
                "Minimize", "performMiniaturize:", "m"
            )
            win_menu.addItemWithTitle_action_keyEquivalent_(
                "Close", "performClose:", "w"
            )
            win_menu_item.setSubmenu_(win_menu)

            AppKit.NSApp.setMainMenu_(menubar)

        def _load_app_icon(self):
            for candidate in ("assets/CynoType.icns", "assets/logo.png"):
                p = resource_path(candidate)
                if os.path.exists(p):
                    img = AppKit.NSImage.alloc().initWithContentsOfFile_(p)
                    if img is not None:
                        AppKit.NSApp.setApplicationIconImage_(img)
                        return

        def _build_html(self) -> str:
            return build_ui_html("platform-mac")

        def applicationDidFinishLaunching_(self, _notification):
            self._setup_menu_bar()
            self._load_app_icon()

            # Initialize TypingEngine on main thread
            self.engine = TypingEngine(
                on_status=self._on_engine_status,
                on_progress=self._on_engine_progress,
                on_finished=self._on_engine_finished,
                on_stopped=self._on_engine_stopped,
                on_minimize=self._on_engine_minimize,
            )

            # Start Quartz global Esc / F8 listener
            listener = MacHotkeyListener(self.engine.handle_emergency_hotkey)
            listener.start()
            for _ in range(15):
                if listener.started_ok.is_set() or listener.failed.is_set():
                    break
                time.sleep(0.02)
            self.hotkey_listener = listener

            # Build sleek native macOS window
            width, height = 820.0, 776.0
            titlebar_strip = 32.0
            style = (
                AppKit.NSWindowStyleMaskTitled
                | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable
                | AppKit.NSWindowStyleMaskResizable
                | AppKit.NSWindowStyleMaskFullSizeContentView
            )
            self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                Foundation.NSMakeRect(0, 0, width, height),
                style,
                AppKit.NSBackingStoreBuffered,
                False,
            )
            self.window.setTitle_(APP_NAME)
            self.window.setAppearance_(
                AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameAqua)
            )
            self.window.setOpaque_(False)
            self.window.setBackgroundColor_(AppKit.NSColor.clearColor())
            self.window.setTitlebarAppearsTransparent_(True)
            self.window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            self.window.setMovableByWindowBackground_(True)
            self.window.setMinSize_(Foundation.NSMakeSize(700.0, 620.0))
            self.window.setDelegate_(self)

            # Native macOS Liquid Glass vibrancy backdrop (blurs wallpaper/windows behind app)
            vev = AppKit.NSVisualEffectView.alloc().initWithFrame_(
                Foundation.NSMakeRect(0, 0, width, height)
            )
            vev.setMaterial_(AppKit.NSVisualEffectMaterialPopover)
            vev.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            vev.setState_(AppKit.NSVisualEffectStateActive)
            vev.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
            )
            self.window.setContentView_(vev)

            # Full-bleed WKWebView so Liquid Glass flows seamlessly behind macOS traffic lights
            cfg = WKWebViewConfiguration.alloc().init()
            cfg.userContentController().addScriptMessageHandler_name_(self, "bridge")

            self.webview = WKWebView.alloc().initWithFrame_configuration_(
                Foundation.NSMakeRect(0, 0, width, height), cfg
            )
            self.webview.setValue_forKey_(False, "drawsBackground")
            self.webview.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
            )
            vev.addSubview_(self.webview)
            self.webview.loadHTMLString_baseURL_(self._build_html(), None)

            # Transparent native drag strip across the top 32px titlebar
            drag_strip = TitlebarDragView.alloc().initWithFrame_(
                Foundation.NSMakeRect(0, height - titlebar_strip, width, titlebar_strip)
            )
            drag_strip.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
            )
            vev.addSubview_(drag_strip)

            self.window.center()
            self.window.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)

        def applicationShouldTerminateAfterLastWindowClosed_(self, _sender):
            return True

        def applicationWillTerminate_(self, _notification):
            if self.engine is not None and self.engine.is_running:
                self.engine.stop("Application quit")
            if self.hotkey_listener is not None:
                self.hotkey_listener.stop()

        # ------------------------------------------------------------------
        # Engine -> UI callbacks
        # ------------------------------------------------------------------
        def _on_engine_status(self, msg: str, is_active: bool):
            self._js(
                f"window.CynoBridge.onStatus({json.dumps(msg)}, {json.dumps(bool(is_active))})"
            )

        def _on_engine_progress(self, pct: float, msg: str):
            self._js(f"window.CynoBridge.onProgress({pct:.2f}, {json.dumps(msg)})")

        def _on_engine_minimize(self):
            def _do():
                if self.window is not None:
                    self.window.miniaturize_(None)

            AppHelper.callAfter(_do)

        def _restore_window(self):
            def _do():
                if self.window is not None:
                    if self.window.isMiniaturized():
                        self.window.deminiaturize_(None)
                    self.window.makeKeyAndOrderFront_(None)
                    AppKit.NSApp.activateIgnoringOtherApps_(True)

            AppHelper.callAfter(_do)

        def _on_engine_finished(self, total_chars: int, auto_minimize: bool):
            self._js(f"window.CynoBridge.onFinished({int(total_chars)})")
            if auto_minimize:
                self._restore_window()

        def _on_engine_stopped(self, reason: str, auto_minimize: bool):
            self._js(f"window.CynoBridge.onStopped({json.dumps(reason)})")
            if auto_minimize:
                self._restore_window()

        # ------------------------------------------------------------------
        # macOS permission helpers
        # ------------------------------------------------------------------
        def _refresh_permissions(self, prompt_if_missing: bool = False):
            can_type, can_listen, lib = mac_check_permissions()
            listener_failed = (
                isinstance(self.hotkey_listener, MacHotkeyListener)
                and self.hotkey_listener.failed.is_set()
            )
            effective_listen = can_listen and not listener_failed

            if prompt_if_missing and lib is not None:
                try:
                    if not can_type and hasattr(lib, "CGRequestPostEventAccess"):
                        lib.CGRequestPostEventAccess()
                    if not effective_listen and hasattr(
                        lib, "CGRequestListenEventAccess"
                    ):
                        lib.CGRequestListenEventAccess()
                except Exception:
                    pass

            if can_type and effective_listen:
                self._js("window.CynoBridge.onPermissions(true, true, '', '')")
                return True, True

            if not can_type and not effective_listen:
                desc = "Enable CynoType in Accessibility & Input Monitoring, then reopen."
                pane = "Privacy_Accessibility"
            elif not can_type:
                desc = "Enable CynoType in Accessibility so it can simulate keystrokes."
                pane = "Privacy_Accessibility"
            else:
                desc = "Enable CynoType in Input Monitoring for global Esc / F8 stop."
                pane = "Privacy_ListenEvent"

            self._js(
                f"window.CynoBridge.onPermissions("
                f"{json.dumps(can_type)}, {json.dumps(effective_listen)}, "
                f"{json.dumps(desc)}, {json.dumps(pane)})"
            )
            return can_type, effective_listen

        # ------------------------------------------------------------------
        # JS -> Python bridge
        # ------------------------------------------------------------------
        def userContentController_didReceiveScriptMessage_(self, _controller, message):
            body = message.body()
            if not body:
                return
            action = str(body.get("action", ""))

            if action == "ui_ready":
                self._refresh_permissions(prompt_if_missing=False)

            elif action == "set_theme":
                theme = str(body.get("theme", "light"))
                if self.window is not None:
                    name = (
                        AppKit.NSAppearanceNameDarkAqua
                        if theme == "dark"
                        else AppKit.NSAppearanceNameAqua
                    )
                    self.window.setAppearance_(
                        AppKit.NSAppearance.appearanceNamed_(name)
                    )

            elif action == "check_permissions":
                self._refresh_permissions(prompt_if_missing=True)

            elif action == "open_permissions":
                pane = str(body.get("pane", "Privacy_Accessibility"))
                self._refresh_permissions(prompt_if_missing=True)
                try:
                    subprocess.Popen(
                        [
                            "open",
                            f"x-apple.systempreferences:com.apple.preference.security?{pane}",
                        ]
                    )
                except Exception:
                    pass

            elif action == "request_paste":
                try:
                    pb = AppKit.NSPasteboard.generalPasteboard()
                    content = pb.stringForType_(AppKit.NSPasteboardTypeString)
                    if content is None:
                        content = pyperclip.paste()
                    if content:
                        self._js(
                            f"window.CynoBridge.onPasteText({json.dumps(str(content))})"
                        )
                except Exception as err:
                    print(f"[Paste Warning] {err}")

            elif action == "auto_detect":
                text = str(body.get("text", ""))
                silent = bool(body.get("silent", False))
                spaces, use_tab, msg = analyze_indentation(text)
                self._js(
                    f"window.CynoBridge.onAutoDetectResult("
                    f"{int(spaces)}, {json.dumps(use_tab)}, {json.dumps(msg)}, {json.dumps(silent)})"
                )

            elif action == "request_reset":
                has_text = bool(body.get("has_text", False))
                if has_text:
                    alert = AppKit.NSAlert.alloc().init()
                    alert.setMessageText_("Reset CynoType?")
                    alert.setInformativeText_(
                        "This clears the editor and restores all speed and indentation settings to their defaults."
                    )
                    alert.addButtonWithTitle_("Reset")
                    alert.addButtonWithTitle_("Cancel")
                    alert.setAlertStyle_(AppKit.NSAlertStyleInformational)
                    if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                        return
                if self.engine.is_running:
                    self.engine.stop("Reset")
                self._js("window.CynoBridge.onPerformReset()")

            elif action == "start":
                can_type, _ = self._refresh_permissions(prompt_if_missing=True)
                if not can_type:
                    self._on_engine_status(
                        "Grant Accessibility permission in System Settings, then reopen",
                        False,
                    )
                    return

                started = self.engine.start(
                    text=str(body.get("text", "")),
                    base_delay_ms=float(body.get("base_delay_ms", 45)),
                    randomize=bool(body.get("randomize", True)),
                    start_delay=int(body.get("start_delay", 5)),
                    auto_minimize=bool(body.get("auto_minimize", True)),
                    code_mode=bool(body.get("code_mode", True)),
                    dismiss_autocomplete=bool(body.get("dismiss_autocomplete", True)),
                    use_tab_key=bool(body.get("use_tab_key", True)),
                    spaces_per_tab=int(body.get("spaces_per_tab", 4)),
                )
                if started:
                    self._js("window.CynoBridge.onStarted()")

            elif action == "stop":
                source = str(body.get("source", "Stop button"))
                self.engine.stop(source)

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    delegate = CynoAppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


# ==============================================================================
# Cross-Platform Fallback UI (Windows / Linux) using Shared TypingEngine
# ==============================================================================
def run_tk_fallback_app():
    import tkinter as tk
    from tkinter import ttk, messagebox, font as tkfont

    root = tk.Tk()
    root.title(f"{APP_NAME} — Human Auto Typer")
    root.geometry("820x740")
    root.minsize(640, 560)
    root.configure(bg="#F5F5F7")

    msg_queue: queue.Queue = queue.Queue()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    elif "clam" in style.theme_names():
        style.theme_use("clam")

    ui_font = "Segoe UI" if IS_WIN else "DejaVu Sans"
    mono_font = "Consolas" if IS_WIN else "DejaVu Sans Mono"
    style.configure(".", font=(ui_font, 10), background="#F5F5F7", foreground="#1D1D1F")
    style.configure("TFrame", background="#F5F5F7")
    style.configure("Card.TFrame", background="#FFFFFF")
    style.configure("TLabel", background="#F5F5F7", foreground="#1D1D1F")
    style.configure("Sub.TLabel", background="#F5F5F7", foreground="#6E6E73", font=(ui_font, 9))
    style.configure("TCheckbutton", background="#F5F5F7", foreground="#1D1D1F")

    container = ttk.Frame(root, padding=24)
    container.pack(fill=tk.BOTH, expand=True)

    # Header
    hdr = ttk.Frame(container)
    hdr.pack(fill=tk.X, pady=(0, 16))
    ttk.Label(hdr, text=APP_NAME, font=(ui_font, 18, "bold")).pack(side=tk.LEFT)
    lbl_status = ttk.Label(hdr, text="Ready to type", style="Sub.TLabel")
    lbl_status.pack(side=tk.LEFT, padx=(16, 0), pady=(6, 0))

    code_mode_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(hdr, text="Code Mode (Smart IDE)", variable=code_mode_var).pack(side=tk.RIGHT)

    # Editor toolbar
    bar = ttk.Frame(container)
    bar.pack(fill=tk.X, pady=(0, 8))
    lbl_stats = ttk.Label(bar, text="0 lines · 0 characters", style="Sub.TLabel")
    lbl_stats.pack(side=tk.LEFT)

    # Text area
    text_wrap = ttk.Frame(container)
    text_wrap.pack(fill=tk.BOTH, expand=True, pady=(0, 16))
    v_scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL)
    v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    h_scroll = ttk.Scrollbar(text_wrap, orient=tk.HORIZONTAL)
    h_scroll.pack(side=tk.BOTTOM, fill=tk.X)

    code_font = tkfont.Font(family=mono_font, size=10)
    text_area = tk.Text(
        text_wrap,
        wrap=tk.NONE,
        undo=True,
        font=code_font,
        tabs=(code_font.measure("    "),),
        bg="#FFFFFF",
        fg="#1D1D1F",
        relief=tk.FLAT,
        highlightthickness=1,
        highlightbackground="#D2D2D7",
        highlightcolor="#0071E3",
        padx=12,
        pady=12,
        yscrollcommand=v_scroll.set,
        xscrollcommand=h_scroll.set,
    )
    text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    v_scroll.config(command=text_area.yview)
    h_scroll.config(command=text_area.xview)

    def update_stats(_e=None):
        raw = text_area.get("1.0", "end-1c")
        lines = len(raw.split("\n")) if raw else 0
        lbl_stats.config(text=f"{lines:,} lines \u00B7 {len(raw):,} characters")

    text_area.bind("<KeyRelease>", update_stats)

    # Settings
    opts = ttk.Frame(container)
    opts.pack(fill=tk.X, pady=(0, 16))

    speed_row = ttk.Frame(opts)
    speed_row.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(speed_row, text="Typing Speed:").pack(side=tk.LEFT)
    lbl_speed = ttk.Label(speed_row, text="45 ms", style="Sub.TLabel")
    lbl_speed.pack(side=tk.RIGHT)

    def on_speed(v):
        ms = int(round(float(v)))
        wpm = int((1000.0 / ms) * 60 / 5) if ms > 0 else 0
        lbl_speed.config(text=f"{ms} ms \u00B7 ~{wpm} WPM")

    slider = ttk.Scale(opts, from_=10, to=250, value=45, orient=tk.HORIZONTAL, command=on_speed)
    slider.pack(fill=tk.X, pady=(0, 10))
    on_speed(45)

    row2 = ttk.Frame(opts)
    row2.pack(fill=tk.X)
    humanize_var = tk.BooleanVar(value=True)
    minimize_var = tk.BooleanVar(value=True)
    use_tab_var = tk.BooleanVar(value=True)
    dismiss_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(row2, text="Natural rhythm", variable=humanize_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(row2, text="Minimize", variable=minimize_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(row2, text="Tab key indent", variable=use_tab_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(row2, text="Dismiss autocomplete", variable=dismiss_var).pack(side=tk.LEFT, padx=(0, 12))

    spin_spaces = ttk.Spinbox(row2, from_=2, to=8, increment=2, width=3)
    spin_spaces.set(4)
    spin_spaces.pack(side=tk.RIGHT)
    ttk.Label(row2, text="Tab width:").pack(side=tk.RIGHT, padx=(8, 4))

    spin_delay = ttk.Spinbox(row2, from_=1, to=30, width=3)
    spin_delay.set(5)
    spin_delay.pack(side=tk.RIGHT)
    ttk.Label(row2, text="Delay (s):").pack(side=tk.RIGHT, padx=(8, 4))

    progress = ttk.Progressbar(container, orient=tk.HORIZONTAL, mode="determinate")
    progress.pack(fill=tk.X, pady=(0, 12))

    # Footer
    footer = ttk.Frame(container)
    footer.pack(fill=tk.X)
    ttk.Label(footer, text="Ctrl+Enter / F6 to start \u00B7 F8 to stop", style="Sub.TLabel").pack(side=tk.LEFT)

    engine = TypingEngine(
        on_status=lambda m, a: msg_queue.put(("status", (m, a))),
        on_progress=lambda p, m: msg_queue.put(("progress", (p, m))),
        on_finished=lambda c, am: msg_queue.put(("finished", (c, am))),
        on_stopped=lambda r, am: msg_queue.put(("stopped", (r, am))),
        on_minimize=lambda: msg_queue.put(("minimize", None)),
    )

    def do_paste():
        content = pyperclip.paste()
        if content:
            text_area.insert(tk.INSERT, content.replace("\r\n", "\n").replace("\r", "\n"))
            update_stats()

    def do_detect():
        sp, ut, msg = analyze_indentation(text_area.get("1.0", "end-1c"))
        spin_spaces.set(sp)
        if ut is not None:
            use_tab_var.set(ut)
        lbl_status.config(text=msg)

    ttk.Button(bar, text="Clear", command=lambda: (text_area.delete("1.0", tk.END), update_stats())).pack(side=tk.RIGHT, padx=(4, 0))
    ttk.Button(bar, text="Paste", command=do_paste).pack(side=tk.RIGHT, padx=(4, 0))
    ttk.Button(bar, text="Auto-Detect", command=do_detect).pack(side=tk.RIGHT, padx=(4, 0))

    def on_primary():
        if engine.is_running:
            engine.stop("Stop button")
            return
        raw = text_area.get("1.0", "end-1c")
        if not raw:
            messagebox.showwarning(APP_NAME, "Please paste or enter text first.")
            return
        if engine.start(
            text=raw,
            base_delay_ms=float(slider.get()),
            randomize=humanize_var.get(),
            start_delay=int(spin_delay.get() or 5),
            auto_minimize=minimize_var.get(),
            code_mode=code_mode_var.get(),
            dismiss_autocomplete=dismiss_var.get(),
            use_tab_key=use_tab_var.get(),
            spaces_per_tab=int(spin_spaces.get() or 4),
        ):
            btn_primary.config(text="Stop Typing", bg="#1D1D1F")

    btn_primary = tk.Button(
        footer,
        text="Start Typing",
        font=(ui_font, 10, "bold"),
        bg="#0071E3",
        fg="#FFFFFF",
        activebackground="#0064C9",
        activeforeground="#FFFFFF",
        relief=tk.FLAT,
        padx=20,
        pady=8,
        cursor="hand2",
        command=on_primary,
    )
    btn_primary.pack(side=tk.RIGHT)

    listener = keyboard.Listener(
        on_press=lambda k: engine.handle_emergency_hotkey("F8") if k == Key.f8 else None
    )
    listener.daemon = True
    listener.start()

    root.bind("<F6>", lambda _e: on_primary() if not engine.is_running else None)
    root.bind("<Control-Return>", lambda _e: on_primary() if not engine.is_running else None)

    def poll():
        while not msg_queue.empty():
            kind, data = msg_queue.get_nowait()
            if kind == "status":
                lbl_status.config(text=data[0])
            elif kind == "progress":
                progress["value"] = data[0]
                lbl_status.config(text=data[1])
            elif kind == "minimize":
                root.iconify()
            elif kind == "finished":
                progress["value"] = 100
                btn_primary.config(text="Start Typing", bg="#0071E3")
                lbl_status.config(text=f"Finished \u00B7 {data[0]:,} characters typed")
                if data[1]:
                    root.deiconify()
            elif kind == "stopped":
                progress["value"] = 0
                btn_primary.config(text="Start Typing", bg="#0071E3")
                lbl_status.config(text=f"Stopped \u00B7 {data[0]}")
                if data[1]:
                    root.deiconify()
        root.after(30, poll)

    poll()
    root.protocol("WM_DELETE_WINDOW", lambda: (engine.stop("Closed"), listener.stop(), root.destroy()))
    root.mainloop()


# ==============================================================================
# Native Windows / Linux WebView2 Application (Same Apple Design Science UI)
# ==============================================================================
def run_webview_app() -> bool:
    """Run the full CynoType Apple Design Science UI on Windows/Linux via pywebview."""
    try:
        import webview
    except ImportError:
        return False

    class WebViewBridge:
        def __init__(self):
            self.window = None
            self.engine = TypingEngine(
                on_status=self._on_status,
                on_progress=self._on_progress,
                on_finished=self._on_finished,
                on_stopped=self._on_stopped,
                on_minimize=self._on_minimize,
            )
            self.listener = keyboard.Listener(
                on_press=lambda k: self.engine.handle_emergency_hotkey("F8")
                if k == Key.f8
                else None
            )
            self.listener.daemon = True
            self.listener.start()

        def _js(self, expr: str):
            win = self.window
            if win is not None:
                try:
                    win.evaluate_js(expr)
                except Exception:
                    pass

        def _on_status(self, msg: str, is_active: bool):
            self._js(
                f"window.CynoBridge.onStatus({json.dumps(msg)}, {json.dumps(bool(is_active))})"
            )

        def _on_progress(self, pct: float, msg: str):
            self._js(f"window.CynoBridge.onProgress({pct:.2f}, {json.dumps(msg)})")

        def _on_minimize(self):
            if self.window is not None:
                try:
                    self.window.minimize()
                except Exception:
                    pass

        def _restore(self):
            if self.window is not None:
                try:
                    self.window.restore()
                except Exception:
                    pass

        def _on_finished(self, total_chars: int, auto_minimize: bool):
            self._js(f"window.CynoBridge.onFinished({int(total_chars)})")
            if auto_minimize:
                self._restore()

        def _on_stopped(self, reason: str, auto_minimize: bool):
            self._js(f"window.CynoBridge.onStopped({json.dumps(reason)})")
            if auto_minimize:
                self._restore()

        def shutdown(self):
            if self.engine.is_running:
                self.engine.stop("Window closed")
            try:
                self.listener.stop()
            except Exception:
                pass

        def dispatch(self, body):
            if not isinstance(body, dict):
                return
            action = str(body.get("action", ""))

            if action == "request_paste":
                try:
                    content = pyperclip.paste()
                    if content:
                        self._js(
                            f"window.CynoBridge.onPasteText({json.dumps(str(content))})"
                        )
                except Exception as err:
                    print(f"[Paste Warning] {err}")

            elif action == "auto_detect":
                text = str(body.get("text", ""))
                silent = bool(body.get("silent", False))
                spaces, use_tab, msg = analyze_indentation(text)
                self._js(
                    f"window.CynoBridge.onAutoDetectResult("
                    f"{int(spaces)}, {json.dumps(use_tab)}, {json.dumps(msg)}, {json.dumps(silent)})"
                )

            elif action == "request_reset":
                has_text = bool(body.get("has_text", False))
                if has_text and self.window is not None:
                    confirmed = self.window.create_confirmation_dialog(
                        "Reset CynoType?",
                        "This clears the editor and restores all speed and indentation settings to their defaults.",
                    )
                    if not confirmed:
                        return
                if self.engine.is_running:
                    self.engine.stop("Reset")
                self._js("window.CynoBridge.onPerformReset()")

            elif action == "start":
                started = self.engine.start(
                    text=str(body.get("text", "")),
                    base_delay_ms=float(body.get("base_delay_ms", 45)),
                    randomize=bool(body.get("randomize", True)),
                    start_delay=int(body.get("start_delay", 5)),
                    auto_minimize=bool(body.get("auto_minimize", True)),
                    code_mode=bool(body.get("code_mode", True)),
                    dismiss_autocomplete=bool(body.get("dismiss_autocomplete", True)),
                    use_tab_key=bool(body.get("use_tab_key", True)),
                    spaces_per_tab=int(body.get("spaces_per_tab", 4)),
                )
                if started:
                    self._js("window.CynoBridge.onStarted()")

            elif action == "stop":
                self.engine.stop(str(body.get("source", "Stop button")))

    bridge = WebViewBridge()
    win = webview.create_window(
        APP_NAME,
        html=build_ui_html("platform-win"),
        js_api=bridge,
        width=820,
        height=780,
        min_size=(700, 620),
        background_color="#F5F5F7",
    )
    bridge.window = win
    win.events.closed += bridge.shutdown

    ico_path = resource_path("assets/CynoType.ico")
    start_kwargs = {}
    if IS_WIN:
        start_kwargs["gui"] = "edgechromium"
    if os.path.exists(ico_path):
        start_kwargs["icon"] = ico_path
    webview.start(**start_kwargs)
    return True


def main():
    if IS_MAC:
        run_macos_app()
    else:
        if not run_webview_app():
            run_tk_fallback_app()


if __name__ == "__main__":
    main()
