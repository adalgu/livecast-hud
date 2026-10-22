# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Transparent subtitle overlay window (macOS first).

- Stays on top, including over full-screen PowerPoint / Keynote (NSWindowCollectionBehaviorFullScreenAuxiliary).
- Layout: two committed English lines on top, a smaller dimmed draft (in-progress) box of two lines below,
  one Korean line, a corner dot for the connection state plus "Talk N". Fixed window height at the bottom
  edge of the screen (≤150 px with Korean, ≤130 px without).
- Drag to move, click-through, position and font-size keys. A dropped connection keeps the last subtitle
  and only changes the dot color.
"""

import os

import PyQt6

# Some Python distributions ship a Qt5 qt.conf that hijacks the plugin path, so Qt cannot find the 'cocoa'
# platform for PyQt6. Point Qt at PyQt6's own plugins before QApplication is created (unless already set).
_QT_PLUGINS = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins")
if os.path.isdir(_QT_PLUGINS):
    os.environ.setdefault("QT_PLUGIN_PATH", _QT_PLUGINS)

from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QLabel, QLayout, QVBoxLayout, QWidget  # noqa: E402

try:
    import Cocoa
    import objc
    HAS_COCOA = True
except ImportError:
    HAS_COCOA = False


class SubtitleSignals(QObject):
    update_text = pyqtSignal(str, str)   # (ko_text, en_text)
    finalize_text = pyqtSignal(str, str) # (ko_text, en_text)
    link_status = pyqtSignal(str)        # connected / disconnected / ended
    command = pyqtSignal(str)            # terminal input: "1".."9" = switch talk, "k" = toggle the Korean line
    talk_requested = pyqtSignal(int)     # talk switch request (F1–F9 or terminal) → Pipeline.set_talk


EN_PX, DRAFT_PX, KO_PX, CORNER_PX = 24, 18, 16, 10
EN_LINES, DRAFT_LINES = 2, 2                # committed box / draft box line counts
DRAFT_GAP = 4                               # gap between the committed and draft boxes
H_MAX_KO, H_MAX_EN_ONLY = 150, 130          # window height caps (Korean on / off) — stays inside a typical slide footer band
OUTER_MARGINS = (6, 4, 6, 0)                # bottom 0: window bottom = screen bottom
INNER_MARGINS = (24, 12, 24, 6)             # top 12: room for the corner dot and "Talk N"
KEEP_SENTENCES = 8                          # committed English sentences kept for rolling (two lines are shown)
DOT_COLORS = {"connected": "#00E676", "disconnected": "#FFB300", "ended": "#9E9E9E"}
TALK_KEYS = {getattr(Qt.Key, f"Key_F{n}"): str(n) for n in range(1, 10)}


class SubtitleOverlayWindow(QWidget):
    def __init__(self, screen_index: int | None = None):
        super().__init__()
        self.screen_index = screen_index  # screen to show subtitles on (None = primary); the projector in extended mode
        self.show_korean = True
        self.font_scale = 1.0
        self.click_through = False
        self.drag_position = QPoint()
        self._ns_window = None
        self.on_shown = None  # called right after a change is drawn (kind, ko, en) — latency logging
        self.committed = []   # committed English sentences, newest last (source of the committed box)
        self.draft = ""       # partial English of the sentence being spoken (draft box)
        self.link_state = "connected"
        self.talk = None      # current talk number (small corner text "Talk N")
        self._ko_full = ""

        self.signals = SubtitleSignals()
        self.signals.update_text.connect(self._on_update_text)
        self.signals.finalize_text.connect(self._on_finalize_text)
        self.signals.link_status.connect(self._on_link_status)
        self.signals.command.connect(self.handle_command)

        self._init_window_flags()
        self._init_ui()
        self._position_at_bottom()

    def _init_window_flags(self):
        """Frameless, always on top, transparent background
        (Qt.SubWindow never creates an NSWindow on macOS, so the window would not appear at all → Qt.Window)"""
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def showEvent(self, event):
        super().showEvent(event)
        if self._ns_window is None:  # the NSWindow exists only once shown, so Cocoa properties are set then
            self._apply_macos_fullscreen_behavior()

    def _apply_macos_fullscreen_behavior(self):
        """On macOS, stay visible on top of Keynote / PowerPoint full-screen Spaces"""
        if not HAS_COCOA or QGuiApplication.platformName() != "cocoa":
            return  # on offscreen and other platforms winId is not an NSView
        try:
            win_id = int(self.winId())
            ns_view = objc.objc_object(c_void_p=win_id)
            ns_window = ns_view.window()
            self._ns_window = ns_window
            if ns_window:
                # CanJoinAllSpaces (every desktop) | FullScreenAuxiliary (over full-screen apps)
                behavior = (
                    Cocoa.NSWindowCollectionBehaviorCanJoinAllSpaces
                    | Cocoa.NSWindowCollectionBehaviorFullScreenAuxiliary
                    | Cocoa.NSWindowCollectionBehaviorStationary
                )
                ns_window.setCollectionBehavior_(behavior)
                # a regular app window does not appear on another app's full-screen Space → accessory app (no Dock icon)
                Cocoa.NSApplication.sharedApplication().setActivationPolicy_(
                    Cocoa.NSApplicationActivationPolicyAccessory)
                # status window level (25) + 2 (PyObjC Cocoa has no kCGStatusWindowLevel, so use the AppKit constant)
                ns_window.setLevel_(Cocoa.NSStatusWindowLevel + 2)
        except Exception as e:
            print(f"Warning: could not set Cocoa window properties: {e}")

    def _init_ui(self):
        self.setMinimumWidth(800)

        self.container = QWidget(self)
        self.container.setObjectName("container")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(*OUTER_MARGINS)
        main_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)   # _fit_height sets the height (fixed, independent of text)
        main_layout.addWidget(self.container)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(*INNER_MARGINS)
        container_layout.setSpacing(0)

        # committed box, 2 lines (bottom = newest) + gap + draft box, 2 lines (small, dimmed). One label per line —
        # _wrap breaks lines itself so whole lines can be dropped
        self.en_rows = [self._add_row(container_layout, "en_row") for _ in range(EN_LINES)]
        container_layout.addSpacing(DRAFT_GAP)
        self.draft_rows = [self._add_row(container_layout, "draft_row") for _ in range(DRAFT_LINES)]

        # Korean line (elided on the left when too long, so the newest words stay visible)
        self.ko_label = QLabel("", self.container)
        self.ko_label.setObjectName("ko_label")
        self.ko_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        shadow_ko = QGraphicsDropShadowEffect(self)
        shadow_ko.setBlurRadius(8)
        shadow_ko.setColor(QColor(0, 0, 0, 200))
        shadow_ko.setOffset(0, 2)
        self.ko_label.setGraphicsEffect(shadow_ko)

        container_layout.addWidget(self.ko_label)

        # corner: connection dot + "Talk N" (outside the layout, in the top margin)
        self.corner = QLabel("", self.container)
        self.corner.setObjectName("corner")
        self.corner.move(10, 1)

        self._apply_styles()
        self._update_corner()

    def _add_row(self, layout: QVBoxLayout, name: str) -> QLabel:
        row = QLabel("", self.container)
        row.setObjectName(name)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shadow_en = QGraphicsDropShadowEffect(self)
        shadow_en.setBlurRadius(14)
        shadow_en.setColor(QColor(0, 0, 0, 240))
        shadow_en.setOffset(0, 3)
        row.setGraphicsEffect(shadow_en)
        layout.addWidget(row)
        return row

    def _apply_styles(self):
        """Dark translucent subtitle style sheet"""
        base_en_size = int(EN_PX * self.font_scale)
        base_draft_size = int(DRAFT_PX * self.font_scale)
        base_ko_size = int(KO_PX * self.font_scale)

        self.setStyleSheet(f"""
            QWidget#container {{
                background-color: rgba(12, 15, 22, 0.82);
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.16);
            }}
            QLabel#corner {{
                color: rgba(255, 255, 255, 0.45);
                font-size: {CORNER_PX}px;
            }}
            QLabel#en_row {{
                color: #FFFFFF;
                font-size: {base_en_size}px;
                font-weight: 700;
                font-family: -apple-system, 'SF Pro Display', 'Inter', 'Helvetica Neue', sans-serif;
            }}
            QLabel#draft_row {{
                color: rgba(255, 255, 255, 0.45);
                font-size: {base_draft_size}px;
                font-weight: 600;
                font-family: -apple-system, 'SF Pro Display', 'Inter', 'Helvetica Neue', sans-serif;
            }}
            QLabel#ko_label {{
                color: rgba(220, 225, 235, 0.75);
                font-size: {base_ko_size}px;
                font-weight: 500;
                font-family: -apple-system, 'Pretendard', 'Apple SD Gothic Neo', sans-serif;
            }}
        """)
        for w in (*self.en_rows, *self.draft_rows, self.ko_label, self.corner):
            w.ensurePolished()
        self._en_h = self.en_rows[0].fontMetrics().height()
        self._draft_h = self.draft_rows[0].fontMetrics().height()
        self._ko_h = self.ko_label.fontMetrics().height()
        for row in self.en_rows:
            row.setFixedHeight(self._en_h)
        for row in self.draft_rows:
            row.setFixedHeight(self._draft_h)
        self.ko_label.setFixedHeight(self._ko_h)
        self.corner.adjustSize()
        self._render()

    def _screen_geometry(self):
        screens = QApplication.screens()
        if self.screen_index is not None and 0 <= self.screen_index < len(screens):
            return screens[self.screen_index].geometry()
        return QApplication.primaryScreen().geometry()

    def _position_at_bottom(self):
        """Bottom center with no bottom margin"""
        screen = self._screen_geometry()
        win_w = int(screen.width() * 0.82)
        win_h = self._need_height()
        pos_x = screen.x() + int((screen.width() - win_w) / 2)
        pos_y = screen.y() + screen.height() - win_h
        self.setGeometry(pos_x, pos_y, win_w, win_h)
        self._anchor_bottom, self._bottom_y = True, screen.y() + screen.height()
        self._fit_height()

    def _position_at_top(self):
        """Top center"""
        screen = self._screen_geometry()
        win_w = int(screen.width() * 0.82)
        win_h = self._need_height()
        pos_x = screen.x() + int((screen.width() - win_w) / 2)
        pos_y = screen.y() + 50
        self.setGeometry(pos_x, pos_y, win_w, win_h)
        self._anchor_bottom = False
        self._fit_height()

    # --- height: fixed whether or not there is text (the box never jumps). Over the cap, the draft box shrinks to one line ---
    def _height(self, korean: bool, draft_n: int) -> int:
        return (OUTER_MARGINS[1] + OUTER_MARGINS[3] + INNER_MARGINS[1] + INNER_MARGINS[3]
                + EN_LINES * self._en_h + DRAFT_GAP + draft_n * self._draft_h + (self._ko_h if korean else 0))

    def _height_cap(self, korean: bool) -> int:
        return H_MAX_KO if korean else H_MAX_EN_ONLY

    def _draft_n(self) -> int:
        """Draft box lines: 2, or 1 when 2 would exceed the cap (after enlarging the font)"""
        fits = self._height(self.show_korean, DRAFT_LINES) <= self._height_cap(self.show_korean)
        return DRAFT_LINES if fits else 1

    def _need_height(self) -> int:
        return self._height(self.show_korean, self._draft_n())

    def _fit_height(self):
        """Match the window to the fixed height (changes only with the Korean line or font size); bottom edge kept when anchored"""
        need = self._need_height()
        top = self._bottom_y - need if self._anchor_bottom else self.y()
        if (need, top) != (self.height(), self.y()):
            self.setGeometry(self.x(), top, self.width(), need)

    # --- English rolling, one Korean line ---
    def _text_width(self) -> int:
        return max(100, self.width() - OUTER_MARGINS[0] - OUTER_MARGINS[2] - INNER_MARGINS[0] - INNER_MARGINS[2] - 8)

    def _wrap(self, text: str, row: QLabel) -> list:
        """Break one English sentence into lines for the current width and that row's font (at word boundaries)"""
        fm, width = row.fontMetrics(), self._text_width()
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}" if cur else word
            if cur and fm.horizontalAdvance(trial) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        return lines + ([cur] if cur else [])

    def _push_en(self, en: str, final: bool):
        """Only final English is appended to the committed box (pushing older lines up). A partial only changes
        the draft box (the committed box never moves)"""
        en = en.strip()
        last = self.committed[-1] if self.committed else ""
        if final:
            if en and en != last:                # the same final twice goes up once
                self.committed.append(en)
                del self.committed[:-KEEP_SENTENCES]
            self.draft = ""                      # a final clears the draft; an empty final (off-script) keeps the committed box
        else:
            self.draft = "" if en == last else en   # the just-committed English resent as a partial is not shown twice; empty clears
        self._render()

    def _render(self):
        """Committed box: the last 2 lines of the committed sentences (bottom line if only one). Draft box: the last
        lines of the partial — always dimmed"""
        lines = [ln for text in self.committed for ln in self._wrap(text, self.en_rows[0])][-EN_LINES:]
        for row, ln in zip(self.en_rows, [""] * (EN_LINES - len(lines)) + lines):
            row.setText(ln)
        n = self._draft_n()
        draft = self._wrap(self.draft, self.draft_rows[0])[-n:]
        for i, row in enumerate(self.draft_rows):
            row.setHidden(i >= n)
            row.setText(draft[i] if i < len(draft) else "")

    def en_lines(self) -> list:
        """English lines shown in the committed box, top to bottom (tests and diagnostics)"""
        return [r.text() for r in self.en_rows if r.text()]

    def draft_lines(self) -> list:
        """English lines shown in the draft box, top to bottom (tests and diagnostics)"""
        return [r.text() for r in self.draft_rows if r.text() and not r.isHidden()]

    def _set_ko(self, text: str):
        self._ko_full = text or ""
        fm = self.ko_label.fontMetrics()
        self.ko_label.setText(fm.elidedText(self._ko_full, Qt.TextElideMode.ElideLeft, self._text_width()))

    def set_korean(self, on: bool):
        """Show or hide the Korean line (K / T keys, terminal k⏎, --en-only)"""
        self.show_korean = on
        self.ko_label.setVisible(on)
        self._render()
        self._fit_height()

    def set_talk(self, n: int):
        self.talk = n
        self._update_corner()

    def _set_scale(self, scale: float):
        """Font size (+/-): all boxes scale together. Not enlarged if even a one-line draft box would exceed the caps"""
        old, self.font_scale = self.font_scale, scale
        self._apply_styles()
        if scale > old and any(self._height(k, 1) > self._height_cap(k) for k in (True, False)):
            self.font_scale = old
            self._apply_styles()
        self._fit_height()

    def handle_command(self, cmd: str):
        """One terminal line (stdin thread → signal): a digit 1–9 switches talk, k toggles the Korean line"""
        if len(cmd) == 1 and cmd in "123456789":
            self.signals.talk_requested.emit(int(cmd))
        elif cmd.lower() == "k":
            self.set_korean(not self.show_korean)

    def _update_corner(self):
        color = DOT_COLORS.get(self.link_state, DOT_COLORS["connected"])
        self.corner.setText(f'<span style="color:{color}">●</span>' + (f" Talk {self.talk}" if self.talk else ""))
        self.corner.adjustSize()

    def set_subtitles(self, ko_text: str, en_text: str, is_final: bool = False):
        """Thread-safe subtitle update"""
        if is_final:
            self.signals.finalize_text.emit(ko_text, en_text)
        else:
            self.signals.update_text.emit(ko_text, en_text)

    def _on_update_text(self, ko_text: str, en_text: str):
        self._set_ko(ko_text)
        self._push_en(en_text, final=False)
        self._fit_height()
        if self.on_shown:
            self.on_shown("partial", ko_text, en_text)

    def _on_finalize_text(self, ko_text: str, en_text: str):
        self._set_ko(ko_text)
        self._push_en(en_text, final=True)
        self._fit_height()
        if self.on_shown:
            self.on_shown("final", ko_text, en_text)

    # --- connection state: subtitles stay, only the corner dot changes color ---
    def set_link_status(self, state: str):
        """Thread-safe connection state (connected / disconnected / ended)"""
        self.signals.link_status.emit(state)

    def _on_link_status(self, state: str):
        self.link_state = state
        self._update_corner()

    def set_click_through(self, on: bool):
        """Click-through: Qt ignores mouse events and the macOS window itself ignores the mouse (slides get the clicks)"""
        self.click_through = on
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        if self._ns_window is not None:
            self._ns_window.setIgnoresMouseEvents_(on)
        print(f"Click-through {'on' if on else 'off'}")

    def diagnostics(self) -> dict:
        """Window state for on-site checks (shown over full screen, click-through)"""
        info = {"platform": QGuiApplication.platformName(), "geometry": list(self.geometry().getRect()),
                "click_through": self.click_through}
        w = self._ns_window
        if w is not None:
            info.update({
                "window_number": int(w.windowNumber()),
                "level": int(w.level()),
                "collection_behavior": int(w.collectionBehavior()),
                "ignores_mouse_events": bool(w.ignoresMouseEvents()),
                "on_active_space": bool(w.isOnActiveSpace()),
                "occlusion_visible": bool(int(w.occlusionState()) & int(Cocoa.NSWindowOcclusionStateVisible)),
            })
        return info

    # --- drag to move ---
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            self._bottom_y = self.y() + self.height()      # keep the new bottom edge when the height changes
            event.accept()

    # --- keys ---
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):         # quit
            self.close()
        elif key == Qt.Key.Key_1:                             # bottom
            self._position_at_bottom()
        elif key == Qt.Key.Key_2:                             # top
            self._position_at_top()
        elif key in (Qt.Key.Key_T, Qt.Key.Key_K):             # Korean line (terminal k⏎ while click-through is on)
            self.set_korean(not self.show_korean)
        elif key in TALK_KEYS:                                # F1–F9: switch talk (window focus; terminal 1–9⏎ otherwise)
            self.handle_command(TALK_KEYS[key])
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):      # larger text
            self._set_scale(min(1.6, self.font_scale + 0.1))
        elif key == Qt.Key.Key_Minus:                         # smaller text
            self._set_scale(max(0.7, self.font_scale - 0.1))
        elif key == Qt.Key.Key_C:                             # click-through
            self.set_click_through(not self.click_through)
        else:
            super().keyPressEvent(event)
