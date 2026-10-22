# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Talk configuration: one JSON file with shared and per-talk vocabulary, plus one script file per talk.

Every list field (hints, glossary, spoken_fixes, english_fixes, match_aliases) may appear at the top level
(shared by all talks) or inside a talk (that talk only). A talk sees both, shared entries first.
See docs/configuration.md for the file format.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

LIST_FIELDS = ("hints", "glossary", "spoken_fixes", "english_fixes", "match_aliases")


class ConfigError(ValueError):
    """The config or a script file is missing or malformed."""


def load_script(path) -> List[Dict]:
    """Script sentences: {"sentences": [{"id", "ko", "en"}, ...]}. "en" may be null (no approved English)."""
    try:
        sentences = json.loads(Path(path).read_text(encoding="utf-8"))["sentences"]
        if not isinstance(sentences, list) or not all(isinstance(s, dict) and s.get("id") and s.get("ko") for s in sentences):
            raise ValueError("every sentence needs 'id' and 'ko'")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ConfigError(f"cannot read script {path}: {e}") from None
    return sentences


class Config:
    def __init__(self, data: Optional[Dict] = None, base_dir: "str | Path" = "."):
        self.data = data or {}
        self.base_dir = Path(base_dir)        # script paths are relative to the config file
        talks = self.data.get("talks")
        talks = {} if talks is None else talks
        if not isinstance(talks, dict):
            raise ConfigError("'talks' must be an object keyed by talk number")
        self.talks: Dict[int, Dict] = {}
        for key, talk in talks.items():
            if not str(key).isdigit() or not 1 <= int(key) <= 9:
                raise ConfigError(f"talk keys must be \"1\" to \"9\", got {key!r}")
            self.talks[int(key)] = talk or {}
        for field in LIST_FIELDS:
            for owner in [self.data, *self.talks.values()]:
                if not isinstance(owner.get(field) or [], list):
                    raise ConfigError(f"'{field}' must be a list")
        self._scripts: Dict[int, List[Dict]] = {}

    @classmethod
    def load(cls, path) -> "Config":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ConfigError(f"cannot read config {path}: {e}") from None
        if not isinstance(data, dict):
            raise ConfigError(f"config {path} must be a JSON object")
        return cls(data, path.parent)

    def talk_numbers(self) -> List[int]:
        return sorted(self.talks)

    def default_talk(self) -> int:
        return min(self.talks) if self.talks else 1

    def name(self, talk: int) -> str:
        return (self.talks.get(talk) or {}).get("name", "")

    def merged(self, field: str, talk: int) -> list:
        """Shared entries, then the talk's own entries"""
        return list(self.data.get(field) or []) + list((self.talks.get(talk) or {}).get(field) or [])

    def merged_all_talks(self, field: str) -> list:
        """Shared entries plus every talk's entries (speech hints cannot change while a stream is open)"""
        out = list(self.data.get(field) or [])
        for n in self.talk_numbers():
            out += list(self.talks[n].get(field) or [])
        return out

    def script(self, talk: int) -> List[Dict]:
        """The talk's script sentences ([] if it has no script)"""
        if talk not in self._scripts:
            rel = (self.talks.get(talk) or {}).get("script")
            self._scripts[talk] = load_script(self.base_dir / rel) if rel else []
        return self._scripts[talk]

    def demo(self, talk: int) -> List[Dict]:
        """Sentences the demo mode plays: the talk's "demo" ids, or every script sentence that has English"""
        sentences = self.script(talk)
        ids = (self.talks.get(talk) or {}).get("demo")
        if ids is None:
            return [s for s in sentences if s.get("en")]
        by_id = {s["id"]: s for s in sentences}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise ConfigError(f"talk {talk} demo ids not in its script: {', '.join(missing)}")
        return [by_id[i] for i in ids]
