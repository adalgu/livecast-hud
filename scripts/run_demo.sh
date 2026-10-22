#!/usr/bin/env bash
# Demo mode with the example talks: no microphone, no network.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m livecast_hud --config "$ROOT/examples/talks.json" --mode demo "$@"
