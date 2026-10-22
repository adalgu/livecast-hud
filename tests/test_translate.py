# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Glossary rules, spoken number and time readings, and script matching — on the example config."""
import collections
import re

import pytest
from fakes import config, script_by_id, translator


def vocab(talk=1):
    return translator(talk=talk).vocab_for(talk)


def match_twice(sentences, sid, text):
    """Ids matched with the cursor right before the sentence (near) and at the start (far)"""
    from livecast_hud.translate import ScriptMatcher
    v = vocab()
    idx = [s["id"] for s in sentences].index(sid)
    out = []
    for cursor in (idx - 1, -1):
        m = ScriptMatcher(sentences, v)
        m.cursor = cursor
        out.append((m.match(text, final=True) or {}).get("id"))
    return out


# ---------- glossary ----------
def test_glossary_normalizes_spoken_forms():
    v = vocab()
    terms = config().merged("glossary", 1)
    assert any(t.get("say") for t in terms)
    for t in terms:
        for s in t.get("say") or []:
            assert v.normalize(f"{s}는 중요합니다") == f"{t['ko']}는 중요합니다", s


def test_proper_nouns_enforced_before_mt_and_common_words_left_alone():
    from livecast_hud.translate import is_proper
    v = vocab()
    for t in config().merged("glossary", 1):
        if t.get("en") and t.get("status", "ok") == "ok" and re.search("[가-힣]", t["ko"]):
            assert (t["en"] in v.enforce_terms(f"이것은 {t['ko']}입니다")) is is_proper(t), t["ko"]
    assert v.enforce_terms("토종 씨앗") == "토종 씨앗"                                 # not approved yet: left to the translator


def test_enforce_respects_word_boundary():
    v = vocab()
    assert v.enforce_terms("한솔빛 공원") == "한솔빛 공원"                             # '솔빛' is part of another word
    assert v.enforce_terms("솔빛 공원") == "Solbit 공원"


FIX_CASES = [  # (Korean source, machine translation, expected fix)
    ("브린들우드 커먼즈", "at Brindle Wood Commons", "at Brindlewood Commons"),
    ("브린들우드 커먼즈", "the Brindlewood Common said", "the Brindlewood Commons said"),
    ("솔빛 마을", "in Solbeat village", "in Solbit village"),
    ("씨앗 도서관", "the seed bookstore opens", "the seed library opens"),
    ("씨앗 도서관", "the seed book store opens", "the seed library opens"),
    ("발아율", "the sprouting ratio rose", "the germination rate rose"),
    ("씨앗 교환", "a seed exchange in spring", "a seed swap in spring"),
]


@pytest.mark.parametrize("ko,mt,want", FIX_CASES)
def test_glossary_fixes_feared_mistranslations(ko, mt, want):
    assert vocab().fix_en(mt, ko) == want


def test_glossary_fix_is_conditioned_on_source():
    v = vocab()
    assert v.fix_en("a seed exchange", "씨앗을 바꿉니다") == "a seed exchange"
    assert v.fix_en("the sprouting ratio", "싹이 텄습니다") == "the sprouting ratio"
    assert v.fix_en("seed swap", "씨앗 교환") == "seed swap"


def test_glossary_covers_every_feared_mistranslation_with_a_fix_case():
    with_bad = {t["ko"] for t in config().merged("glossary", 1) if t.get("bad")}
    assert with_bad == {term for term in with_bad for ko, _, _ in FIX_CASES if term in ko}


def test_display_english_uses_glossary_casing_without_changing_words():
    from livecast_hud.translate import ScriptMatcher
    v = vocab()
    assert v.canonical_case("log it with a qr code at brindlewood commons") == "log it with a QR code at Brindlewood Commons"
    assert v.canonical_case("the seed library") == "the seed library"                # common words keep their case
    sentences = config().script(1)
    m = ScriptMatcher(sentences, v)
    for s in sentences:
        r = m.match(s["ko"], final=True)
        if s["en"]:
            assert r["en"].lower() == s["en"].lower() and "qr code" not in r["en"]


@pytest.mark.parametrize("en,proper,want", [("HoneyLoop", None, True), ("QR code", None, True), ("Bee Map", False, False),
                                            ("Solbit", True, True), ("Solbit", None, False), ("light pollution", None, False),
                                            ("Spatial maps", None, False)])
def test_proper_noun_rule_examples(en, proper, want):
    from livecast_hud.translate import is_proper
    term = {"en": en} if proper is None else {"en": en, "proper": proper}
    assert is_proper(term) is want


# ---------- misheard words (--mt gcp path) and English spelling fixes ----------
@pytest.mark.parametrize("fn,src,want", [
    ("fix_spoken", "블린들우드 커먼즈는 마을 단체", "브린들우드 커먼즈는 마을 단체"),
    ("fix_spoken", "솔비 마을에서", "솔빛 마을에서"),
    ("fix_spoken", "솔비 마을은 작습니다", "솔빛 마을은 작습니다"),
    ("fix_spoken", "두 번째 발판은", "두 번째 발표는"),                  # the fix drops the final consonant: particle follows
    ("fix_spoken", "첫 번째 발판을", "첫 번째 발표를"),
    ("fix_spoken", "세 번째 발판으로 넘어가", "세 번째 발표로 넘어가"),
    ("fix_spoken", "두 번째 발판과 세 번째", "두 번째 발표와 세 번째"),
    ("fix_spoken", "두 번째 발판", "두 번째 발표"),
    ("fix_spoken", "두 번째 발판에서는", "두 번째 발표에서는"),
    ("fix_spoken", "나무 발판은 튼튼합니다", "나무 발판은 튼튼합니다"),      # without the context words it stays
    ("fix_en_names", "Brindelwood opens.", "Brindlewood opens."),
    ("fix_en_names", "from Solbich, SOLBICH and solbich", "from Solbit, SOLBIT and solbit"),
    ("fix_en_names", "Brindlewood and Solbit stay.", "Brindlewood and Solbit stay."),
])
def test_spoken_and_english_name_fixes(fn, src, want):
    v = vocab()
    assert getattr(v, fn)(src) == want
    assert getattr(v, fn)(want) == want


def test_particle_follows_the_fixed_word():
    from livecast_hud.translate import Vocabulary
    v = Vocabulary(spoken_fixes=[{"from": "바다", "to": "바닥"}, {"from": "하늘", "to": "하니"}, {"from": "마루", "to": "마을"}])
    assert v.fix_spoken("바다는 넓다") == "바닥은 넓다"                    # vowel → consonant
    assert v.fix_spoken("하늘을 본다") == "하니를 본다"                      # consonant → vowel
    assert v.fix_spoken("마루로 간다") == "마을로 간다"                      # ㄹ takes 로 like a vowel
    assert v.fix_spoken("바다는것") == "바닥는것"                            # not a separate particle: kept


# ---------- spoken readings: numbers, times, aliases (matching keys only) ----------
def test_spoken_key_canonical_forms():
    v = vocab()
    times = ["오후 두 시부터 다섯 시까지", "오후 2시부터 5시까지", "14시부터 17시까지", "십사 시부터 십칠 시까지", "14~17시", "오후 2~5시"]
    assert {v.spoken_key(t) for t in times} == {"14시부터 17시까지"}
    assert v.spoken_key("씨앗 스와프") == v.spoken_key("SWAP") == v.spoken_key("씨앗스와프") == "씨앗 교환"
    assert v.spoken_key("SWAPS") == "SWAPS"                                   # inside a longer Latin word: no alias
    assert v.spoken_key("한 대당 약 사 점 팔이 초") == "1대당 약 4.82초"
    assert v.spoken_key("삼백칠십일 점 삼칠 초") == "371.37초"
    assert v.spoken_key("삼십삼 퍼센트") == v.spoken_key("33 프로") == "33%"
    assert v.spoken_key("오전 9시") == "9시" and v.spoken_key("오후 12시") == "12시"


TIME_FORMS = ["오후 두 시부터 다섯 시까지", "14시부터 17시까지", "십사 시부터 십칠 시까지", "오후 두 시부터 다섯 시", "14~17시"]


@pytest.mark.parametrize("form", TIME_FORMS)
def test_time_reading_variants_attach_to_same_sentence(form):
    sentences = config().script(1)
    ko = script_by_id()["s05"]["ko"]
    assert "오후 2시부터 5시까지" in ko
    assert match_twice(sentences, "s05", ko.replace("오후 2시부터 5시까지", form)) == ["s05", "s05"]


ALIAS_CASES = [("s07", "씨앗 교환", "씨앗 스와프"), ("s07", "씨앗 교환", "SWAP"), ("s07", "씨앗 교환", "씨앗스와프")]


@pytest.mark.parametrize("sid,said,variant", ALIAS_CASES)
def test_alias_variants_attach_to_same_sentence(sid, said, variant):
    ko = script_by_id()[sid]["ko"]
    assert said in ko
    assert match_twice(config().script(1), sid, ko.replace(said, variant)) == [sid, sid]


NUM_CASES = [  # (sentence, number in the script, spoken form when recognition writes numbers as words)
    ("s03", "340건", "삼백사십 건"), ("s04", "약 78%", "약 칠십팔 퍼센트"), ("s04", "약 78%", "약 칠십팔 프로"),
    ("s06", "4.5초", "사 점 오 초"), ("s09", "12곳", "십이 곳"),
]


@pytest.mark.parametrize("sid,said,variant", NUM_CASES)
def test_number_reading_variants_attach_to_same_sentence(sid, said, variant):
    ko = script_by_id()[sid]["ko"]
    assert said in ko
    assert match_twice(config().script(1), sid, ko.replace(said, variant)) == [sid, sid]


def test_spoken_normalization_adds_no_new_merges():
    """Sentences sharing a key after normalization = sentences sharing one before (no new collisions)"""
    v = vocab()

    def groups(f):
        g = collections.defaultdict(list)
        for s in config().script(1):
            g[f(s["ko"])].append(s["id"])
        return sorted(x for x in g.values() if len(x) > 1)

    assert groups(v.key) == groups(lambda t: re.sub(r"[\s.,?!·:;'\"`]", "", t).lower())


# ---------- script matching ----------
def test_matcher_follows_whole_script_in_order():
    from livecast_hud.translate import ScriptMatcher
    sentences = config().script(1)
    m = ScriptMatcher(sentences, vocab())
    assert [(m.match(s["ko"], final=True) or {}).get("id") for s in sentences] == [s["id"] for s in sentences]


def test_matcher_follows_script_in_order_with_variants_mixed():
    from livecast_hud.translate import ScriptMatcher
    swaps = [("오후 2시부터 5시까지", "오후 두 시부터 다섯 시까지"), ("씨앗 교환", "씨앗 스와프"), ("340건", "삼백사십 건"),
             ("QR 코드", "큐알 코드")]
    m = ScriptMatcher(config().script(1), vocab())
    for s in config().script(1):
        said = s["ko"]
        for a, b in swaps:
            said = said.replace(a, b)
        assert (m.match(said, final=True) or {}).get("id") == s["id"], s["id"]


@pytest.mark.parametrize("variant", [
    lambda s: s.replace(" ", ""),                          # no spaces
    lambda s: s.replace("QR 코드", "큐알 코드"),            # spoken form
    lambda s: s.rstrip("."),                               # no period
    lambda s: s[:-3] + s[-1],                              # a few last characters dropped
])
def test_matcher_tolerates_recognition_variants(variant):
    from livecast_hud.translate import ScriptMatcher
    by_id = script_by_id()
    m = ScriptMatcher(config().script(1), vocab())
    for i in ["s01", "s02", "s05", "s08", "s11"]:
        r = m.match(variant(by_id[i]["ko"]), final=True)
        assert r and r["id"] == i, (i, r)


def test_matcher_leaves_off_script_speech_blank():
    from livecast_hud.translate import ScriptMatcher
    m = ScriptMatcher(config().script(1), vocab())
    assert m.match("오늘 점심 메뉴는 비빔밥이었습니다.", final=True) is None
    assert m.match("질문이 있으시면 손을 들어 주세요.", final=True) is None


def test_matcher_partial_locks_on_next_sentence_early():
    from livecast_hud.translate import ScriptMatcher
    sentences = config().script(1)
    m = ScriptMatcher(sentences, vocab())
    m.match(sentences[3]["ko"], final=True)
    nxt = sentences[4]["ko"]
    r = m.match(nxt[: int(len(nxt) * 0.6)], final=False)
    assert r and r["id"] == sentences[4]["id"]


def test_talks_without_a_script_match_nothing():
    tr = translator(talk=2)
    assert not tr.script_first and tr.lookup(script_by_id()["s02"]["ko"], True) is None
    tr.set_talk(1)
    assert tr.script_first and tr.lookup(script_by_id()["s02"]["ko"], True)["id"] == "s02"


def test_speech_hints_cover_every_talk_within_v1_limits():
    from livecast_hud.config import Config
    from livecast_hud.translate import speech_hints
    c = config()
    h = speech_hints(c)
    assert all(t["ko"] in h for t in c.merged_all_talks("glossary"))
    assert all(x in h for x in c.merged_all_talks("hints")) and "브린들우드 커먼스" in h and "큐알 코드" in h
    assert len(h) == len(set(h)) and all(len(x) <= 100 for x in h)
    odd = Config({"hints": ["가", "x" * 101, " 가 "], "talks": {"2": {"hints": ["나"]}}})
    assert speech_hints(odd) == ["가", "나"] and speech_hints(None) == []
