# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Shared test setup: the package on the import path, Qt without a screen (offscreen), and an audit hook that
counts external calls (socket connect, address lookup, URL request, subprocess) and asserts zero per test."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

WATCHED = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request", "subprocess.Popen", "os.system"}
EXTERNAL_EVENTS = []


def _audit(event, args):
    if event in WATCHED:
        # Popen args are (executable, argv, cwd, env): keep executable and argv only, so no env value reaches a failure message
        EXTERNAL_EVENTS.append((event, repr(args[:2] if event == "subprocess.Popen" else args)[:200]))


sys.addaudithook(_audit)


@pytest.fixture(autouse=True)
def no_external_calls():
    before = len(EXTERNAL_EVENTS)
    yield
    assert EXTERNAL_EVENTS[before:] == [], f"external call made: {EXTERNAL_EVENTS[before:]}"


def _blocked(name):
    def _raise(*args, **kwargs):
        raise AssertionError(f"blocked in tests: {name}")
    return _raise


@pytest.fixture(autouse=True)
def block_devices_and_network(monkeypatch):
    """Any real connection, microphone stream, Cloud client or credential lookup fails the test at once.
    Tests that need one of these on purpose replace it with a fake via monkeypatch, as before."""
    import importlib.util
    import socket
    import types

    monkeypatch.setattr(socket.socket, "connect", _blocked("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", _blocked("socket.create_connection"))

    # importing the real sounddevice initializes PortAudio and scans devices, so a stand-in is used unless it is loaded
    if "sounddevice" in sys.modules:
        for name in ("InputStream", "RawInputStream"):
            monkeypatch.setattr(sys.modules["sounddevice"], name, _blocked(f"sounddevice.{name}"))
    elif importlib.util.find_spec("sounddevice"):
        sd = types.ModuleType("sounddevice")
        sd.InputStream, sd.RawInputStream = _blocked("sounddevice.InputStream"), _blocked("sounddevice.RawInputStream")
        sd.__getattr__ = _blocked("sounddevice (device access)")
        monkeypatch.setitem(sys.modules, "sounddevice", sd)

    try:
        from google.cloud import speech
        monkeypatch.setattr(speech.SpeechClient, "__init__", _blocked("google.cloud.speech.SpeechClient"))
    except ImportError:
        pass
    try:
        import google.auth
        monkeypatch.setattr(google.auth, "default", _blocked("google.auth.default"))
    except ImportError:
        pass
    try:
        import requests
        monkeypatch.setattr(requests.Session, "request", _blocked("requests.Session.request"))
    except ImportError:
        pass
    from livecast_hud import translate
    monkeypatch.setattr(translate, "urllib_post", _blocked("translate.urllib_post"))


@pytest.fixture(scope="session")
def qapp():
    import livecast_hud.overlay  # noqa: F401  sets the PyQt6 plugin path first, as a real run does
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
