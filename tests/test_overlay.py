# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""HUD window (offscreen): two committed lines on top, a dimmed draft box below, one Korean line, fixed height,
connection dot, talk switching keys. No real window is shown."""
import pytest
from fakes import DictHTTP, FakeClock, FakeHTTP, gcp, script_by_id, translator

TWO = ["First is said.", "Second is said."]


def window(qapp, *finals):
    from livecast_hud.overlay import SubtitleOverlayWindow
    w = SubtitleOverlayWindow()
    w.show()
    for en in finals:
        w.set_subtitles("확정", en, is_final=True)
    qapp.processEvents()
    return w


def gcp_pipe(w, http):
    from livecast_hud.pipeline import EventLog, Pipeline
    clock = FakeClock()
    return Pipeline(w, translator(mode="gcp", talk=2, gcp=gcp(http)), EventLog(None), clock=clock, mt_inline=True), clock


def key(k):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier)


# ---------- height, screen, connection state ----------
def test_hud_height_fixed_for_long_subtitle_keeping_bottom_edge(qapp):
    """A long English sentence keeps the window height (under the 150 px cap with Korean), shows its last 2 lines,
    and keeps the bottom edge"""
    from livecast_hud.overlay import H_MAX_KO
    by_id = script_by_id()
    long = max((s for s in by_id.values() if s["en"]), key=lambda s: len(s["en"]))
    w = window(qapp)
    w.set_subtitles(by_id["s01"]["ko"], by_id["s01"]["en"], is_final=True)
    qapp.processEvents()
    bottom, h1 = w.geometry().bottom(), w.height()
    w.set_subtitles(long["ko"], " ".join([long["en"]] * 3), is_final=True)
    qapp.processEvents()
    assert h1 == w.height() <= H_MAX_KO and len(w.en_lines()) == 2
    assert w.geometry().bottom() == bottom


def test_hud_screen_index_falls_back_to_primary(qapp):
    from livecast_hud.overlay import SubtitleOverlayWindow
    assert SubtitleOverlayWindow(screen_index=99)._screen_geometry() == qapp.primaryScreen().geometry()
    assert SubtitleOverlayWindow(screen_index=0)._screen_geometry() == qapp.screens()[0].geometry()


def test_hud_keeps_last_subtitle_and_shows_reconnecting(qapp):
    from livecast_hud.overlay import DOT_COLORS, SubtitleOverlayWindow
    w = SubtitleOverlayWindow()
    w.set_subtitles("씨앗은 봄에 나눕니다.", "We share seeds in spring.", is_final=True)
    qapp.processEvents()
    w.set_link_status("disconnected")
    qapp.processEvents()
    assert w.link_state == "disconnected" and DOT_COLORS["disconnected"] in w.corner.text()
    assert w.en_lines() == ["We share seeds in spring."]
    assert w.ko_label.text() == "씨앗은 봄에 나눕니다."
    w.set_link_status("connected")
    qapp.processEvents()
    assert DOT_COLORS["connected"] in w.corner.text()
    assert w.en_lines() == ["We share seeds in spring."]


def test_corner_shows_link_dot_and_talk(qapp):
    from livecast_hud.overlay import DOT_COLORS, SubtitleOverlayWindow
    w = SubtitleOverlayWindow()
    w.set_talk(2)
    w.set_link_status("disconnected")
    qapp.processEvents()
    assert "Talk 2" in w.corner.text() and DOT_COLORS["disconnected"] in w.corner.text()


# ---------- keys and terminal commands ----------
def test_terminal_and_f_keys_reach_talk_and_korean(qapp):
    from PyQt6.QtCore import Qt
    from livecast_hud.overlay import SubtitleOverlayWindow
    w = SubtitleOverlayWindow()
    talks = []
    w.signals.talk_requested.connect(talks.append)
    w.signals.command.emit("2")                                           # terminal 2⏎ (stdin thread → signal)
    w.keyPressEvent(key(Qt.Key.Key_F3))
    w.keyPressEvent(key(Qt.Key.Key_F9))
    w.signals.command.emit("10")                                          # not a talk command
    qapp.processEvents()
    assert talks == [2, 3, 9]
    w.signals.command.emit("k")
    qapp.processEvents()
    assert not w.show_korean and w.ko_label.isHidden()
    w.keyPressEvent(key(Qt.Key.Key_K))
    assert w.show_korean and not w.ko_label.isHidden()


def test_en_only_starts_with_korean_off(qapp):
    """Same call as the --en-only handling in cli.main (set_korean(False))"""
    from pathlib import Path
    from livecast_hud import cli
    from livecast_hud.overlay import SubtitleOverlayWindow
    assert "if args.en_only:\n        window.set_korean(False)" in Path(cli.__file__).read_text(encoding="utf-8")
    w = SubtitleOverlayWindow()
    w.set_korean(False)
    assert not w.show_korean and w.ko_label.isHidden() and cli.build_parser().parse_args(["--en-only"]).en_only


# ---------- committed box and draft box ----------
def test_committed_two_lines_stay_while_partial_changes_draft_only(qapp):
    w = window(qapp)
    for k in range(3):
        w.set_subtitles(f"문장 {k}", f"Sentence {k}.", is_final=True)
    qapp.processEvents()
    top2 = ["Sentence 1.", "Sentence 2."]
    assert w.en_lines() == top2 and w.draft_lines() == []                 # committed box: last 2 lines
    w.set_subtitles("문장 3 말하는 중", "Sentence three is", is_final=False)
    qapp.processEvents()
    assert w.en_lines() == top2 and w.draft_lines() == ["Sentence three is"]   # partials only touch the draft box
    w.set_subtitles("문장 3 말하는 중 더", "Sentence three is still", is_final=False)
    qapp.processEvents()
    assert w.en_lines() == top2 and w.draft_lines() == ["Sentence three is still"]
    w.set_subtitles("문장 3 끝", "Sentence three is done", is_final=True)
    qapp.processEvents()
    assert w.en_lines() == ["Sentence 2.", "Sentence three is done"] and w.draft_lines() == []   # only a final rolls the box


def test_long_sentence_rolls_committed_lines_and_window_caps(qapp):
    from livecast_hud.overlay import H_MAX_EN_ONLY, H_MAX_KO
    w = window(qapp)
    long = " ".join(["This is a long translated sentence that will need more than one line"] * 3)
    w.set_subtitles("가", "Short one.", is_final=True)
    w.set_subtitles("가" * 400, long, is_final=True)
    qapp.processEvents()
    lines = w.en_lines()
    assert len(lines) == 2 and "Short one." not in lines
    screen = w._screen_geometry()
    assert w.height() <= H_MAX_KO and w.geometry().y() + w.height() == screen.y() + screen.height()
    assert w.ko_label.text().startswith("…") and w.ko_label.text().endswith("가")   # Korean line elided on the left
    w.set_korean(False)
    qapp.processEvents()
    assert w.height() <= H_MAX_EN_ONLY and len(w.en_lines()) == 2 and w.geometry().y() + w.height() == screen.y() + screen.height()


def test_partials_never_move_committed_lines(qapp):
    w = window(qapp, *TWO)
    h = w.height()
    assert w.en_lines() == TWO
    for en in ["Third", "Third is", "Third is being said", "Third is being said.", "Third is being said and then some more"]:
        w.set_subtitles("말하는 중", en, is_final=False)
        qapp.processEvents()
        assert w.en_lines() == TWO and w.draft_lines() == [en] and w.height() == h


def test_partial_equal_to_last_committed_is_not_shown_twice(qapp):
    w = window(qapp, *TWO)
    w.set_subtitles("셋째 말하는 중", "Third going", is_final=False)
    w.set_subtitles("셋째", "Third is said.", is_final=True)
    for en in ("Third is said.", "  Third is said. "):
        w.set_subtitles("셋째 다음", en, is_final=False)
        qapp.processEvents()
        assert w.en_lines() == ["Second is said.", "Third is said."] and w.draft_lines() == []
    w.set_subtitles("다음", "Third is", is_final=False)                  # whole-sentence comparison: a shared start still shows
    assert w.draft_lines() == ["Third is"]


def test_pipeline_resend_of_committed_english_stays_out_of_draft(qapp):
    """After a final, a short partial resends the previous English (the one just committed) — not shown twice"""
    w = window(qapp)
    pipe, clock = gcp_pipe(w, DictHTTP({"첫 문장을 말합니다": "First is said."}))
    pipe._emit("final", "첫 문장을 말합니다.", final=True)
    clock.t += 1.0
    pipe._emit("partial", "둘", final=False)
    qapp.processEvents()
    assert pipe.en == "First is said." and w.en_lines() == ["First is said."] and w.draft_lines() == []


def test_long_partial_shows_last_two_lines_then_final_goes_to_bottom(qapp):
    w = window(qapp, "First is said.")
    assert [r.text() for r in w.en_rows] == ["", "First is said."]       # one sentence: bottom line
    long = " ".join(f"word{i}" for i in range(60))
    w.set_subtitles("길게 말하는 중", long, is_final=False)
    qapp.processEvents()
    d = w.draft_lines()
    assert len(d) == 2 and d[-1].endswith("word59") and not d[0].startswith("word0 ") and " ".join(d) in long
    assert w.en_lines() == ["First is said."]
    w.set_subtitles("끝", "Long one is done.", is_final=True)
    qapp.processEvents()
    assert w.draft_lines() == [] and [r.text() for r in w.en_rows] == ["First is said.", "Long one is done."]
    w.set_subtitles("길게 끝", long + ".", is_final=True)                 # a long final shows its last 2 lines
    qapp.processEvents()
    assert len(w.en_lines()) == 2 and w.en_lines()[-1].endswith("word59.")


@pytest.mark.parametrize("korean", [True, False])
def test_height_fixed_empty_or_full_and_under_cap(qapp, korean):
    from livecast_hud.overlay import H_MAX_EN_ONLY, H_MAX_KO
    assert (H_MAX_KO, H_MAX_EN_ONLY) == (150, 130)
    w = window(qapp)
    w.set_korean(korean)
    qapp.processEvents()
    empty, bottom = w.height(), w.geometry().bottom()
    w.set_subtitles("가" * 200, "First is said and it is a long sentence that will wrap onto one more line for sure, "
                    "because it keeps going and going.", is_final=True)
    w.set_subtitles("가" * 200, " ".join(["still speaking"] * 30), is_final=False)
    qapp.processEvents()
    assert len(w.en_lines()) == 2 and len(w.draft_lines()) == 2
    assert w.height() == empty <= (H_MAX_KO if korean else H_MAX_EN_ONLY) and w.geometry().bottom() == bottom
    assert w.ko_label.isHidden() is not korean


def test_plus_scales_both_boxes_and_never_exceeds_caps(qapp):
    """+ grows both boxes alike. Over the cap the draft box drops to 1 line; if 1 line still exceeds it, no growth"""
    from PyQt6.QtCore import Qt
    from livecast_hud.overlay import DRAFT_PX, EN_PX, H_MAX_EN_ONLY, H_MAX_KO
    w = window(qapp, *TWO)
    w.set_subtitles("말", " ".join(["still speaking"] * 30), is_final=False)
    en_h, draft_h = w._en_h, w._draft_h
    w.keyPressEvent(key(Qt.Key.Key_Plus))
    assert w.font_scale > 1.0 and w._en_h > en_h and w._draft_h > draft_h
    for _ in range(8):
        s = w.font_scale
        assert f"font-size: {int(EN_PX * s)}px" in w.styleSheet() and f"font-size: {int(DRAFT_PX * s)}px" in w.styleSheet()
        for korean, cap in ((True, H_MAX_KO), (False, H_MAX_EN_ONLY)):
            w.set_korean(korean)
            assert w.height() <= cap and w.en_lines() == TWO and 1 <= len(w.draft_lines()) <= 2
        w.keyPressEvent(key(Qt.Key.Key_Plus))
    assert w.font_scale < 1.6                                             # the cap stops growth before the 1.6 limit


def test_empty_partial_or_final_clears_draft_only(qapp):
    w = window(qapp, *TWO)
    w.set_subtitles("말", "Third going", is_final=False)
    w.set_subtitles("말", "", is_final=False)
    assert w.draft_lines() == [] and w.en_lines() == TWO
    w.set_subtitles("말", "Third going", is_final=False)
    w.set_subtitles("대본 밖 문장입니다.", "", is_final=True)
    qapp.processEvents()
    assert w.draft_lines() == [] and w.en_lines() == TWO and w.committed == TWO


def test_script_mode_off_script_final_keeps_committed(qapp):
    """--mt script: a script final commits its English, an off-script partial keeps it (not in the draft), an
    off-script final gives empty English"""
    from livecast_hud.pipeline import EventLog, Pipeline
    s = script_by_id()["s07"]
    w = window(qapp)
    pipe = Pipeline(w, translator(), EventLog(None))
    pipe._emit("final", s["ko"], final=True)
    pipe._emit("partial", "전혀 상관없는 즉흥 발언을 하는 중", final=False)
    pipe._emit("final", "전혀 상관없는 즉흥 발언을 했습니다.", final=True)
    qapp.processEvents()
    assert w.committed == [s["en"]] and w.draft_lines() == [] and pipe.en == ""


def test_older_final_to_committed_and_newer_partial_stays_in_draft(qapp):
    w = window(qapp, "Zero is said.")
    pipe, _ = gcp_pipe(w, FakeHTTP())
    pipe.seq = 5
    pipe._put("mt", "새 조각", "Newer going", False, None, "새 조각", 5)
    pipe._mt_done({"seq": 3, "seg": "옛 문장.", "final": True, "talk": 2}, "Older done.")
    qapp.processEvents()
    assert w.en_lines() == ["Zero is said.", "Older done."] and w.draft_lines() == ["Newer going"]


def test_same_final_twice_goes_up_once(qapp):
    w = window(qapp, "First is said.")
    for en in ("Second is said.", "Second is said.", " Second is said. "):
        w.set_subtitles("둘", en, is_final=True)
    qapp.processEvents()
    assert w.committed == TWO and w.en_lines() == TWO


def test_draft_always_dim_and_committed_always_white(qapp):
    w = window(qapp, "First is said")
    w.set_subtitles("말", "Second is said.", is_final=False)
    qapp.processEvents()
    assert w.en_lines() == ["First is said"] and w.draft_lines() == ["Second is said."]
    color = lambda r: r.palette().color(r.foregroundRole()).getRgb()     # noqa: E731
    assert all(color(r) == (255, 255, 255, 255) for r in w.en_rows)
    assert all(color(r)[:3] == (255, 255, 255) and color(r)[3] < 128 for r in w.draft_rows)
    assert w._draft_h < w._en_h
