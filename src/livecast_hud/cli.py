# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Command line entry point: Korean speech → English subtitles over your slides.

- --mode demo: types the configured script sentences word by word (offline, default)
- --mode file: streams a recording (--audio WAV) in real time in place of the microphone
- --mode live: the real microphone (--device) with --stt gcp
- Switch talks while running: type 1–9 and Enter in the terminal (works during click-through) or F1–F9 when the
  window has focus. Toggle the Korean line: k and Enter, or the K key.
"""

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .config import Config, ConfigError
from .translate import GcpTranslator, LivecastTranslator, speech_hints


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="livecast-hud",
                                     description="Live English subtitles for Korean talks, shown on top of your slides.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", help="talk config JSON (scripts, glossary, hints); default: $LIVECAST_CONFIG")
    parser.add_argument("--mode", choices=["demo", "file", "live"], default="demo",
                        help="demo (play the script, default) | file (a recording, --audio) | live (microphone, --stt gcp)")
    parser.add_argument("--audio", help="input WAV for file mode (16 kHz, mono, 16-bit). With --stt offline a "
                                        "<name>.timing.json must sit next to it")
    parser.add_argument("--stt", choices=["offline", "gcp"], default="offline",
                        help="offline (file-mode stand-in, no network, default) | gcp (Google Cloud Speech-to-Text)")
    parser.add_argument("--mt", choices=["script", "gcp"], default="script",
                        help="script (match the talk script, offline, default) | gcp (Google Cloud Translation)")
    parser.add_argument("--gcp-project", help="Google Cloud project id for --stt gcp / --mt gcp; default: $LIVECAST_GCP_PROJECT")
    parser.add_argument("--talk", type=int, choices=range(1, 10), metavar="N",
                        help="talk number from the config (default: the lowest). Switch while running with 1-9 Enter or F1-F9")
    parser.add_argument("--device", help="microphone index or part of its name (see --list-devices)")
    parser.add_argument("--list-devices", action="store_true", help="list microphone inputs and exit (records nothing)")
    parser.add_argument("--click-through", action="store_true", help="start with click-through on (the HUD takes no clicks or focus)")
    parser.add_argument("--no-click-through", dest="click_through", action="store_false",
                        help="turn click-through off (wins over an earlier --click-through, e.g. after run_live.sh); press C to turn it on")
    parser.add_argument("--position", choices=["bottom", "top"], default="bottom", help="subtitle position (default bottom)")
    parser.add_argument("--screen", type=int, help="screen index for the subtitles (listed at start; default primary). "
                                                   "Use the projector screen in extended mode")
    parser.add_argument("--en-only", action="store_true", help="show English only (hide the Korean line)")
    parser.add_argument("--log", help="JSONL log of display events (latency checks and review)")
    parser.add_argument("--exit-on-end", action="store_true", help="quit 3 seconds after the input (or one demo pass) ends")
    parser.add_argument("--snapshot-at", default="", help="testing: save a PNG of the HUD window n seconds after start (comma separated)")
    parser.add_argument("--snapshot-dir", default=".", help="testing: folder for snapshot PNGs")
    return parser


def read_commands(stream, emit):
    """Terminal lines → Qt signal (daemon thread, works during click-through): 1–9 = switch talk, k = Korean line"""
    for line in stream:
        cmd = line.strip().lower()
        if cmd == "k" or (len(cmd) == 1 and cmd in "123456789"):
            emit(cmd)


def run_demo_mode(pipe, sentences, loop: bool = True, on_end=None, sleep=time.sleep):
    """Type the script sentences word by word and show their script English (no external calls).
    Loops until closed, or plays once and calls on_end when loop is False."""
    print("[DEMO] playing the script sentences...")

    def demo_worker():
        sleep(1.0)
        while True:
            for item in sentences:
                words = item["ko"].split()
                for i in range(1, len(words) + 1):
                    pipe.on_partial(" ".join(words[:i]))
                    sleep(0.35)
                pipe.on_final(item["ko"])
                sleep(3.2)  # time for the audience to read
            if not loop:
                break
        if on_end:
            on_end()

    t = threading.Thread(target=demo_worker, daemon=True)
    t.start()
    return t


def watch_end(app, pipe, done: threading.Event, args, engine=None):
    """Poll on the Qt thread: log the audio start, and when the input ends mark the HUD 'ended' (quit with --exit-on-end)"""
    from PyQt6.QtCore import QTimer
    state = {"t0_logged": False}

    def poll():
        src = getattr(engine, "source", None)
        if src is not None and src.t0 and not state["t0_logged"]:
            state["t0_logged"] = True
            pipe.log.write("audio_t0", t0=src.t0, m0=src.t0_mono, audio=str(src.path), duration=round(src.duration, 3))
        if done.is_set():
            timer.stop()
            pipe.window.set_link_status("ended")
            pipe.log.write("end", stats=dict(pipe.stats),
                           chunks=len(src.lags) if src else None,
                           max_lag=round(max(src.lags, default=0.0), 4) if src else None)
            print(f"Input ended: {dict(pipe.stats)}")
            if args.exit_on_end:
                QTimer.singleShot(3000, app.quit)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    return timer


def run_stream_mode(pipe, args, project, done: threading.Event):
    """File (--mode file) or microphone (--mode live) → STT (offline stand-in or gcp) → translation → HUD"""
    if args.stt == "offline":
        from .stt import OfflineScriptSTT
        engine = OfflineScriptSTT(args.audio)
    else:
        from .stt import LivecastSTTEngine
        device = int(args.device) if args.device and args.device.isdigit() else args.device
        engine = LivecastSTTEngine(project, device=device, audio_path=args.audio if args.mode == "file" else None,
                                   hints=speech_hints(pipe.translator.config))   # every talk's hints
    print(f"[{args.mode.upper()}] input {'file ' + args.audio if args.mode == 'file' else 'microphone'} -> STT {args.stt} -> MT {args.mt}")
    engine.start(on_partial=pipe.on_partial, on_final=pipe.on_final, on_status=pipe.on_status, on_end=done.set)
    return engine


def snapshot(window, folder: str, t: float, log):
    """Testing: save only the HUD window as PNG (never the whole desktop)"""
    path = Path(folder) / f"hud_{t:06.1f}s.png"
    window.grab().save(str(path))
    log.write("snapshot", path=str(path), window=window.diagnostics())
    print(f"Snapshot {path}")


def fail(message: str):
    sys.exit(f"livecast-hud: error: {message}")


def load_config(args) -> Config:
    path = args.config or os.environ.get("LIVECAST_CONFIG")
    try:
        config = Config.load(path) if path else Config()
        talk = args.talk if args.talk is not None else config.default_talk()
        if config.talks and talk not in config.talks:
            fail(f"talk {talk} is not in the config (talks: {', '.join(map(str, config.talk_numbers()))})")
        args.talk = talk
        if args.mode == "demo" and not config.demo(talk):
            fail(f"demo mode needs a script for talk {talk}; try: livecast-hud --config examples/talks.json")
    except ConfigError as e:
        fail(str(e))
    return config


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_devices:
        from .stt import list_input_devices
        print("\n".join(list_input_devices()))
        return
    if args.mode == "file" and not args.audio:
        parser.error("--mode file needs --audio")
    if args.mode == "live" and args.stt != "gcp":
        parser.error("--mode live works only with --stt gcp")
    project = args.gcp_project or os.environ.get("LIVECAST_GCP_PROJECT")
    if (args.stt == "gcp" or args.mt == "gcp") and not project:
        fail("--stt gcp and --mt gcp need a Google Cloud project: pass --gcp-project ID or set LIVECAST_GCP_PROJECT")
    config = load_config(args)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C in the terminal quits at once

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from .overlay import SubtitleOverlayWindow
    from .pipeline import EventLog, Pipeline

    app = QApplication(sys.argv[:1])
    window = SubtitleOverlayWindow(screen_index=args.screen)
    translator = LivecastTranslator(config, mode=args.mt, talk=args.talk,
                                    gcp=GcpTranslator(project) if args.mt == "gcp" else None)
    log = EventLog(args.log)
    pipe = Pipeline(window, translator, log)

    talks = ", ".join(f"{n}" + (f" {config.name(n)}" if config.name(n) else "") for n in config.talk_numbers()) or "none"
    print("\n" + "=" * 70)
    print(f"LiveCast HUD {__version__}: mode {args.mode} | STT {args.stt} | MT {args.mt} | talk {args.talk}")
    print(f"- Talks in config: {talks}")
    print("- Screens: " + "  ".join(f"[{i}] {s.geometry().width()}x{s.geometry().height()}"
                                    + (" (primary)" if s == app.primaryScreen() else "") for i, s in enumerate(app.screens())))
    print("- Terminal (works during click-through): 1-9 + Enter switch talk | k + Enter toggle the Korean line")
    print("- Keys (click the HUD first):")
    print("  1 bottom | 2 top | T or K Korean line | F1-F9 switch talk (fn+F1 on a MacBook)")
    print("  C click-through (so slide clicks pass through) | + / - text size | Esc or Q quit (or Ctrl+C)")
    print("  Drag with the mouse to move")
    print("=" * 70 + "\n")

    window.show()
    if args.position == "top":
        window._position_at_top()
    if args.en_only:
        window.set_korean(False)
    if args.click_through:
        window.set_click_through(True)
    window.set_talk(args.talk)
    window.signals.talk_requested.connect(pipe.set_talk)
    if sys.stdin:
        threading.Thread(target=read_commands, args=(sys.stdin, window.signals.command.emit), daemon=True).start()
    log.write("start", argv=sys.argv[1:], window=window.diagnostics())

    for t in filter(None, args.snapshot_at.split(",")):
        QTimer.singleShot(int(float(t) * 1000), lambda t=float(t): snapshot(window, args.snapshot_dir, t, log))

    done = threading.Event()
    if args.mode == "demo":
        run_demo_mode(pipe, config.demo(args.talk), loop=not args.exit_on_end, on_end=done.set)
        _timer = watch_end(app, pipe, done, args)
    else:
        engine = run_stream_mode(pipe, args, project, done)
        _timer = watch_end(app, pipe, done, args, engine)

    sys.exit(app.exec())
