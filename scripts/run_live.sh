#!/usr/bin/env bash
# Live mode: microphone -> Google Cloud Speech-to-Text -> subtitles, starting with click-through on.
# This sends audio to Google Cloud and is billed to your project, so it refuses to start unless you confirm:
#   LIVECAST_GCP_CONFIRMED=1 scripts/run_live.sh --config my-talk/talks.json --gcp-project YOUR_PROJECT_ID [--device N] [--screen N]
# Arguments after the script name are passed on, so --no-click-through wins over the default --click-through.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${LIVECAST_GCP_CONFIRMED:-}" != "1" ]; then
  echo "Live mode calls Google Cloud (billed). Set LIVECAST_GCP_CONFIRMED=1 to start: LIVECAST_GCP_CONFIRMED=1 scripts/run_live.sh"
  exit 1
fi
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m livecast_hud --mode live --stt gcp --click-through "$@"
