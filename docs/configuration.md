# Configuration

Everything specific to your talks lives in one JSON config file plus one script file per talk. The code holds no
names, terms or sentences. Pass the file with `--config PATH` or set `LIVECAST_CONFIG`.
A complete example is in [`examples/talks.json`](../examples/talks.json) (fictional talks).

## Shared and per-talk fields

The list fields `hints`, `glossary`, `spoken_fixes`, `english_fixes` and `match_aliases` can appear at the top
level (shared by every talk) and inside a talk (that talk only). A talk uses both, shared entries first.

```json
{
  "hints": ["브린들우드 커먼즈"],
  "glossary": [{"ko": "QR 코드", "say": ["큐알 코드"], "en": "QR code"}],
  "spoken_fixes": [{"from": "솔비 마을", "to": "솔빛 마을"}],
  "english_fixes": [{"from": "Solbich", "to": "Solbit"}],
  "talks": {
    "1": {"name": "Saving Seeds Together", "speaker": "Speaker A", "script": "scripts/talk1.json",
          "demo": ["s01", "s02"], "hints": ["텃밭"], "glossary": [], "match_aliases": []},
    "2": {"name": "Bees on the Roof", "hints": ["옥상 양봉"]}
  }
}
```

## Top level

| Field | Meaning |
| --- | --- |
| `talks` | Object keyed `"1"` to `"9"`. Switch talks while running with `1`–`9` Enter in the terminal or `F1`–`F9`. `--talk` defaults to the lowest key |
| `hints` | Speech-recognition hints (words and phrases up to 100 characters). Every talk's hints are sent when the stream opens, so switching talks never reopens it |
| `glossary` | Terms, see [Glossary entries](#glossary-entries) |
| `spoken_fixes` | Misheard Korean fixed before translation and display (`--mt gcp` only), see [Fixes and aliases](#fixes-and-aliases) |
| `english_fixes` | English spelling fixed after machine translation (`--mt gcp` only) |
| `match_aliases` | Other ways of saying a phrase, used only to match the script |

## Talk

| Field | Meaning |
| --- | --- |
| `name` | Talk title, printed at start and on a talk switch |
| `speaker` | Free text for your own reference (not shown) |
| `script` | Path to the talk's script file, relative to the config file. Talks without a script show no English with `--mt script`; with `--mt gcp` everything goes to the translator |
| `demo` | Script sentence ids that `--mode demo` plays, in order. Default: every sentence that has English |
| `hints`, `glossary`, `spoken_fixes`, `english_fixes`, `match_aliases` | Same as the top level, for this talk only |

## Glossary entries

```json
{"ko": "브린들우드 커먼즈", "say": ["브린들 우드 커먼즈"], "en": "Brindlewood Commons", "bad": ["Brindle Wood Commons"]}
```

| Field | Meaning |
| --- | --- |
| `ko` | Written Korean form (required). Also sent as a speech hint |
| `say` | Spoken or misspaced forms that are rewritten to `ko` everywhere (display, matching, translation). Forms that are not all lower case are also sent as hints |
| `en` | Fixed English. `null` means not decided yet |
| `bad` | Regular expressions for feared mistranslations. After machine translation they are replaced with `en`, only when the Korean contains `ko` |
| `status` | `"ok"` (default) or anything else to keep the entry out of translation until it is approved |
| `proper` | Whether `en` is a proper noun. Proper nouns are inserted before machine translation and their casing is fixed in the English line. Default: true when a capital letter follows the first character (`QR code`, `HoneyLoop`). Set `true` for one-word names (`Solbit`) and `false` for title-case common terms (`Bee Map`) |

## Fixes and aliases

Each entry is `{"from": "...", "to": "..."}`. Spaces in `from` match any spacing.

| Field | Meaning |
| --- | --- |
| `spoken_fixes` | `from` must start a word; text after it is kept. If the fix changes whether the word ends in a consonant, a following particle (은/는, 이/가, 을/를, 과/와, 으로/로) is adjusted: `{"from": "번째 발판", "to": "번째 발표"}` turns `두 번째 발판은` into `두 번째 발표는` |
| `english_fixes` | Whole-word, case-insensitive; an all-caps or all-lower match keeps that case |
| `match_aliases` | Rewritten only in the key used to match the script, never on screen. Latin `from` text must stand alone (`SWAP` does not match inside `SWAPS`) |

Spoken numbers and times are handled without configuration when matching: `오후 두 시부터 다섯 시까지`, `14~17시`
and `십사 시부터 십칠 시까지` all match `오후 2시부터 5시까지`; `삼백사십 건` matches `340건`; `칠십팔 퍼센트` matches `78%`.

## Script file

```json
{
  "sentences": [
    {"id": "s01", "ko": "안녕하십니까, 브린들우드 커먼즈에서 씨앗 도서관을 운영하는 발표자 A입니다.",
     "en": "Hello, I'm Speaker A, and I run the seed library at Brindlewood Commons."},
    {"id": "s10", "ko": "이 비율이 다른 동네에서도 같을지는 아직 모릅니다.", "en": null}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `sentences` | The talk in speaking order. Matching looks a few sentences ahead of the last one matched |
| `id` | Unique id within the script (required) |
| `ko` | The sentence as you plan to say it (required) |
| `en` | Its English. `null` leaves the line empty with `--mt script` and sends the sentence to the translator with `--mt gcp` |

Other fields (such as `title` or `note`) are ignored, so you can keep your own notes in the file.

## Timing file (file mode with `--stt offline`)

`--mode file --stt offline` needs `<audio>.timing.json` next to the WAV: `{"sentences": [{"id", "start", "end", "ko"}]}`
with times in seconds. The stand-in emits growing partial results inside each span and the final text at `end`.
It exists to test the pipeline without a network.
