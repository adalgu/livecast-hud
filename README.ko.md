# LiveCast HUD

[English](README.md)

LiveCast HUD는 한국어 발표에 영어 자막을 실시간으로 붙여 슬라이드 위에 띄웁니다.
발표자의 말을 Google Cloud Speech-to-Text로 알아듣고 전체화면 PowerPoint나 Keynote
슬라이드 쇼 위의 얇은 자막 바에 영어를 보여 줍니다. 대본을 미리 준비했다면 말한 문장을
대본과 맞춰 직접 써 둔 영어를 그대로 띄웁니다. 이때는 클라우드 번역을 전혀 쓰지 않습니다.

![자막 바: 영어 두 줄, 말하는 중인 문장의 흐린 줄, 한국어 줄](docs/screenshot.png)

## 데모

![데모: 중립 예시 덱 위에 앱의 자막 바가 뜬 모습](docs/demo.gif)

demo 모드로 만든 화면(마이크 없음)입니다. 앱이 대본을 입력하며 자막 바를 그렸고, 그 바를
2560x1080 화면에서의 실제 위치 그대로 덱 슬라이드 위에 얹어 프레임을 만들었습니다.
라이브 모드는 Google 음성 인식을 씁니다.

- [데모 덱(PPTX)](examples/demo-deck.pptx)과 [슬라이드 이미지](docs/demo/). 덱은 지어낸 중립
  내용이고 한국어이며, 무료 글꼴 나눔고딕을 썼습니다.
- 직접 실행: `livecast-hud --config examples/demo-talk.json`
- 원본 크기 프레임: [하나](docs/demo/demo-1.png), [둘](docs/demo/demo-2.png)

## 왜 쓰나

- **내가 쓴 영어가 나옵니다.** 대본에 있는 문장에는 미리 준비한 번역을 띄웁니다. 이름과
  용어는 용어표대로 맞춥니다.
- **발표를 방해하지 않습니다.** 자막 바가 전체화면 슬라이드 위에 떠 있고 클릭은 그대로
  통과시킵니다. 클리커와 마우스는 계속 슬라이드를 넘깁니다.
- **1분이면 오프라인으로 써 볼 수 있습니다.** 데모 모드는 마이크도, 계정도, 네트워크도
  필요 없습니다.

## 준비물

- macOS. 자막 창이 macOS 창 기능에 기댑니다.
- Python 3.10 이상.
- 라이브 자막을 쓸 때만: 결제가 켜진 Google Cloud 프로젝트와
  [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)(`gcloud`).

## 빠른 시작 (데모, 클라우드 없이)

```sh
git clone https://github.com/adalgu/livecast-hud.git && cd livecast-hud
python3 -m pip install -e .
livecast-hud --config examples/talks.json
```

주 화면 아래쪽에 자막 바가 뜨고 예시 발표를 한 문장씩 되풀이해 보여 줍니다. 한 번만 보려면
`--exit-on-end`를 붙입니다. 네트워크로는 아무것도 나가지 않습니다.

끝내려면 터미널에서 `Ctrl+C`를 누릅니다. 자막 바를 클릭한 뒤 `Esc`를 눌러도 됩니다.

다른 Python 작업과 패키지를 섞고 싶지 않다면 설치 전에 가상 환경을 만드세요:
`python3 -m venv .venv && source .venv/bin/activate`.

## Google Cloud로 라이브 자막 켜기

라이브 모드는 목소리를 Google로 보내고 비용이 듭니다. 처음 한 번만 아래처럼 준비합니다.
`YOUR_PROJECT_ID`는 내 프로젝트 ID로 바꿉니다.

1. Google Cloud용 추가 패키지를 설치합니다.

   ```sh
   python3 -m pip install -e '.[gcp]'
   ```

2. 결제가 켜진 프로젝트에서 API 두 개를 켭니다. 라이브 모드에는 Speech-to-Text가 늘
   필요하고 Cloud Translation은 `--mt gcp`를 쓸 때만 씁니다.

   ```sh
   gcloud services enable speech.googleapis.com translate.googleapis.com --project YOUR_PROJECT_ID
   ```

3. 애플리케이션 기본 사용자 인증 정보(ADC)로 로그인합니다. API 사용료는 4단계에서 지정한
   프로젝트로 청구됩니다.

   ```sh
   gcloud auth application-default login
   ```

   선택: `LIVECAST_GCP_ACCOUNT`를 지정하면 ADC 대신 `gcloud`에서 그 계정의 액세스 토큰을
   받아 씁니다.

4. 쓸 프로젝트를 알려 줍니다. 실행할 때마다 `--gcp-project YOUR_PROJECT_ID`를 붙이거나
   셸에서 한 번 지정해 둡니다.

   ```sh
   export LIVECAST_GCP_PROJECT=YOUR_PROJECT_ID
   ```

5. 마이크 번호를 확인합니다. 장치 목록만 보여 주고 녹음은 하지 않습니다.

   ```sh
   livecast-hud --list-devices
   ```

6. 라이브 자막을 시작합니다. 마이크 번호는 5단계에서 본 번호를 씁니다.

   ```sh
   livecast-hud --config examples/talks.json --mode live --stt gcp --device 1
   ```

실제 발표에서는 `scripts/run_live.sh`를 쓰면 같은 일을 안전장치와 함께 합니다.
`LIVECAST_GCP_CONFIRMED=1`을 지정하기 전에는 시작하지 않으므로, 시험 삼아 돌리다 실수로
과금되는 일이 없습니다. 클릭 통과를 켠 채로 시작합니다. 설정 파일은 알아서 고르지 않으니
`--config`를 붙이거나 `LIVECAST_CONFIG`를 지정합니다.

```sh
LIVECAST_GCP_CONFIRMED=1 scripts/run_live.sh --config examples/talks.json --device 1
```

**마이크 권한.** 처음 실행하면 macOS가 터미널 앱(터미널, iTerm2 등)에 마이크를 허용할지
묻습니다. **허용**을 누릅니다. **허용 안 함**을 눌렀다면 **시스템 설정 → 개인정보 보호 및
보안 → 마이크**에서 켜고 터미널을 다시 엽니다. 권한이 없으면 macOS가 무음을 넘겨 자막이
나오지 않습니다.

**다른 권한은 필요 없습니다.** 화면 기록이나 손쉬운 사용 권한은 쓰지 않습니다. LiveCast
HUD는 자기 창만 그리고 화면이나 다른 앱으로 가는 키 입력은 읽지 않습니다.

**비용 (대략).** 아래 가격은 2026-10-22에 Google 공식 페이지에서 확인했습니다. 바뀔 수
있으니 발표 전에 다시 보세요:
[Speech-to-Text 가격](https://cloud.google.com/speech-to-text/pricing),
[Cloud Translation 가격](https://cloud.google.com/translate/pricing).

| 서비스 | 쓰는 경우 | 가격 | 발표 1시간 |
| --- | --- | --- | --- |
| Speech-to-Text (V1 API, 표준 모델, 데이터 로깅 끔) | `--stt gcp` | 매월 처음 60분 무료, 그 뒤 분당 US$0.024 | 약 US$1.44 |
| Cloud Translation (NMT) | `--mt gcp`일 때만 | 매월 처음 50만 자 무료, 그 뒤 100만 자당 US$20 | 많아야 약 40만 자, 대개 무료 범위 안 |

정리하면 라이브 자막 1시간에 약 US$1.50이 듭니다. 무료 글자 수를 다 쓴 뒤 `--mt gcp`까지
쓰면 많게는 약 US$8이 더 듭니다. 리허설도 발표와 똑같이 과금됩니다.

## PowerPoint·Keynote 전체화면과 함께 쓰기

1. LiveCast HUD를 먼저 켜고 슬라이드 쇼를 시작합니다. 자막 바는 모든 Space에 함께 뜨는
   전체화면 보조 창이라 슬라이드 쇼 위에서도 보입니다. Dock 아이콘이 없고 슬라이드의
   포커스를 가져가지 않습니다.
2. 클릭 통과를 켭니다. `--click-through`를 붙이거나 자막 바에서 `C`를 누릅니다. 그러면
   클릭과 키 입력이 자막 바를 지나 슬라이드로 갑니다.
3. 프로젝터를 쓸 때: 화면 미러링이면 따로 할 일이 없습니다. 확장 모드(발표자 보기 등)라면
   `--screen N`으로 프로젝터 화면 번호를 지정합니다. 화면 목록은 시작할 때 터미널에
   나옵니다.
4. 자막 바가 슬라이드 아래쪽 내용을 가리면 `--position top`을 씁니다.
5. 실제 발표 환경에서 한 번 리허설하세요. 화면 구성과 앱 버전마다 다를 수 있습니다.

**클릭 통과를 켜면 자막 바는 마우스와 키보드를 받지 않습니다.** 발표를 바꾸거나 한국어
줄을 켤 때는 아래 터미널 명령을 씁니다. 자막 바를 옮기거나 클릭 통과를 다시 끄려면
`Ctrl+C`로 끝내고 `--no-click-through`를 붙여 다시 시작합니다.

터미널에서 `Ctrl+C`를 누르면 언제든 자막 바가 바로 사라집니다.

## 키와 터미널 명령

### 키

먼저 자막 바를 클릭합니다. 키는 클릭 통과가 꺼져 있을 때만 듣습니다.

| 키 | 동작 |
| --- | --- |
| `1` / `2` | 자막 바를 화면 아래 / 위로 |
| `T` 또는 `K` | 한국어 줄 켜기·끄기 |
| `F1` … `F9` | 발표 1 … 9로 바꾸기 (맥북은 `fn`을 함께) |
| `C` | 클릭 통과 켜기. 켠 뒤에는 자막 바가 키를 받지 않으니 터미널을 씁니다 |
| `+` / `-` | 글자 크게 / 작게 |
| `Esc` 또는 `Q` | 끝내기 |
| 마우스로 끌기 | 자막 바 옮기기 |


### 터미널 명령

클릭 통과를 켠 상태에서도 됩니다. 프로그램을 띄운 터미널에 입력하고 Enter를 누릅니다.

| 입력 | 동작 |
| --- | --- |
| `1` … `9` | 발표 1 … 9로 바꾸기 |
| `k` | 한국어 줄 켜기·끄기 |

설정에 없는 번호는 안내만 찍고 무시합니다. `Ctrl+C`(Enter 없이)를 누르면 끝납니다.

### 명령 줄 옵션

`livecast-hud --help`로도 같은 목록을 볼 수 있습니다.

| 옵션 | 하는 일 |
| --- | --- |
| `--config PATH` | 발표·대본·용어를 담은 설정 파일. 기본값: `LIVECAST_CONFIG`. 데모 모드에는 꼭 필요합니다 |
| `--mode demo\|file\|live` | `demo`(기본)는 마이크 없이 대본 문장을 흘려 보여 줍니다. `file`은 WAV 파일을 실제 속도로 재생합니다. `live`는 마이크를 씁니다 |
| `--audio PATH` | `--mode file`에 쓸 WAV 파일(16kHz, 모노, 16비트) |
| `--stt offline\|gcp` | 음성 인식. `offline`(기본)은 `--mode file`용 시험 대역으로, WAV 옆의 `<파일>.timing.json`을 읽습니다. `gcp`는 Google Cloud Speech-to-Text입니다 |
| `--mt script\|gcp` | 영어를 어디서 가져올지. `script`(기본)는 대본에 있는 문장이면 준비한 영어를 보여 주고 없으면 영어 줄을 비웁니다. `gcp`는 나머지를 Google Cloud Translation으로 보냅니다 |
| `--talk N` | 처음 시작할 발표, 1~9. 기본값: 설정 파일의 가장 작은 번호 |
| `--gcp-project ID` | Google Cloud 프로젝트. 기본값: `LIVECAST_GCP_PROJECT` |
| `--device ID` | 마이크 번호 또는 이름 일부 |
| `--list-devices` | 마이크 목록만 보여 주고 끝냅니다. 녹음하지 않습니다 |
| `--screen N` | 자막 바를 띄울 화면. 기본값: 주 화면 |
| `--position bottom\|top` | 자막 바 위치. 기본값: 아래 |
| `--click-through` / `--no-click-through` | 클릭 통과를 켜고 / 끄고 시작 |
| `--en-only` | 영어만 보여 주고 한국어 줄은 숨김 |
| `--log PATH` | 화면에 보인 내용과 시각을 JSON Lines 파일로 기록 |
| `--exit-on-end` | 오디오 파일이 끝나거나 데모가 한 바퀴 돌면 3초 뒤 종료 |
| `--snapshot-at SECONDS` / `--snapshot-dir DIR` | 시험용: 지정한 시각(쉼표로 구분)에 자막 바를 PNG로 저장 |
| `--version` | 버전을 보여 주고 끝냅니다 |

| 환경 변수 | 뜻 |
| --- | --- |
| `LIVECAST_CONFIG` | `--config`의 기본값 |
| `LIVECAST_GCP_PROJECT` | `--gcp-project`의 기본값 |
| `LIVECAST_GCP_ACCOUNT` | 선택. ADC 대신 `gcloud`에서 이 계정의 액세스 토큰을 받아 씀 |
| `LIVECAST_GCP_CONFIRMED` | `1`이면 `scripts/run_live.sh`가 시작됨 |

## 내 발표 대본과 용어 넣기

발표마다 다른 내용은 코드가 아니라 JSON 설정 파일 하나에 들어 있습니다. 예시를 복사해
고친 다음 `--config`로 지정하면 됩니다.

```sh
cp -R examples my-talk
livecast-hud --config my-talk/talks.json
```


설정 파일에 들어가는 것:

- **`talks`.** 발표는 `"1"`부터 `"9"`까지 아홉 개까지 둘 수 있습니다. 발표마다 이름(`name`),
  대본 파일 경로(`script`, 설정 파일 기준 상대 경로), 데모 모드에서 보여 줄 문장 id를 적습니다.
- **대본 파일.** 문장마다 한국어와 영어를 짝지어 둡니다. `--mt script`에서는 대본과 비슷한
  문장을 말하면 그 문장의 영어가 뜹니다.
- **`glossary`.** 이름이나 제품명처럼 늘 같은 영어로 옮겨야 하는 한국어 용어입니다. 사람들이
  흔히 발음하는 꼴도 함께 적어 둡니다.
- **`hints`.** 이름이나 전문 용어처럼 음성 인식이 미리 알고 있으면 좋은 단어입니다.
- **`spoken_fixes`, `english_fixes`, `match_aliases`.** 음성 인식이나 번역이 자꾸 틀리는
  말을 고치는 표와, 대본 속 표현을 달리 말하는 방법입니다.

맨 위에 적은 칸은 모든 발표가 함께 쓰고, 발표 안에 적은 칸은 그 발표만 씁니다.

대본 파일은 이 정도만 있으면 됩니다.

```json
{"sentences": [
  {"id": "s1", "ko": "오늘 발표를 시작하겠습니다.", "en": "Let me begin today's talk."}
]}
```

요령:

- 한 항목에 한 문장씩, 실제로 말할 그대로 씁니다.
- 숫자와 시각은 여러 가지로 읽힐 수 있습니다. 흔한 읽기 방식은 같은 말로 보고 맞춥니다.
- `--mt script`에서는 대본에 없는 말에 영어가 붙지 않습니다. 기계 번역이라도 붙이려면
  `--mt gcp`를 씁니다.

전체 설명: [docs/configuration.md](docs/configuration.md).

## 동작 원리

1. 마이크(또는 WAV 파일)에서 소리를 100ms 단위로 받습니다.
2. Speech-to-Text가 한국어 중간 결과와 확정 결과를 계속 돌려줍니다. Google의 스트림 한
   개당 시간 제한(약 5분)이 오기 전에 새 스트림으로 갈아탑니다. 연결이 끊기면 마지막 자막을
   그대로 두고 다시 연결될 때까지 상태 점이 주황색으로 바뀝니다.
3. 들어온 한국어를 대본과 맞춰 봅니다. 몇 문장 앞까지 함께 보기 때문에 문장을 다 말하기
   전에 영어가 뜰 수 있습니다. `--mt gcp`에서는 대본에 없는 말을 Cloud Translation으로
   보냅니다. 말하는 중인 문장은 0.8초에 한 번까지만 보냅니다.
4. 자막 바에는 확정된 영어 두 줄, 말하는 중인 문장의 흐린 두 줄, 원하면 한국어 줄이
   나옵니다.

흐름도와 자세한 설명: [docs/architecture.md](docs/architecture.md).

## 개인정보와 한계

개인정보:

- `--stt gcp`를 쓰면 마이크 소리가 내 프로젝트의 Google Cloud Speech-to-Text로 갑니다.
  `--mt gcp`를 쓰면 알아들은 한국어 글이 Cloud Translation으로 갑니다. 내 프로젝트에
  적용되는 Google Cloud 약관을 따릅니다.
- 데모 모드, 그리고 `--stt offline --mt script`로 돌리는 파일 모드는 네트워크를 전혀 쓰지
  않습니다.
- 청중이나 함께 발표하는 사람이 자기 목소리가 클라우드로 간다는 걸 알아야 한다면 시작
  전에 알려 주세요.
- `--log` 파일에는 알아들은 내용이 모두 남습니다. 남에게 넘기지 마세요.

한계:

- macOS 전용입니다. 자막 창이 macOS 창 기능에 기댑니다.
- 한국어로 말하고 영어 자막을 띄우는 데 맞춰져 있습니다. 인식 언어는 한국어, 번역 언어는
  영어로 정해져 있습니다.
- 대본 맞춤은 대본에 가깝게 말할수록 잘 됩니다.
- 라이브 모드는 안정된 인터넷이 필요합니다. 새 스트림으로 갈아타거나 다시 연결하는 동안
  몇 단어를 놓칠 수 있습니다.
- 설정 파일 하나에 발표는 아홉 개까지입니다(`F1`~`F9`).
- `--stt offline`은 실제 음성 인식이 아닙니다. 시험용으로 시각 파일을 다시 재생합니다.

## 개발

```sh
python3 -m pip install -e '.[dev]'
QT_QPA_PLATFORM=offscreen python3 -m pytest
ruff check .
```

시험은 네트워크도 마이크도 쓰지 않습니다. 풀 리퀘스트를 보내기 전에
[CONTRIBUTING.md](CONTRIBUTING.md)를, 보안 문제를 비공개로 알릴 때는
[SECURITY.md](SECURITY.md)를 보세요. 바뀐 내용은 [CHANGELOG.md](CHANGELOG.md)에 있습니다.

## 라이선스

Copyright 2026 Kunwoo Kim

[Apache License 2.0](LICENSE)으로 배포합니다. [NOTICE](NOTICE)도 함께 보세요.
