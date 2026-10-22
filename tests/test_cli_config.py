# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Command line and config file: options, errors that stop before any call, demo playback, example data."""
import io
import json
from pathlib import Path

import pytest
from fakes import EXAMPLES, ROOT, FakeWindow, config, translator


def block_live_paths(monkeypatch):
    """Everything past argument and config checks fails at once: if a guard in cli.main breaks, the test fails
    instead of opening a window, an event loop, the microphone or Google Cloud"""
    import sys
    import types

    import PyQt6.QtWidgets
    from livecast_hud import cli, overlay, stt, translate

    def blocked(name):
        def _raise(*args, **kwargs):
            raise AssertionError(f"cli.main reached {name} (a guard did not stop it)")
        return _raise

    mic = types.ModuleType("sounddevice")
    mic.__getattr__ = blocked("sounddevice (microphone)")
    monkeypatch.setitem(sys.modules, "sounddevice", mic)
    monkeypatch.setattr(PyQt6.QtWidgets, "QApplication", blocked("QApplication (event loop)"))
    monkeypatch.setattr(overlay, "SubtitleOverlayWindow", blocked("SubtitleOverlayWindow"))
    monkeypatch.setattr(cli, "GcpTranslator", blocked("GcpTranslator"))
    for name in ("LivecastSTTEngine", "OfflineScriptSTT", "list_input_devices", "get_gcp_credentials", "_gcp"):
        monkeypatch.setattr(stt, name, blocked(f"stt.{name}"))
    for name in ("GcpTranslator", "adc_token", "gcloud_token", "urllib_post"):
        monkeypatch.setattr(translate, name, blocked(f"translate.{name}"))
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)          # keep pytest's Ctrl+C handler


def run_main(argv, capsys, monkeypatch):
    from livecast_hud import cli
    for name in ("LIVECAST_CONFIG", "LIVECAST_GCP_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    block_live_paths(monkeypatch)
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    return e.value.code, capsys.readouterr()


# ---------- options ----------
def test_no_click_through_after_run_live_flags_wins():
    from livecast_hud.cli import build_parser
    p = build_parser()
    live = (ROOT / "scripts" / "run_live.sh").read_text(encoding="utf-8")
    assert '--click-through "$@"' in live and "LIVECAST_GCP_CONFIRMED" in live   # user arguments come after the defaults
    d = p.parse_args([])
    assert (d.click_through, d.talk, d.mt, d.mode, d.stt, d.config, d.gcp_project) == (False, None, "script", "demo", "offline", None, None)
    args = p.parse_args(["--mode", "live", "--stt", "gcp", "--click-through", "--device", "1", "--mt", "gcp", "--talk", "1",
                         "--no-click-through"])
    assert (args.click_through, args.mt, args.talk) == (False, "gcp", 1)
    assert p.parse_args(["--click-through"]).click_through is True


def test_removed_and_bounded_options():
    from livecast_hud.cli import build_parser
    p = build_parser()
    for bad in (["--mt", "web"], ["--talk", "0"], ["--talk", "10"]):
        with pytest.raises(SystemExit):
            p.parse_args(bad)


def test_version(capsys):
    from livecast_hud import __version__
    from livecast_hud.cli import build_parser
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--version"])
    assert capsys.readouterr().out.strip() == f"livecast-hud {__version__}" and __version__ == "0.1.0"


def test_read_commands_from_terminal_lines():
    from livecast_hud.cli import read_commands
    got = []
    read_commands(io.StringIO("2\nx\n 3 \nK\n10\n1\n9\n0\n"), got.append)
    assert got == ["2", "3", "k", "1", "9"]


# ---------- errors stop before any window or call ----------
@pytest.mark.parametrize("argv", [["--mt", "gcp"], ["--mode", "live", "--stt", "gcp"], ["--mode", "file", "--audio", "x.wav", "--stt", "gcp"]])
def test_gcp_without_project_stops_with_one_line(argv, capsys, monkeypatch):
    code, out = run_main(argv, capsys, monkeypatch)
    assert code.startswith("livecast-hud: error:") and "--gcp-project" in code and "LIVECAST_GCP_PROJECT" in code
    assert "\n" not in code and out.out == ""


@pytest.mark.parametrize("argv,needle", [
    (["--config", "missing.json"], "cannot read config"),
    (["--config", str(EXAMPLES), "--talk", "4"], "talk 4 is not in the config"),
    (["--config", str(EXAMPLES), "--talk", "2"], "demo mode needs a script for talk 2"),
    ([], "demo mode needs a script for talk 1"),
])
def test_config_problems_stop_with_one_line(argv, needle, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    code, out = run_main(argv, capsys, monkeypatch)
    assert code.startswith("livecast-hud: error:") and needle in code and out.out == ""


@pytest.mark.parametrize("argv", [["--mode", "live", "--stt", "gcp", "--gcp-project", "proj-fake-000"],
                                  ["--config", str(EXAMPLES), "--mt", "gcp", "--gcp-project", "proj-fake-000"],
                                  ["--list-devices"]])
def test_paths_past_the_guards_are_blocked_in_tests(argv, capsys, monkeypatch):
    """The blocking in run_main is in force: valid live arguments stop at the first blocked call, not at a device"""
    with pytest.raises(AssertionError, match="a guard did not stop it"):
        run_main(argv, capsys, monkeypatch)


def test_conftest_blocks_devices_and_network():
    """The autouse fixture in conftest is in force for every test (optional modules checked when installed)"""
    import importlib
    import importlib.util
    import socket
    import sys

    from livecast_hud import translate

    def connect():
        with socket.socket() as s:
            s.connect(("192.0.2.1", 9))

    calls = [lambda: socket.create_connection(("192.0.2.1", 9)), connect,
             lambda: translate.urllib_post("http://192.0.2.1", {}, b"", 1)]
    if "sounddevice" in sys.modules:
        calls += [lambda: sys.modules["sounddevice"].RawInputStream(), lambda: sys.modules["sounddevice"].InputStream()]
    if importlib.util.find_spec("google.auth"):
        calls.append(lambda: importlib.import_module("google.auth").default())
    if importlib.util.find_spec("requests"):
        calls.append(lambda: importlib.import_module("requests").Session().request("POST", "http://192.0.2.1"))
    if importlib.util.find_spec("google") and importlib.util.find_spec("google.cloud") and importlib.util.find_spec("google.cloud.speech"):
        calls.append(lambda: importlib.import_module("google.cloud.speech").SpeechClient())
    assert len(calls) >= 3
    for call in calls:
        with pytest.raises(AssertionError, match="blocked in tests"):
            call()


def test_mode_combinations_are_checked(capsys, monkeypatch):
    for argv in (["--mode", "file"], ["--mode", "live"]):
        code, out = run_main(argv, capsys, monkeypatch)
        assert code == 2 and "error" in out.err


# ---------- config file ----------
def test_examples_config_loads_and_connects():
    from livecast_hud.config import LIST_FIELDS
    c = config()
    assert c.talk_numbers() == [1, 2, 3] and c.default_talk() == 1 and all(c.name(n) for n in c.talk_numbers())
    assert all(isinstance(x, list) for f in LIST_FIELDS for x in [c.merged(f, 1)])
    assert [s["id"] for s in c.demo(1)] == c.talks[1]["demo"] and all(s["en"] for s in c.demo(1))
    assert len(c.script(1)) >= 10 and c.script(2) == [] and c.demo(2) == []
    for n in c.talk_numbers():
        tr = translator(talk=n)
        assert tr.vocab.normalize("가") == "가" and tr.script_first is bool(c.script(n))


def test_script_paths_are_relative_to_the_config_file(tmp_path):
    from livecast_hud.config import Config
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "t.json").write_text(json.dumps({"sentences": [{"id": "a", "ko": "가나다", "en": None}]}), encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps({"talks": {"4": {"script": "s/t.json"}}}), encoding="utf-8")
    c = Config.load(tmp_path / "c.json")
    assert c.default_talk() == 4 and c.script(4)[0]["id"] == "a" and c.demo(4) == []


@pytest.mark.parametrize("data,needle", [
    ({"talks": []}, "'talks' must be an object"),
    ({"talks": {"one": {}}}, "talk keys"),
    ({"talks": {"10": {}}}, "talk keys"),
    ({"glossary": {"ko": "가"}}, "'glossary' must be a list"),
    ({"talks": {"1": {"hints": "가"}}}, "'hints' must be a list"),
])
def test_bad_config_shapes_raise_config_error(data, needle):
    from livecast_hud.config import Config, ConfigError
    with pytest.raises(ConfigError, match=needle):
        Config(data)


def test_missing_or_broken_files_raise_config_error(tmp_path):
    from livecast_hud.config import Config, ConfigError, load_script
    (tmp_path / "bad.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "noid.json").write_text(json.dumps({"sentences": [{"ko": "가"}]}), encoding="utf-8")
    for call in (lambda: Config.load(tmp_path / "none.json"), lambda: Config.load(tmp_path / "bad.json"),
                 lambda: load_script(tmp_path / "noid.json"),
                 lambda: Config({"talks": {"1": {"script": "x.json", "demo": []}}}, tmp_path).script(1)):
        with pytest.raises(ConfigError):
            call()
    c = Config({"talks": {"1": {"script": "noid.json"}}}, tmp_path)
    with pytest.raises(ConfigError):
        c.demo(1)


def test_demo_ids_must_exist(tmp_path):
    from livecast_hud.config import Config, ConfigError
    (tmp_path / "t.json").write_text(json.dumps({"sentences": [{"id": "a", "ko": "가나다", "en": "abc"}]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="zz"):
        Config({"talks": {"1": {"script": "t.json", "demo": ["a", "zz"]}}}, tmp_path).demo(1)


def test_examples_are_fictional_and_documented():
    """Example speakers are placeholders; every config field used by the examples is described in docs/configuration.md"""
    data = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    assert {t["speaker"] for t in data["talks"].values()} == {"Speaker A", "Speaker B", "Speaker C"}
    doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    fields = set(data) | {k for t in data["talks"].values() for k in t} | {k for g in config().merged_all_talks("glossary") for k in g}
    assert all(f"`{f}`" in doc for f in fields), sorted(f for f in fields if f"`{f}`" not in doc)


# ---------- demo playback ----------
def test_demo_scenarios_use_script_sentences_verbatim():
    by_id = {s["id"]: s for s in config().script(1)}
    demo = config().demo(1)
    assert len(demo) >= 5
    for d in demo:
        assert d == by_id[d["id"]] and d["en"]


def test_demo_mode_plays_once_and_ends_when_not_looping():
    from livecast_hud.cli import run_demo_mode
    from livecast_hud.pipeline import EventLog, Pipeline
    import threading
    w, ended = FakeWindow(), threading.Event()
    pipe = Pipeline(w, translator(), EventLog(None))
    pipe.DEBOUNCE = 0.0
    t = run_demo_mode(pipe, config().demo(1), loop=False, on_end=ended.set, sleep=lambda s: None)
    t.join(5)
    assert ended.is_set()
    finals = [(ko, en) for ko, en, final in w.shown if final]
    assert finals == [(s["ko"], s["en"]) for s in config().demo(1)]     # each sentence once, with its script English
    assert pipe.stats["matched"] == len(finals)


def test_package_entry_points():
    tomllib = pytest.importorskip("tomllib")                              # Python 3.11+
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["name"] == "livecast-hud" and meta["version"] == "0.1.0" and meta["requires-python"] == ">=3.10"
    assert meta["scripts"] == {"livecast-hud": "livecast_hud.cli:main"} and meta["license"] == "Apache-2.0"
    assert Path(ROOT / "src" / "livecast_hud" / "__main__.py").exists()
