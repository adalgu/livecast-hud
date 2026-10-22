# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Korean → English subtitle text.

- `--mt script` (default, offline): match the recognized Korean against the talk script and show the
  prepared English of the matched sentence. Off-script speech leaves the English line empty.
- `--mt gcp`: Cloud Translation v3 (`GcpTranslator`). HTTP posting and the token provider are injectable
  (tests use fakes). For talks with a script, script matching runs first. Tokens, accounts and project
  ids are never printed.
- Glossary data (spoken forms, feared mistranslations, proper nouns, misheard-word fixes, match aliases)
  comes from the config file; only general Korean rules (number and time readings, particles) live here.
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Tuple

from .config import Config


def _alternation(keys) -> re.Pattern:
    """One regex over all keys, longest first (so a longer term wins over a term it contains)"""
    return re.compile("|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True)), re.IGNORECASE)


def is_proper(term: Dict) -> bool:
    """A glossary English term is a proper noun when it has a capital letter after its first character
    (e.g. "QR code", "HiveNet"). Set "proper" in the entry to override (single capitalized names, or
    title-case common terms)."""
    if "proper" in term:
        return bool(term["proper"])
    return any(c.isupper() for c in term["en"][1:])


# ---------- general Korean rules: reading numbers and times (used only for script-matching keys) ----------
_NATIVE = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12}
_NATIVE_RE = re.compile(r"(?<![가-힣])(열한|열두|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)\s*(?=시|대)")
_SINO_DIGIT = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_SINO_RE = re.compile(r"(?<![가-힣])([일이삼사오육칠팔구십백천]+)(?:\s*점\s*((?:[영공일이삼사오육칠팔구]\s*)+?))?\s*(?=시|초|분|건|곳|대|퍼센트|프로)")
_TILDE_RE = re.compile(r"(\d{1,2})\s*시?\s*[~∼〜\-–—]\s*(\d{1,2})\s*시(?:\s*까지)?")
_PM_RANGE_RE = re.compile(r"오후\s*(\d{1,2})\s*시(\s*부터\s*)(?:오후\s*)?(\d{1,2})\s*시")
_AMPM_RE = re.compile(r"(오전|오후)\s*(\d{1,2})\s*시")


def _sino(s: str) -> int:
    """십칠 → 17, 이십 → 20, 삼백칠십일 → 371"""
    total = num = 0
    for ch in s:
        if ch in "십백천":
            total += (num or 1) * {"십": 10, "백": 100, "천": 1000}[ch]
            num = 0
        else:
            num = _SINO_DIGIT[ch]
    return total + num


def _pm(h: str) -> int:
    return int(h) + 12 if 1 <= int(h) <= 11 else int(h)


def read_numbers(text: str) -> str:
    """Spoken numbers and times to one written form. e.g. '오후 다섯 시부터 여덟 시까지' → '17시부터 20시까지'"""
    text = _NATIVE_RE.sub(lambda m: str(_NATIVE[m.group(1)]), text)
    text = _SINO_RE.sub(lambda m: str(_sino(m.group(1)))
                        + ("." + "".join(str(_SINO_DIGIT[c]) for c in m.group(2) if not c.isspace()) if m.group(2) else ""), text)
    text = _TILDE_RE.sub(lambda m: f"{m.group(1)}시부터 {m.group(2)}시까지", text)
    text = _PM_RANGE_RE.sub(lambda m: f"{_pm(m.group(1))}시{m.group(2)}{_pm(m.group(3))}시", text)
    text = _AMPM_RE.sub(lambda m: f"{_pm(m.group(2)) if m.group(1) == '오후' else int(m.group(2))}시", text)
    return re.sub(r"(\d)\s*(?:퍼센트|프로)", r"\1%", text)


# ---------- general Korean rule: particles after a fixed word ----------
_PARTICLES = (("으로", "로"), ("은", "는"), ("이", "가"), ("을", "를"), ("과", "와"))   # (after a final consonant, after a vowel)


def _final_consonant(ch: str) -> int:
    """Final consonant index of a Hangul syllable (0 = none, 8 = ㄹ); 0 for anything else"""
    return (ord(ch) - 0xAC00) % 28 if "가" <= ch <= "힣" else 0


def _particle_after(word: str, particle: str) -> str:
    """Pick the particle form that fits the last syllable of word (ㄹ takes 로, like a vowel)"""
    for after_consonant, after_vowel in _PARTICLES:
        if particle in (after_consonant, after_vowel):
            fc = _final_consonant(word[-1]) if word else 0
            vowel_like = fc == 0 or (fc == 8 and after_vowel == "로")
            return after_vowel if vowel_like else after_consonant
    return particle


def _words_re(text: str) -> str:
    """Words of text with any spacing between them"""
    return r"\s*".join(map(re.escape, text.split()))


_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+")     # split only after an end mark followed by a space (the dot in 4.82 is not an end)
_ENDS = re.compile(r"[.?!]$")
MT_MIN_CHARS, MT_MAX_CHARS = 8, 80
SHORT_MAX = 3


def split_closed(text: str) -> Tuple[List[str], str]:
    """(sentences closed by an end mark, the open tail) — used to commit closed sentences inside a partial"""
    parts = [x.strip() for x in _SENT_SPLIT.split(text.strip()) if x.strip()]
    if parts and not _ENDS.search(parts[-1]):
        return parts[:-1], parts[-1]
    return parts, ""


def too_short(ko_text: str) -> bool:
    """Fragments of 3 characters or fewer without punctuation and spaces (a single syllable plus an automatic
    period) are not translated"""
    return len(re.sub(r"[\W_]", "", ko_text)) <= SHORT_MAX


def mt_segment(text: str, final: bool) -> str:
    """Text to send to --mt gcp (pure function). partial: the open fragment after the last end mark — not sent if
    it is shorter than 8 characters after a closed sentence; over 80 characters keeps the last 80 cut at a word
    boundary. final: the last sentence (or the last two when together they fit in 80 characters)."""
    parts = [x.strip() for x in _SENT_SPLIT.split(text.strip()) if x.strip()]
    if not parts:
        return ""
    if final:
        if len(parts) > 1 and len(parts[-2]) + 1 + len(parts[-1]) <= MT_MAX_CHARS:
            return f"{parts[-2]} {parts[-1]}"
        return parts[-1]
    done = [x for x in parts if _ENDS.search(x)]
    tail = "" if _ENDS.search(parts[-1]) else parts[-1]
    if len(tail) < MT_MIN_CHARS and done:
        return ""                                          # closed sentences are committed by Pipeline._close
    if len(tail) > MT_MAX_CHARS:
        cut = tail[-MT_MAX_CHARS:]
        if not tail[-MT_MAX_CHARS - 1].isspace() and " " in cut:
            cut = cut.split(" ", 1)[1]                     # drop a word that was cut in the middle
        return cut
    return tail


class Vocabulary:
    """Glossary-driven text rules for one talk (shared entries plus that talk's entries)."""

    def __init__(self, glossary=(), spoken_fixes=(), english_fixes=(), match_aliases=()):
        terms = [t for t in glossary if t.get("ko")]
        self._say_to_ko = {s.lower(): t["ko"] for t in terms for s in t.get("say") or []}
        self._say_re = _alternation(self._say_to_ko) if self._say_to_ko else None
        usable = [t for t in terms if t.get("en") and t.get("status", "ok") == "ok"]
        # proper nouns are inserted before machine translation (common words are left to the translator)
        self.proper_ko_to_en = {t["ko"]: t["en"] for t in usable if is_proper(t) and re.search("[가-힣]", t["ko"])}
        # a Hangul letter right before means the match is part of another word, so it is not replaced
        self._proper_ko_re = (re.compile(r"(?<![가-힣])(?:" + _alternation(self.proper_ko_to_en).pattern + ")", re.IGNORECASE)
                              if self.proper_ko_to_en else None)
        self._bad = [(t["ko"], re.compile(rf"\b(?:{p})\b", re.IGNORECASE), t["en"]) for t in usable for p in t.get("bad") or []]
        self._case = {t["en"].lower(): t["en"] for t in usable if is_proper(t)}
        self._case_re = (re.compile(r"\b(?:" + "|".join(re.escape(e) for e in sorted(self._case, key=len, reverse=True)) + r")\b",
                                    re.IGNORECASE) if self._case else None)
        # misheard words: fixed at a word start (the particle after it is kept, or adjusted when the fix changes
        # whether the word ends in a consonant)
        self._spoken_fixes = [(re.compile(r"(?<!\S)" + _words_re(f["from"]) + r"(?:(으로|로|은|는|이|가|을|를|과|와)(?!\w))?"),
                               f["from"], f["to"]) for f in spoken_fixes]
        self._en_fixes = [(re.compile(rf"\b{re.escape(f['from'])}\b", re.IGNORECASE), f["to"]) for f in english_fixes]
        self._aliases = [(re.compile(r"(?<![A-Za-z])" + _words_re(a["from"]) + r"(?![A-Za-z])"), a["to"]) for a in match_aliases]

    def normalize(self, text: str) -> str:
        """Spoken forms to the written glossary form (e.g. 큐알 코드 → QR 코드)"""
        return self._say_re.sub(lambda m: self._say_to_ko[m.group(0).lower()], text) if self._say_re else text

    def canonical_case(self, en_text: str) -> str:
        """Fix the casing of glossary proper nouns without changing words (e.g. 'qr code' → 'QR code')"""
        return self._case_re.sub(lambda m: self._case[m.group(0).lower()], en_text) if self._case_re else en_text

    def enforce_terms(self, ko_text: str) -> str:
        """Before machine translation: replace Korean proper nouns with their fixed English"""
        text = self.normalize(ko_text)
        if not self._proper_ko_re:
            return text
        lower = {k.lower(): v for k, v in self.proper_ko_to_en.items()}
        return self._proper_ko_re.sub(lambda m: lower[m.group(0).lower()], text)

    def fix_en(self, en_text: str, ko_src: str) -> str:
        """After machine translation: replace feared mistranslations only when the source has that term"""
        ko = self.normalize(ko_src)
        for term, pat, en in self._bad:
            if term in ko:
                en_text = pat.sub(en, en_text)
        return self.canonical_case(en_text)

    def fix_spoken(self, ko_text: str) -> str:
        """Before translation and display (--mt gcp): fix misheard words, e.g. '그린 일 커먼즈는' → '그린힐 커먼즈는'"""
        for pat, src, dst in self._spoken_fixes:
            def rep(m, src=src, dst=dst):
                particle = m.group(1) or ""
                if particle and _final_consonant(src[-1]) != _final_consonant(dst[-1]):
                    particle = _particle_after(dst, particle)
                return dst + particle
            ko_text = pat.sub(rep, ko_text)
        return ko_text

    def fix_en_names(self, en_text: str) -> str:
        """After translation: English spelling fixes at word boundaries, keeping all-upper or all-lower case"""
        for pat, rep in self._en_fixes:
            en_text = pat.sub(lambda m: rep.upper() if m.group(0).isupper() else rep.lower() if m.group(0).islower() else rep, en_text)
        return en_text

    def spoken_key(self, text: str) -> str:
        """One form for reading variants (match aliases, numbers, times) — script-matching keys only"""
        for pat, rep in self._aliases:
            text = pat.sub(rep, text)
        return read_numbers(text)

    def key(self, text: str) -> str:
        return re.sub(r"[\s.,?!·:;'\"`()]", "", self.spoken_key(self.normalize(text))).lower()


class ScriptMatcher:
    """Script sentence matching (offline translation). Follows the talk in order (cursor) and prefers the
    next few sentences."""

    NEAR = 3                 # sentences after the cursor treated as "near"
    FINAL_NEAR, FINAL_FAR = 0.60, 0.72
    PARTIAL_NEAR, PARTIAL_FAR = 0.80, 0.92
    PARTIAL_MIN_CHARS, PARTIAL_MIN_SHARE = 6, 0.30

    def __init__(self, sentences: List[Dict], vocab: Optional[Vocabulary] = None):
        self.items = sentences
        self.vocab = vocab or Vocabulary()
        self.keys = [self.vocab.key(s["ko"]) for s in sentences]
        self.cursor = -1

    def match(self, ko_text: str, final: bool) -> Optional[Dict]:
        q = self.vocab.key(ko_text)
        if not q or (not final and len(q) < self.PARTIAL_MIN_CHARS):
            return None
        scored = []
        for i, key in enumerate(self.keys):
            if not final and len(q) < max(self.PARTIAL_MIN_CHARS, int(len(key) * self.PARTIAL_MIN_SHARE)):
                continue
            ratio = SequenceMatcher(None, q, key if final else key[: len(q)], autojunk=False).ratio()
            near = self.cursor < i <= self.cursor + self.NEAR
            need = (self.FINAL_NEAR if near else self.FINAL_FAR) if final else (self.PARTIAL_NEAR if near else self.PARTIAL_FAR)
            if ratio >= need:
                scored.append((ratio + (0.05 if near else 0.0), i))
        if not scored:
            return None
        scored.sort(reverse=True)
        score, i = scored[0]
        # two far sentences scoring alike (same opening words): pick neither
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.02 and not (self.cursor < i <= self.cursor + self.NEAR):
            return None
        if final:
            self.cursor = i
        item = self.items[i]
        en = item.get("en")
        return {"id": item["id"], "en": self.vocab.canonical_case(en) if en else None, "score": round(score, 3)}


# ---------- --mt gcp: Cloud Translation v3 ----------
GCP_TRANSLATE_URL = "https://translation.googleapis.com/v3/projects/{project}/locations/global:translateText"
CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def urllib_post(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> Tuple[int, bytes]:
    """Default HTTP POST (injectable — tests pass a fake). Returns (status, body)"""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def gcloud_token(account: str) -> Optional[str]:
    """Token from `gcloud auth print-access-token --account=…`. None on failure (nothing is printed)"""
    try:
        out = subprocess.run(["gcloud", "auth", "print-access-token", f"--account={account}"],
                             capture_output=True, timeout=10, check=True)
        return out.stdout.decode().strip() or None
    except Exception:
        return None


def adc_token() -> Optional[str]:
    """Token from Application Default Credentials (`gcloud auth application-default login`). None on failure"""
    try:
        import google.auth
        from google.auth.transport.requests import Request
        creds, _ = google.auth.default(scopes=[CLOUD_SCOPE])
        creds.refresh(Request())
        return creds.token
    except Exception:
        return None


def default_token_provider() -> Callable[[], Optional[str]]:
    """LIVECAST_GCP_ACCOUNT set: that gcloud account's token. Otherwise Application Default Credentials"""
    account = os.environ.get("LIVECAST_GCP_ACCOUNT")
    return (lambda: gcloud_token(account)) if account else adc_token


class GcpTranslator:
    """Cloud Translation v3 (--mt gcp). post(url, headers, body, timeout) and token_provider() are injectable.
    The token is fetched again after 45 minutes or on 401/403, with one retry only. Failures return None and
    leave only the kind of error in last_error (no values)."""

    TIMEOUT = 1.5
    TOKEN_TTL = 45 * 60

    def __init__(self, project: str, post: Optional[Callable] = None,
                 token_provider: Optional[Callable[[], Optional[str]]] = None, clock: Callable[[], float] = time.monotonic):
        if not project:
            raise ValueError("a Google Cloud project id is required")
        self.project, self.post, self.clock = project, post or urllib_post, clock
        self.token_provider = token_provider or default_token_provider()
        self.token, self.token_t = None, 0.0
        self.last_error = ""

    def _token(self, force: bool) -> Optional[str]:
        if force or not self.token or self.clock() - self.token_t >= self.TOKEN_TTL:
            self.token, self.token_t = self.token_provider(), self.clock()
        return self.token

    def translate(self, text: str) -> Optional[str]:
        body = json.dumps({"contents": [text], "sourceLanguageCode": "ko", "targetLanguageCode": "en",
                           "mimeType": "text/plain"}).encode("utf-8")
        for attempt in (0, 1):
            token = self._token(force=attempt == 1)
            if not token:
                self.last_error = "no token"
                return None
            headers = {"Authorization": f"Bearer {token}", "x-goog-user-project": self.project,
                       "Content-Type": "application/json"}
            try:
                status, data = self.post(GCP_TRANSLATE_URL.format(project=self.project), headers, body, self.TIMEOUT)
            except Exception as e:                       # timeout or connection error — the type name only
                self.last_error = type(e).__name__
                return None
            if status in (401, 403) and attempt == 0:
                continue                                 # fetch a new token and retry once
            if status != 200:
                self.last_error = f"HTTP {status}"
                return None
            try:
                return json.loads(data)["translations"][0]["translatedText"]
            except Exception as e:
                self.last_error = f"bad response {type(e).__name__}"
                return None
        return None


class LivecastTranslator:
    """Per-talk translation state: vocabulary, script matcher (keeps its cursor per talk) and --mt gcp cache."""

    def __init__(self, config: Optional[Config] = None, mode: str = "script", talk: Optional[int] = None,
                 gcp: Optional[GcpTranslator] = None):
        if mode == "gcp" and gcp is None:
            raise ValueError("--mt gcp needs a GcpTranslator")
        self.config = config or Config()
        self.mode, self.gcp = mode, gcp
        self.talk = talk if talk is not None else self.config.default_talk()
        self._vocabs: Dict[int, Vocabulary] = {}
        self._matchers: Dict[int, ScriptMatcher] = {}
        self.gcp_caches: Dict[int, Dict[str, str]] = {}

    def vocab_for(self, talk: int) -> Vocabulary:
        if talk not in self._vocabs:
            self._vocabs[talk] = Vocabulary(**{f: self.config.merged(f, talk)
                                               for f in ("glossary", "spoken_fixes", "english_fixes", "match_aliases")})
        return self._vocabs[talk]

    def matcher_for(self, talk: int) -> ScriptMatcher:
        if talk not in self._matchers:
            self._matchers[talk] = ScriptMatcher(self.config.script(talk), self.vocab_for(talk))
        return self._matchers[talk]

    @property
    def vocab(self) -> Vocabulary:
        return self.vocab_for(self.talk)

    @property
    def script_first(self) -> bool:
        """--mt gcp tries the script first only for talks that have one"""
        return bool(self.config.script(self.talk))

    def knows_talk(self, talk: int) -> bool:
        return talk in self.config.talks if self.config.talks else 1 <= talk <= 9

    def set_talk(self, talk: int):
        self.talk = talk

    def normalize(self, ko_text: str) -> str:
        return self.vocab.normalize(ko_text)

    def fix_spoken(self, ko_text: str) -> str:
        return self.vocab.fix_spoken(ko_text)

    def lookup(self, ko_text: str, final: bool) -> Optional[Dict]:
        """Script match (id, en, score) in the current talk, None if off-script"""
        return self.matcher_for(self.talk).match(ko_text, final)

    def translate_gcp(self, ko_text: str) -> Optional[str]:
        """--mt gcp: cache (per talk) → insert proper nouns → Cloud Translation → fix_en → fix_en_names. None on failure"""
        talk, vocab = self.talk, self.vocab
        cache = self.gcp_caches.setdefault(talk, {})
        if ko_text in cache:
            return cache[ko_text]
        en = self.gcp.translate(vocab.enforce_terms(ko_text))
        if en is None:
            return None
        cache[ko_text] = en = vocab.fix_en_names(vocab.fix_en(en, ko_text))
        return en


def speech_hints(config: Optional[Config]) -> List[str]:
    """Speech-recognition hints: glossary written forms, spoken forms that are not all lower case, and "hints" —
    shared plus every talk (talk switches never reopen the stream). Phrases over the v1 limit (100 chars) are
    dropped, duplicates removed."""
    config = config or Config()
    glossary = config.merged_all_talks("glossary")
    phrases = ([t["ko"] for t in glossary if t.get("ko")]
               + [s for t in glossary for s in t.get("say") or [] if not s.islower()]
               + config.merged_all_talks("hints"))
    return list(dict.fromkeys(h.strip() for h in phrases if isinstance(h, str) and h.strip() and len(h.strip()) <= 100))
