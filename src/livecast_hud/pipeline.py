# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Recognition results → translation (script match or Cloud Translation) → HUD."""

import collections
import json
import re
import threading
import time

from .translate import LivecastTranslator, mt_segment, split_closed, too_short


class EventLog:
    """JSONL log of display events (--log, for latency checks and review). Does nothing without a path."""

    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8") if path else None
        self.lock = threading.Lock()

    def write(self, ev, **kw):
        if self.f:
            with self.lock:
                # m = monotonic clock (latency measurement unaffected by sleep or clock adjustments)
                self.f.write(json.dumps({"t": round(time.time(), 4), "m": round(time.monotonic(), 4), "ev": ev, **kw},
                                        ensure_ascii=False) + "\n")
                self.f.flush()


class MTWorker:
    """--mt gcp translation thread. Keeps only the latest partial request (older partials waiting are dropped) and
    handles every final in arrival order first (no finished sentence loses its English)."""

    def __init__(self, run, done):
        self.run, self.done = run, done
        self.cv = threading.Condition()
        self.finals, self.partial = collections.deque(), None
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, req):
        with self.cv:
            if req["final"]:
                self.finals.append(req)
            else:
                self.partial = req
            self.cv.notify()

    def _loop(self):
        while True:
            with self.cv:
                while not self.finals and self.partial is None:
                    self.cv.wait()
                if self.finals:
                    req = self.finals.popleft()
                else:
                    req, self.partial = self.partial, None
            try:
                en = self.run(req)
            except Exception:
                en = None
            self.done(req, en)


def _key(sent: str) -> str:
    """Closed-sentence key: spacing and punctuation differences count as the same sentence"""
    return re.sub(r"[\s.,?!]", "", sent)


class Pipeline:
    """Recognition results → translation → HUD. Partial results update the screen at most every 0.35 s (debounce)."""

    DEBOUNCE = 0.35
    MT_EVERY = 0.8   # --mt gcp: translate partials at most every 0.8 s (finals right away)

    def __init__(self, window, translator: LivecastTranslator, log: EventLog, clock=time.monotonic, mt_inline: bool = False):
        self.window, self.translator, self.log = window, translator, log
        self.en = ""                           # English line on screen (basis for drops and off-script speech)
        self.last_partial = 0.0
        self.pending = collections.deque()     # waiting to be drawn (emit order = draw order)
        self.stats = collections.Counter()
        window.on_shown = self._shown
        # --mt gcp state: seq = order sent to the screen, en_seq = order that produced the current English
        # (late translations are dropped); clock is faked in tests
        self.lock = threading.RLock()
        self.clock, self.seq, self.en_seq, self.ko, self.last_mt, self.link = clock, 0, 0, "", None, None
        # committing closed sentences: closed = keys already committed (never twice), open_* = the dimmed draft's English and source
        self.closed, self.open_en, self.open_src, self.open_seq = collections.deque(maxlen=64), "", "", 0
        self.mt = (MTWorker(lambda r: self.translator.translate_gcp(r["seg"]), self._mt_done)
                   if translator.mode == "gcp" and not mt_inline else None)

    def _translate(self, ko: str, final: bool):
        m = self.translator.lookup(ko, final)
        if m:
            return m, m["en"] or ""                       # script sentence without English: empty English line
        return None, ("" if final else self.en)           # off-script: empty on final, keep the previous English on partial

    def _emit(self, kind: str, ko: str, final: bool):
        ko = self.translator.normalize(ko)
        if self.translator.mode == "gcp":
            return self._emit_gcp(kind, self.translator.fix_spoken(ko), final)   # the Korean line shows the fixed words too
        m, en = self._translate(ko, final)
        self.en = en
        if final and m:
            self.stats["matched"] += 1
        self.pending.append({"kind": kind, "id": m["id"] if m else None, "score": m["score"] if m else None,
                             "t_emit": round(time.time(), 4)})
        self.window.set_subtitles(ko, en, is_final=final)

    # ---------- --mt gcp ----------
    def _show(self, kind: str, ko: str, en: str, final: bool, m):
        self.pending.append({"kind": kind, "id": m["id"] if m else None, "score": m["score"] if m else None,
                             "t_emit": round(time.time(), 4)})
        self.window.set_subtitles(ko, en, is_final=final)

    def _put(self, kind: str, ko: str, en: str, final: bool, m, src: str, seq=None):
        """Set the English line and remember the dimmed draft (used when committing closed sentences)"""
        seq = self.seq if seq is None else seq
        self.en, self.en_seq = en, max(self.en_seq, seq)
        self.open_en, self.open_src, self.open_seq = ("", "", 0) if final else (en, src, seq)
        self._show(kind, ko, en, final, m)

    def _emit_gcp(self, kind: str, ko: str, final: bool):
        """The Korean line is shown right away. A sentence newly closed by punctuation inside a partial is committed
        (_close). The open fragment (on final: sentences not yet committed) is matched against the script first when
        the talk has one, and the translator is not called on a match. Otherwise the worker translates it while the
        previous English stays."""
        with self.lock:
            self.seq += 1
            self.ko = ko
            closed, tail = split_closed(ko)
            reqs = []
            if final:
                rest = [x for x in closed + ([tail] if tail else []) if _key(x) not in self.closed]
                self.closed.extend(_key(x) for x in rest)
                seg = mt_segment(" ".join(rest), True)              # a final is always translated from the final text
            else:
                # the last closed sentence (nothing after it) is not committed: streaming partials add and remove a
                # temporary period at the end while the speaker is still talking
                for x in (closed if tail else closed[:-1]):
                    if _key(x) not in self.closed and not too_short(x):
                        self.closed.append(_key(x))
                        reqs += self._close(kind, ko, x)
                seg = mt_segment(ko, False)
            if seg and too_short(seg):
                seg = ""                                            # short fragments are not translated: the English line stays
            m = None
            if self.translator.script_first and seg:
                m = self.translator.lookup(seg, final) or (self.translator.lookup(ko, final) if final and ko != seg else None)
                if m and not m["en"]:
                    m = None                                        # script sentence without English: machine translation on gcp
            if m:
                if final:
                    self.stats["matched"] += 1
                self._put(kind, ko, m["en"], final, m, seg)
            else:
                self._show(kind, ko, self.en, False, None)          # the English line changes when the translation arrives
                now = self.clock()
                if seg and (final or self.last_mt is None or now - self.last_mt >= self.MT_EVERY):
                    if not final:
                        self.last_mt = now
                    self.stats["mt_sent"] += 1
                    reqs.append({"seq": self.seq, "seg": seg, "final": final, "talk": self.translator.talk})
        for req in reqs:
            if self.mt:
                self.mt.submit(req)
            else:                                                   # tests: translate on the same thread
                self._mt_done(req, self.translator.translate_gcp(req["seg"]))

    def _close(self, kind: str, ko: str, sent: str) -> list:
        """Commit one sentence closed inside a partial. A script sentence uses its script English; if the dimmed draft
        is the translation of this whole sentence it is committed as is. If the draft only covers the start (Korean puts
        the verb and negation last), the sentence is translated once and committed when that arrives; if that
        translation fails, the dimmed draft (the latest English while it was open) is committed."""
        self.en_seq = self.seq                                     # translations requested while this sentence was open are dropped
        self.stats["closed"] += 1
        m = self.translator.lookup(sent, True) if self.translator.script_first else None
        if m and m["en"]:
            self._put(kind, ko, m["en"], True, m, sent)
            return []
        if self.open_en and _key(self.open_src) == _key(sent):
            self._put(kind, ko, self.open_en, True, None, sent)
            return []
        self.stats["mt_sent"] += 1
        return [{"seq": self.seq, "seg": sent, "final": True, "talk": self.translator.talk}]

    def _set_link(self, state: str):
        if state != self.link:
            self.link = state
            self.window.set_link_status(state)
            self.log.write("mt_status", state=state)

    def _mt_done(self, req: dict, en):
        """Translation result: on failure keep the previous English and show 'disconnected'. Dropped if the talk changed,
        or if it is a partial and newer English is already shown"""
        with self.lock:
            if en is None:
                if self.link != "disconnected":
                    print(f"[MT gcp] translation failed ({self.translator.gcp.last_error}) — keeping the previous English")
                self._set_link("disconnected")
                if req["final"] and self.open_en and self.open_seq <= req["seq"]:
                    self._put("mt", self.ko, self.open_en, True, None, self.open_src, req["seq"])   # commit the draft as is
                return
            self._set_link("connected")
            if req["talk"] != self.translator.talk or (not req["final"] and req["seq"] < self.en_seq):
                self.stats["mt_dropped"] += 1                       # finished sentences (finals, closing translations) are never dropped for lateness
                return
            newer = req["final"] and self.open_en and self.open_seq > req["seq"]
            keep = (self.open_en, self.open_src, self.open_seq)
            self._put("mt", self.ko, en, req["final"], None, req["seg"], req["seq"])
            if newer:                                               # a newer draft that appeared meanwhile goes back below
                self._put("mt", self.ko, keep[0], False, None, keep[1], keep[2])

    def set_talk(self, talk: int):
        """Switch talk (terminal 1–9⏎, F1–F9): glossary and script matching change at once (caches are per talk).
        Speech hints already cover every talk."""
        if not self.translator.knows_talk(talk):
            print(f"Talk {talk} is not in the config — ignored")
            return
        with self.lock:
            self.translator.set_talk(talk)
        self.window.set_talk(talk)
        self.log.write("talk", talk=talk)
        name = self.translator.config.name(talk)
        print(f"Talk {talk}" + (f": {name}" if name else "") + (" (script first)" if self.translator.script_first else ""))

    def on_partial(self, ko: str):
        self.stats["partial_in"] += 1
        now = time.time()
        if now - self.last_partial < self.DEBOUNCE:
            return
        self.last_partial = now
        self.stats["partial_shown"] += 1
        self._emit("partial", ko, final=False)

    def on_final(self, ko: str):
        self.stats["final"] += 1
        self._emit("final", ko, final=True)

    def on_status(self, state: str):
        self.window.set_link_status(state)
        self.log.write("status", state=state)

    def _shown(self, kind: str, ko: str, en: str):
        meta = self.pending.popleft() if self.pending else {}
        self.log.write("shown", ko=ko, en=en, **meta)
