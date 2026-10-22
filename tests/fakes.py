# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Test doubles and helpers: example config, fake Cloud Translation, fake clock, fake window, silent WAV files."""
import json
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "talks.json"
FAKE_TOKEN, FAKE_PROJECT, FAKE_ACCOUNT = "ya29.FAKE-token-0123456789abcdef", "proj-fake-000", "someone@example.test"


def config():
    from livecast_hud.config import Config
    return Config.load(EXAMPLES)


def script_by_id(talk=1):
    return {s["id"]: s for s in config().script(talk)}


def translator(mode="script", talk=1, gcp=None):
    from livecast_hud.translate import LivecastTranslator
    return LivecastTranslator(config(), mode=mode, talk=talk, gcp=gcp)


def wav_file(path: Path, seconds: float, rate: int = 16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


class FakeHTTP:
    """Fake Cloud Translation: answers in order, (status, English) or an exception. Records requests"""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        a = self.answers.pop(0) if self.answers else (200, "EN")
        if isinstance(a, Exception):
            raise a
        status, en = a
        return status, json.dumps({"translations": [{"translatedText": en}]}).encode() if status == 200 else b""


class DictHTTP(FakeHTTP):
    """Fake translation that picks the English by a key text contained in the Korean sent (fail keys get 403)"""

    def __init__(self, table, fail=()):
        super().__init__()
        self.table, self.fail = table, fail

    def __call__(self, url, headers, body, timeout):
        ko = json.loads(body)["contents"][0]
        self.calls.append({"ko": ko})
        if any(f in ko for f in self.fail):
            return 403, b""
        en = next(v for k, v in self.table.items() if k in ko)
        return 200, json.dumps({"translations": [{"translatedText": en}]}).encode()


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeWindow:
    def __init__(self):
        self.shown, self.links, self.talks, self.on_shown = [], [], [], None

    def set_subtitles(self, ko, en, is_final=False):
        self.shown.append((ko, en, is_final))

    def set_link_status(self, s):
        self.links.append(s)

    def set_talk(self, n):
        self.talks.append(n)


def gcp(http, clock=None, tokens=None):
    from livecast_hud.translate import GcpTranslator
    toks = iter(tokens or [FAKE_TOKEN] * 10)
    return GcpTranslator(project=FAKE_PROJECT, post=http, token_provider=lambda: next(toks, None), clock=clock or FakeClock())


def pipeline(http, talk=2, clock=None, tokens=None, window=None):
    """--mt gcp pipeline on the example config, translating on the same thread"""
    from livecast_hud.pipeline import EventLog, Pipeline
    w = window or FakeWindow()
    tr = translator(mode="gcp", talk=talk, gcp=gcp(http, tokens=tokens))
    return Pipeline(w, tr, EventLog(None), clock=clock or FakeClock(), mt_inline=True), w
