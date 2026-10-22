# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""--mt gcp with a fake Cloud Translation and fake tokens: request shape, token refresh, cache, rate limit, drops,
late results, script-first talks, committing closed sentences inside partials. Zero external calls (conftest)."""
import threading
import time

import pytest
from fakes import FAKE_PROJECT, FAKE_TOKEN, DictHTTP, FakeClock, FakeHTTP, FakeWindow, gcp, pipeline, script_by_id, translator


# ---------- text to send ----------
@pytest.mark.parametrize("text,final,want", [
    ("첫 문장입니다. 두 번째 문장은 지금 말하는 중", False, "두 번째 문장은 지금 말하는 중"),       # open fragment after the mark
    ("표지 없이 계속 말하는 중입니다만", False, "표지 없이 계속 말하는 중입니다만"),                 # no mark = everything
    ("첫 문장입니다. 두 번", False, ""),                                                            # under 8 chars after a closed sentence
    ("첫 문장입니다? 둘", False, ""),
    ("끝난 문장입니다.", False, ""),
    ("짧은 말", False, "짧은 말"),                                                                   # short is fine with nothing closed
    ("씨앗 하나당 약 4.82초 걸렸고 계속", False, "씨앗 하나당 약 4.82초 걸렸고 계속"),               # a decimal point is not an end
    ("하나. 둘입니다. 셋째입니다.", True, "둘입니다. 셋째입니다."),                                 # final: two sentences (≤ 80)
    ("앞 " * 40 + "문장입니다. 마지막 문장입니다.", True, "마지막 문장입니다."),                     # over 80 together → one
    ("", False, ""),
])
def test_mt_segment(text, final, want):
    from livecast_hud.translate import mt_segment
    assert mt_segment(text, final) == want


def test_mt_segment_long_fragment_cut_at_word_boundary():
    from livecast_hud.translate import MT_MAX_CHARS, mt_segment
    text = "앞 문장입니다. " + " ".join(f"어절{i:02d}" for i in range(30))
    seg = mt_segment(text, False)
    assert len(seg) <= MT_MAX_CHARS and text.endswith(seg) and seg.startswith("어절") and text[-len(seg) - 1] == " "


# ---------- Cloud Translation client ----------
def test_gcp_request_shape_and_timeout():
    http = FakeHTTP((200, "Good morning."))
    assert gcp(http).translate("안녕하십니까.") == "Good morning."
    c = http.calls[0]
    assert c["url"] == f"https://translation.googleapis.com/v3/projects/{FAKE_PROJECT}/locations/global:translateText"
    assert c["headers"] == {"Authorization": f"Bearer {FAKE_TOKEN}", "x-goog-user-project": FAKE_PROJECT,
                            "Content-Type": "application/json"}
    assert c["body"] == {"contents": ["안녕하십니까."], "sourceLanguageCode": "ko", "targetLanguageCode": "en", "mimeType": "text/plain"}
    assert c["timeout"] == 1.5


def test_gcp_token_refetch_after_45_minutes_and_on_401_once():
    from livecast_hud.translate import GcpTranslator
    clock, fetched = FakeClock(), []

    def tokens():
        fetched.append(f"tok-{len(fetched)}")
        return fetched[-1]

    http = FakeHTTP((200, "a"), (200, "b"), (401, ""), (200, "c"), (401, ""), (401, ""))
    g = GcpTranslator(project=FAKE_PROJECT, post=http, token_provider=tokens, clock=clock)
    assert g.translate("가") == "a" and fetched == ["tok-0"]
    clock.t += 45 * 60                                                   # 45 minutes → fetch again
    assert g.translate("나") == "b" and fetched == ["tok-0", "tok-1"]
    assert g.translate("다") == "c" and fetched == ["tok-0", "tok-1", "tok-2"]   # 401 → new token, one retry
    assert g.translate("라") is None and len(fetched) == 4 and g.last_error == "HTTP 401"   # only one retry
    assert len(http.calls) == 6


def test_gcp_failures_return_none_without_values():
    for http, tokens, err in [(FakeHTTP(TimeoutError("timed out")), None, "TimeoutError"),
                              (FakeHTTP((500, "")), None, "HTTP 500"),
                              (FakeHTTP(), [None], "no token")]:
        g = gcp(http, tokens=tokens)
        assert g.translate("가나다라마바사") is None and g.last_error == err
        assert FAKE_TOKEN not in g.last_error and FAKE_PROJECT not in g.last_error


def test_gcp_translator_needs_a_project_and_picks_a_token_source(monkeypatch):
    from livecast_hud import translate
    with pytest.raises(ValueError):
        translate.GcpTranslator(project="")
    monkeypatch.delenv("LIVECAST_GCP_ACCOUNT", raising=False)
    assert translate.default_token_provider() is translate.adc_token
    calls = []
    monkeypatch.setattr(translate, "gcloud_token", lambda account: calls.append(account) or "tok")
    monkeypatch.setenv("LIVECAST_GCP_ACCOUNT", "someone@example.test")
    assert translate.default_token_provider()() == "tok" and calls == ["someone@example.test"]


def test_translate_gcp_cache_per_talk_terms_and_fix_en():
    http = FakeHTTP((200, "HoneyLoop and Honey Loop and qr code"), (200, "x"))
    tr = translator(mode="gcp", talk=2, gcp=gcp(http))
    assert tr.translate_gcp("허니 루프와 꿀벌 지도, 큐알 코드") == "HoneyLoop and HoneyLoop and QR code"     # fix_en and casing
    assert http.calls[0]["body"]["contents"] == ["HoneyLoop와 꿀벌 지도, QR code"]     # proper nouns only; 'Bee Map' is marked common
    assert tr.translate_gcp("허니 루프와 꿀벌 지도, 큐알 코드") == "HoneyLoop and HoneyLoop and QR code" and len(http.calls) == 1   # cache
    tr.set_talk(3)
    tr.translate_gcp("허니 루프와 꿀벌 지도, 큐알 코드")
    assert len(http.calls) == 2 and http.calls[1]["body"]["contents"] == ["허니 루프와 꿀벌 지도, QR code"]   # per-talk cache and glossary


def test_script_mode_makes_no_gcp_translator():
    from livecast_hud.translate import LivecastTranslator
    assert translator().gcp is None
    with pytest.raises(ValueError):
        LivecastTranslator(mode="gcp")


# ---------- pipeline: 0.8 s limit, drops, late results, script-first talks ----------
def test_partial_mt_at_most_every_0_8s_and_final_always():
    clock = FakeClock()
    http = FakeHTTP()
    pipe, w = pipeline(http, clock=clock)
    for k in range(3):
        pipe._emit("partial", f"옥상 벌통은 올해 {k}번째로", final=False)
        clock.t += 0.3
    assert len(http.calls) == 1
    clock.t += 0.5                                                        # 1.1 s after the previous request
    pipe._emit("partial", "옥상 벌통은 올해 두 배로 늘었습니다 그리고", final=False)
    assert len(http.calls) == 2
    pipe._emit("final", "옥상 벌통은 올해 두 배로 늘었습니다.", final=True)       # finals go right away
    assert len(http.calls) == 3 and w.shown[-1][2] is True


def test_failure_keeps_previous_english_and_shows_disconnected(capsys):
    http = FakeHTTP((200, "The beehives doubled."), (403, ""), (403, ""), (200, "Back again."))
    pipe, w = pipeline(http)
    pipe._emit("final", "벌통이 두 배로 늘었습니다.", final=True)
    pipe._emit("final", "이 문장은 번역이 실패합니다.", final=True)
    assert w.shown[-1][1] == "The beehives doubled." and w.links == ["connected", "disconnected"]
    pipe._emit("final", "다시 연결됩니다.", final=True)
    assert w.shown[-1][1] == "Back again." and w.links[-1] == "connected"
    out = capsys.readouterr().out
    assert "HTTP 403" in out and FAKE_TOKEN not in out and FAKE_PROJECT not in out


def test_late_result_is_dropped_when_newer_english_shown():
    pipe, w = pipeline(FakeHTTP())
    old = {"seq": 1, "seg": "가", "final": False, "talk": 2}
    pipe.seq = 2
    pipe._mt_done({"seq": 2, "seg": "나", "final": False, "talk": 2}, "newer")
    n = len(w.shown)
    pipe._mt_done(old, "older")
    assert len(w.shown) == n and pipe.en == "newer" and pipe.stats["mt_dropped"] == 1
    pipe._mt_done({"seq": 2, "seg": "나", "final": False, "talk": 3}, "other talk")   # arrived after a talk switch
    assert pipe.en == "newer" and pipe.stats["mt_dropped"] == 2


def test_worker_keeps_latest_partial_and_all_finals():
    from livecast_hud.pipeline import MTWorker
    gate, seen, done = threading.Event(), [], threading.Event()

    def run(req):
        if req["n"] == 0:
            gate.wait(2)
        seen.append(req["n"])
        if req["n"] == 4:
            done.set()
        return "en"

    wk = MTWorker(run, lambda req, en: None)
    wk.submit({"n": 0, "final": False})
    time.sleep(0.05)                                                      # while request 0 runs
    for n, final in ((1, False), (2, True), (3, False), (4, False)):
        wk.submit({"n": n, "final": final})
    gate.set()
    assert done.wait(2)
    assert seen == [0, 2, 4]                                              # old partials 1 and 3 dropped, final 2 first


def test_script_talk_tries_script_first_then_translation():
    by_id = script_by_id()
    http = FakeHTTP((200, "Off-script English."))
    pipe, w = pipeline(http, talk=1)
    pipe._emit("final", by_id["s05"]["ko"], final=True)
    assert len(http.calls) == 0 and w.shown[-1] == (by_id["s05"]["ko"], by_id["s05"]["en"], True)
    pipe._emit("partial", by_id["s06"]["ko"][:20], final=False)          # start of a script sentence
    assert len(http.calls) == 0
    pipe._emit("final", "오늘 점심 메뉴는 비빔밥이었습니다.", final=True)
    assert len(http.calls) == 1 and w.shown[-1][1] == "Off-script English."


@pytest.mark.parametrize("talk", [2, 3])
def test_talks_without_script_do_not_use_another_talks_script(talk):
    by_id = script_by_id()
    http = FakeHTTP((200, "Machine English."))
    pipe, w = pipeline(http, talk=talk)
    pipe._emit("final", by_id["s05"]["ko"], final=True)
    assert len(http.calls) == 1 and w.shown[-1][1] == "Machine English." and pipe.stats["matched"] == 0


def test_set_talk_switches_script_first_and_window_and_ignores_unknown_talks(capsys):
    pipe, w = pipeline(FakeHTTP(), talk=2)
    assert not pipe.translator.script_first
    pipe.set_talk(1)
    assert pipe.translator.talk == 1 and pipe.translator.script_first and w.talks == [1]
    pipe.set_talk(7)
    assert pipe.translator.talk == 1 and w.talks == [1] and "not in the config" in capsys.readouterr().out


def test_blank_script_rows_use_mt_on_gcp_but_stay_blank_on_script():
    from livecast_hud.pipeline import EventLog, Pipeline
    by_id = script_by_id()
    blank = [i for i, s in by_id.items() if not s["en"]]
    assert blank == ["s10"]
    for sid in blank:
        http = FakeHTTP((200, "Machine English."))
        pipe, w = pipeline(http, talk=1)
        pipe._emit("final", by_id[sid]["ko"], final=True)
        assert len(http.calls) == 1 and w.shown[-1][1:] == ("Machine English.", True), sid
        ws = FakeWindow()
        Pipeline(ws, translator(), EventLog(None))._emit("final", by_id[sid]["ko"], final=True)
        assert ws.shown[-1][1] == "", sid


def test_name_fixes_only_on_gcp_path():
    from livecast_hud.pipeline import EventLog, Pipeline
    http = DictHTTP({"마을 단체": "Brindelwood is a village group."})
    pipe, w = pipeline(http)
    pipe._emit("final", "블린들우드 커먼즈는 마을 단체입니다.", final=True)
    assert "블린들우드" not in http.calls[0]["ko"]
    assert w.shown[-1] == ("브린들우드 커먼즈는 마을 단체입니다.", "Brindlewood is a village group.", True)
    ws = FakeWindow()
    Pipeline(ws, translator(talk=2), EventLog(None))._emit("final", "블린들우드 커먼즈는 마을 단체입니다.", final=True)
    assert ws.shown[-1][0] == "블린들우드 커먼즈는 마을 단체입니다."                   # --mt script shows what was heard


# ---------- short fragments ----------
@pytest.mark.parametrize("piece,final_text", [(" 반.", "반갑습니다."), (" 정.", "정보를 모읍니다."), (" 를.", "를.")])
def test_short_piece_not_translated_and_final_uses_final_text(piece, final_text):
    http = DictHTTP({"반갑습니다": "Nice to meet you.", "정보를": "We collect information."})
    clock = FakeClock()
    pipe, w = pipeline(http, clock=clock)
    pipe._emit("partial", piece, final=False)                                  # one syllable plus an automatic period
    assert http.calls == [] and pipe.stats["closed"] == 0
    clock.t += 1.0
    pipe._emit("final", piece, final=True)                                    # a short final is not translated either
    assert http.calls == [] and all(en == "" for _, en, _ in w.shown)
    clock.t += 1.0
    pipe._emit("final", final_text, final=True)
    if final_text == "를.":
        assert http.calls == []
    else:
        assert [c["ko"] for c in http.calls] == [final_text] and w.shown[-1][1:] == (http.table[final_text.split()[0].rstrip(".")], True)


# ---------- committing sentences closed inside partials ----------
STEPS_TABLE = {"첫 문장을 말합니다.": "First is said.", "첫 문장을 말하는": "First being", "둘째 문장도 말합니다.": "Second is said.",
               "둘째 문장도 말하는": "Second being", "셋째 문장을 끝냅니다.": "Third is done.", "셋째 문장을 끝내는": "Third ending"}
A, B, C = "가나다 첫 문장을 말합니다.", "둘째 문장도 말합니다.", "셋째 문장을 끝냅니다."
STEPS = ["가나다 첫 문장을 말하는", f"{A} 둘", f"{A} 둘째 문장도 말하는", f"{A} {B} 셋째 문장을 끝내는", f"{A} {B} {C}", f"{A} {B} {C} 넷"]


def test_partials_only_fill_committed_box_with_closed_sentences(qapp):
    """No finals, only partials with 3 end marks → closed sentences go up in order (last 2 lines) and never change"""
    from livecast_hud.overlay import SubtitleOverlayWindow
    http = DictHTTP(STEPS_TABLE)
    clock = FakeClock()
    w = SubtitleOverlayWindow()
    w.show()
    pipe, _ = pipeline(http, clock=clock, window=w)
    seen, kept = [], []
    for t in STEPS:
        clock.t += 1.0
        pipe._emit("partial", t, final=False)
        qapp.processEvents()
        seen.append((w.en_lines(), w.draft_lines()))
        kept.append(list(w.committed))
    assert seen[0] == ([], ["First being"])
    assert seen[1] == (["First is said."], [])                                          # closed: its translation is committed, draft empty
    assert seen[2] == (["First is said."], ["Second being"])
    assert seen[3] == (["First is said.", "Second is said."], ["Third ending"])
    assert seen[4] == seen[3]                                                             # a period at the very end may be temporary
    assert seen[5] == (["Second is said.", "Third is done."], [])                       # committed once speech continues
    for before, after in zip(kept, kept[1:]):                                             # committed sentences only get appended
        assert after[:len(before)] == before
    assert pipe.stats["final"] == 0 and pipe.stats["closed"] == 3
    pipe._emit("final", f"{A} {B} {C}", final=True)                                      # a late final does not commit them again
    qapp.processEvents()
    assert (w.en_lines(), w.draft_lines()) == seen[5] and w.committed == kept[5] and len(http.calls) == 6


def test_worker_thread_keeps_every_closed_sentence_in_order():
    """Even when translations arrive after the next partial (worker thread), all 3 closed sentences are committed in order"""
    from livecast_hud.pipeline import EventLog, Pipeline

    class SlowHTTP(DictHTTP):
        def __call__(self, *a, **k):
            time.sleep(0.05)
            return super().__call__(*a, **k)

    http = SlowHTTP(STEPS_TABLE)
    clock, w = FakeClock(), FakeWindow()
    pipe = Pipeline(w, translator(mode="gcp", talk=2, gcp=gcp(http)), EventLog(None), clock=clock)
    for t in STEPS:
        clock.t += 1.0
        pipe._emit("partial", t, final=False)
        time.sleep(0.02)                                                  # faster than the 0.05 s translation
    for _ in range(100):
        if len([x for x in w.shown if x[2]]) >= 3:
            break
        time.sleep(0.02)
    assert [x[1] for x in w.shown if x[2]] == ["First is said.", "Second is said.", "Third is done."]


def test_closed_sentence_reuses_full_translation_and_falls_back_on_failure():
    http = DictHTTP({"전부 번역된 문장입니다": "All of it.", "앞부분만": "Only the start", "다음 조각을 말하는": "Next going"},
                    fail=("앞부분만 번역된 문장입니다.",))
    clock = FakeClock()
    pipe, w = pipeline(http, clock=clock)
    pipe._emit("partial", "전부 번역된 문장입니다", final=False)
    clock.t += 1.0
    pipe._emit("partial", "전부 번역된 문장입니다. 다음 조각을 말하는", final=False)
    assert [x[1:] for x in w.shown if x[2]] == [("All of it.", True)] and len(http.calls) == 2   # same sentence: not translated again
    clock.t += 1.0
    pipe._emit("partial", "앞부분만", final=False)                                          # a new utterance
    clock.t += 1.0
    pipe._emit("partial", "앞부분만 번역된 문장입니다. 끝", final=False)                     # closing translation gets 403 → draft committed
    assert w.shown[-1][1:] == ("Only the start", True) and w.links[-1] == "disconnected"


def test_older_final_goes_above_newer_dim_line():
    pipe, w = pipeline(FakeHTTP())
    pipe.seq = 5
    pipe._put("mt", "새 조각", "Newer going", False, None, "새 조각", 5)
    pipe._mt_done({"seq": 3, "seg": "옛 문장.", "final": True, "talk": 2}, "Older done.")
    assert [x[1:] for x in w.shown[-2:]] == [("Older done.", True), ("Newer going", False)] and pipe.open_en == "Newer going"


def test_temporary_period_partials_leave_one_sentence_in_record(qapp):
    """partial "A." → "A B." → "A B C" → final "A B C." leaves 1 committed sentence and no closed-sentence commits"""
    from livecast_hud.overlay import SubtitleOverlayWindow
    http = DictHTTP({"함께 씁니다": "Both share the hives.", "나눠 쓰고": "Both share", "모두": "Both."})
    clock, w = FakeClock(), SubtitleOverlayWindow()
    w.show()
    pipe, _ = pipeline(http, clock=clock, window=w)

    def say(kind, ko):
        clock.t += 1.0
        pipe._emit(kind, ko, final=kind == "final")
        qapp.processEvents()
        return w.en_lines(), w.draft_lines()

    assert say("partial", "옥상과 정원 모두.") == ([], [])
    assert say("partial", "옥상과 정원 모두 벌통을.") == ([], [])
    assert say("partial", "옥상과 정원 모두 벌통을 나눠 쓰고") == ([], ["Both share"])
    assert say("final", "옥상과 정원 모두 벌통을 나눠 쓰고 함께 씁니다.") == (["Both share the hives."], [])
    assert pipe.stats["closed"] == 0
    assert [c["ko"] for c in http.calls] == ["옥상과 정원 모두 벌통을 나눠 쓰고", "옥상과 정원 모두 벌통을 나눠 쓰고 함께 씁니다."]


def test_closed_sentence_with_tail_goes_up_once_and_final_adds_only_the_rest(qapp):
    from livecast_hud.overlay import SubtitleOverlayWindow
    from livecast_hud.pipeline import _key
    http = DictHTTP({"첫 문장을 말합니다": "First is said.", "끝입니다": "Second is done.", "둘째 문장을 말하는": "Second going"})
    clock, w = FakeClock(), SubtitleOverlayWindow()
    w.show()
    pipe, _ = pipeline(http, clock=clock, window=w)
    s1 = "첫 문장을 말합니다."
    for text, want in [(f"{s1} 둘째 문장을 말하는", (["First is said."], ["Second going"])),
                       (f"{s1} 둘째 문장을 말하는 중", (["First is said."], ["Second going"]))]:
        clock.t += 1.0
        pipe._emit("partial", text, final=False)
        qapp.processEvents()
        assert (w.en_lines(), w.draft_lines()) == want
    assert pipe.stats["closed"] == 1 and [c["ko"] for c in http.calls].count(s1) == 1
    clock.t += 1.0
    pipe._emit("final", f"{s1} 둘째 문장을 말하는 중 끝입니다.", final=True)
    qapp.processEvents()
    assert (w.en_lines(), w.draft_lines()) == (["First is said.", "Second is done."], [])
    assert list(pipe.closed) == [_key(s1), _key("둘째 문장을 말하는 중 끝입니다.")]
    assert http.calls[-1]["ko"] == "둘째 문장을 말하는 중 끝입니다."
