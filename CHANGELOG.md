# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-22

First public release.

### Added

- Subtitle overlay for macOS that joins every Space as a full-screen auxiliary window, so
  it can sit above full-screen slideshows, with click-through so clickers and the mouse
  keep working on the slides.
- Three input modes: `demo` (no microphone, no cloud), `file` (a WAV file played in
  real time), and `live` (microphone).
- Live Korean speech recognition with Google Cloud Speech-to-Text streaming, including a
  planned hand-over to a fresh stream before the service's per-stream time limit.
- Two ways to get English: `script` (match what you say to your prepared script and show
  its English, fully offline) and `gcp` (Google Cloud Translation).
- Per-talk scripts, glossaries, recognition hints and spoken-form fixes loaded from a
  JSON config file (`--config` or `LIVECAST_CONFIG`) instead of being built into the code.
- Layout with two finished English lines, two dimmer in-progress lines and an optional
  Korean source line; a connection dot that turns yellow while reconnecting and keeps
  the last subtitle on screen.
- Keyboard and terminal controls to switch talks, move the bar, resize text and toggle
  the Korean line, even while click-through is on.
- Example config, scripts and glossary for a fictional talk in `examples/`.
- Network-free test suite that fails any test which opens a socket or starts a process.
