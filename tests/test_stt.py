# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Speech recognition: real-time file streaming, the offline stand-in, and the Cloud streaming engine with fake
clients (reconnect, new token on reconnect, planned rotation before the 5-minute limit, hidden values)."""
import json
import threading
import time
import types
import wave
from pathlib import Path

import pytest
from fakes import FAKE_ACCOUNT, FAKE_PROJECT, FAKE_TOKEN, FakeClock, FakeWindow, script_by_id, translator, wav_file


# ---------- file input: real-time 100 ms chunks ----------
def test_file_source_streams_100ms_chunks_in_real_time(tmp_path):
    from livecast_hud.stt import FileAudioSource
    src = FileAudioSource(str(wav_file(tmp_path / "a.wav", 2.0)))
    got = []
    t_start = time.monotonic()
    src.run(lambda c: got.append((time.monotonic() - t_start, c)), lambda: False)
    chunks = [c for _, c in got if c is not None]
    assert got[-1][1] is None
    assert len(chunks) == 20 and all(len(c) == 3200 for c in chunks)
    for k, (t, _) in enumerate(got[:-1]):                                # chunk k leaves at (k+1) × 0.1 s
        assert abs(t - (k + 1) * 0.1) < 0.03, (k, t)
    assert max(src.lags) < 0.03


def test_file_source_rejects_wrong_format(tmp_path):
    from livecast_hud.stt import FileAudioSource
    p = tmp_path / "b.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00" * 400)
    with pytest.raises(ValueError):
        FileAudioSource(str(p))


def test_offline_stt_emits_partials_then_final_at_sentence_end(tmp_path):
    from livecast_hud.stt import OfflineScriptSTT
    wav = wav_file(tmp_path / "c.wav", 2.5)
    (tmp_path / "c.timing.json").write_text(json.dumps({"sentences": [
        {"id": "x1", "start": 0.2, "end": 1.2, "ko": "첫 번째 발표는 씨앗 도서관 이야기를 다룹니다."},
        {"id": "x2", "start": 1.5, "end": 2.3, "ko": "두 번째 발표는 옥상 양봉 이야기를 다룹니다."},
    ]}), encoding="utf-8")
    events, done = [], threading.Event()
    t0 = time.monotonic()
    stt = OfflineScriptSTT(str(wav))
    stt.start(on_partial=lambda s: events.append(("p", time.monotonic() - t0, s)),
              on_final=lambda s: events.append(("f", time.monotonic() - t0, s)), on_end=done.set)
    assert done.wait(5)
    finals = [e for e in events if e[0] == "f"]
    assert [f[2] for f in finals] == [s["ko"] for s in stt.timing]
    assert abs(finals[0][1] - 1.2) < 0.15 and abs(finals[1][1] - 2.3) < 0.15
    partials = [e[2] for e in events if e[0] == "p" and e[1] < 1.2]
    assert partials and all(stt.timing[0]["ko"].startswith(p) for p in partials)
    assert len(partials[-1]) > len(partials[0])


def test_display_keeps_heard_korean_and_shows_script_english():
    from livecast_hud.pipeline import EventLog, Pipeline
    by_id = script_by_id()
    heard = by_id["s05"]["ko"].replace("오후 2시부터 5시까지", "오후 두 시부터 다섯 시까지")
    w = FakeWindow()
    Pipeline(w, translator(), EventLog(None)).on_final(heard)
    assert w.shown[-1] == (heard, by_id["s05"]["en"], True)            # the Korean line shows what was heard


def test_file_mode_variant_reading_shows_script_english(tmp_path):
    from livecast_hud.pipeline import EventLog, Pipeline
    from livecast_hud.stt import OfflineScriptSTT
    by_id = script_by_id()
    heard = by_id["s05"]["ko"].replace("오후 2시부터 5시까지", "십사 시부터 십칠 시까지")
    wav = wav_file(tmp_path / "v.wav", 2.0)
    (tmp_path / "v.timing.json").write_text(json.dumps({"sentences": [{"id": "s05", "start": 0.2, "end": 1.5, "ko": heard}]},
                                                       ensure_ascii=False), encoding="utf-8")
    w, done = FakeWindow(), threading.Event()
    pipe = Pipeline(w, translator(), EventLog(None))
    OfflineScriptSTT(str(wav)).start(on_partial=pipe.on_partial, on_final=pipe.on_final, on_end=done.set)
    assert done.wait(5)
    assert [x for x in w.shown if x[2]] == [(heard, by_id["s05"]["en"], True)]


# ---------- Cloud streaming engine with fake clients ----------
def fake_speech(client_cls):
    return types.SimpleNamespace(
        SpeechClient=client_cls, SpeechContext=lambda **kw: kw, StreamingRecognitionConfig=lambda **kw: kw,
        StreamingRecognizeRequest=lambda **kw: kw,
        RecognitionConfig=type("RC", (), {"__init__": lambda self, **kw: None,
                                          "AudioEncoding": types.SimpleNamespace(LINEAR16=1)}),
    )


def final_response(text):
    alt = types.SimpleNamespace(transcript=text)
    return iter([types.SimpleNamespace(results=[types.SimpleNamespace(alternatives=[alt], is_final=True)])])


def test_gcp_engine_reconnects_after_stream_error(tmp_path, monkeypatch):
    from livecast_hud import stt
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, **kw):
            pass

        def streaming_recognize(self, config, requests):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("network down")
            for _ in requests:   # read the file to the end
                pass
            return final_response("안녕하십니까.")

    monkeypatch.setattr(stt, "_gcp", lambda: (fake_speech(FakeClient), lambda **kw: kw))
    monkeypatch.setattr(stt, "get_gcp_credentials", lambda account=None: None)
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)
    states, finals, done = [], [], threading.Event()
    eng = stt.LivecastSTTEngine(FAKE_PROJECT, audio_path=str(wav_file(tmp_path / "d.wav", 0.5)), account="")
    eng.start(on_final=finals.append, on_status=states.append, on_end=done.set)
    try:
        assert done.wait(5)
    finally:
        eng.stop()
    assert states == ["disconnected", "connected"] and finals == ["안녕하십니까."] and calls["n"] == 2


def run_engine(tmp_path, monkeypatch, answers):
    """Run the file-mode engine on fake Cloud libraries. Tokens come from answers in order (None when used up); a
    client cannot be built without a token (token and ADC both failing) and the first stream drops (expired token).
    Returns: tokens fetched, (token, quota project) per opened stream, status changes, finals"""
    from livecast_hud import stt
    tokens, fetched, opened = iter(answers), [], []

    def fake_credentials(account=None):
        fetched.append(next(tokens, None))
        return fetched[-1]

    class FakeClient:
        def __init__(self, credentials, client_options):
            if credentials is None:
                raise RuntimeError("no default credentials")
            self.creds, self.opts = credentials, client_options

        def streaming_recognize(self, config, requests):
            opened.append((self.creds, self.opts["quota_project_id"]))
            if len(opened) == 1:
                raise ConnectionError("UNAUTHENTICATED: token expired")
            for _ in requests:
                pass
            return final_response("안녕하십니까.")

    monkeypatch.setattr(stt, "_gcp", lambda: (fake_speech(FakeClient), lambda **kw: kw))
    monkeypatch.setattr(stt, "get_gcp_credentials", fake_credentials)
    monkeypatch.setattr(stt.time, "sleep", lambda s: None)
    states, finals, done = [], [], threading.Event()
    eng = stt.LivecastSTTEngine(FAKE_PROJECT, audio_path=str(wav_file(tmp_path / "d.wav", 0.5)), account="")
    eng.start(on_final=finals.append, on_status=states.append, on_end=done.set)
    try:
        assert done.wait(5)
    finally:
        eng.stop()   # stop before the fakes are undone, so no leftover thread reaches real credentials
    return fetched, opened, states, finals


def test_reconnect_fetches_new_token_and_builds_new_client(tmp_path, monkeypatch):
    fetched, opened, states, finals = run_engine(tmp_path, monkeypatch, ["cred-1", "cred-2"])
    assert fetched == ["cred-1", "cred-2"]                        # once at start + once on reconnect
    assert opened == [("cred-1", FAKE_PROJECT), ("cred-2", FAKE_PROJECT)]
    assert states == ["disconnected", "connected"] and finals == ["안녕하십니까."]


def test_reconnect_keeps_retrying_when_client_rebuild_fails(tmp_path, monkeypatch):
    fetched, opened, states, finals = run_engine(tmp_path, monkeypatch, ["cred-1", None, "cred-3"])
    assert fetched == ["cred-1", None, "cred-3"]
    assert opened == [("cred-1", FAKE_PROJECT), ("cred-3", FAKE_PROJECT)]
    assert states == ["disconnected", "connected"] and finals == ["안녕하십니까."]


class FakeSpeechMod:
    """Fake google.cloud.speech: records the chunks of each stream and advances the clock 0.1 s per chunk"""

    def __init__(self, clock):
        self.clock, self.streams = clock, []
        mod = self

        class RecognitionConfig:
            class AudioEncoding:
                LINEAR16 = 1

            def __init__(self, **kw):
                pass

        class Request:
            def __init__(self, audio_content):
                self.audio_content = audio_content

        class Client:
            def __init__(self, credentials=None, client_options=None):
                pass

            def streaming_recognize(self, config, requests):
                got = []
                mod.streams.append(got)
                for r in requests:
                    got.append(r.audio_content)
                    mod.clock.t += 0.1
                    yield type("Resp", (), {"results": []})()

        self.RecognitionConfig, self.StreamingRecognizeRequest, self.SpeechClient = RecognitionConfig, Request, Client
        self.SpeechContext = self.StreamingRecognitionConfig = lambda **kw: kw


@pytest.mark.parametrize("minutes,streams,tokens", [(6, 2, 1), (50, 12, 2)])
def test_gcp_stream_rotates_before_5min_without_gap_or_new_token(monkeypatch, capsys, minutes, streams, tokens):
    from livecast_hud import stt
    clock = FakeClock()
    mod = FakeSpeechMod(clock)
    calls = []
    monkeypatch.setattr(stt, "_gcp", lambda: (mod, lambda **kw: kw))
    monkeypatch.setattr(stt, "get_gcp_credentials", lambda account=None: calls.append(1))
    eng = stt.LivecastSTTEngine(FAKE_PROJECT, account="")
    eng.source, eng.clock, eng.is_running = object(), clock, True     # like file input (no microphone)
    states = []
    eng.on_status_callback = states.append
    chunks = [i.to_bytes(4, "big") for i in range(minutes * 600)]   # 0.1 s chunks
    for c in chunks:
        eng.audio_q.put(c)
    eng.audio_q.put(None)
    eng._run_loop()
    assert len(mod.streams) == streams and eng.rotations == streams - 1
    assert all(abs(len(x) - 2700) <= 1 for x in mod.streams[:-1])               # rotate every 270 s (±1 chunk rounding)
    assert [c for x in mod.streams for c in x] == chunks                         # no gap, same order
    assert len(calls) == tokens and "disconnected" not in states                 # new token only after 45 min, no drop shown
    assert FAKE_PROJECT not in capsys.readouterr().out


# ---------- values never printed, credentials ----------
def test_redact_and_stt_prints_hide_values():
    from livecast_hud import stt
    msg = f"PERMISSION_DENIED project {FAKE_PROJECT} account {FAKE_ACCOUNT} Bearer {FAKE_TOKEN} {FAKE_TOKEN}"
    r = stt.redact(msg, (FAKE_PROJECT, FAKE_ACCOUNT, None))
    assert FAKE_PROJECT not in r and FAKE_ACCOUNT not in r and "FAKE-token" not in r and "PERMISSION_DENIED" in r
    src = Path(stt.__file__).read_text(encoding="utf-8")
    assert "{self.project_id}" not in src and "{e}" not in src           # project never printed, errors go through redact


def test_no_account_means_adc_without_running_gcloud(monkeypatch):
    from livecast_hud import stt
    monkeypatch.delenv("LIVECAST_GCP_ACCOUNT", raising=False)
    assert stt.get_gcp_credentials(None) is None and stt.get_gcp_credentials("") is None   # audit hook: no subprocess
    assert stt.LivecastSTTEngine(FAKE_PROJECT).account is None
    monkeypatch.setenv("LIVECAST_GCP_ACCOUNT", FAKE_ACCOUNT)
    assert stt.LivecastSTTEngine(FAKE_PROJECT).account == FAKE_ACCOUNT
