# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Speech recognition.

- Google Cloud Speech-to-Text streaming (`--stt gcp`; google.cloud.speech v1 API, model latest_long).
- Offline stand-in (`--stt offline`): in file mode, emits the sentences of a timing file as partial and final
  results at their audio times (no external calls).
- Input: microphone (sounddevice, 16 kHz mono, 100 ms chunks, --device) or a WAV file streamed in real time.
- Drops: a stream error sets the status to 'disconnected' (the HUD keeps the last subtitle), reconnects after
  one second, and goes back to 'connected' when results arrive.
"""

import json
import os
import queue
import re
import subprocess
import threading
import time
import wave
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, List, Optional


def redact(text, secrets=()) -> str:
    """Hide the given values (project, account) and token-like strings in messages printed to the terminal"""
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return re.sub(r"ya29\.[0-9A-Za-z_\-.]+|[Bb]earer\s+\S+", "<token>", text)


def _gcp():
    """The Google Cloud libraries are imported only for --stt gcp (the offline path needs no install)."""
    from google.api_core.client_options import ClientOptions
    from google.cloud import speech
    return speech, ClientOptions


def get_gcp_credentials(account: Optional[str] = None):
    """With an account (LIVECAST_GCP_ACCOUNT): a token from `gcloud auth print-access-token --account=…`.
    Without one, or if that fails: None, so the client uses Application Default Credentials."""
    if not account:
        return None
    from google.oauth2 import credentials
    try:
        cmd = ["gcloud", "auth", "print-access-token", f"--account={account}"]
        token = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        if token:
            return credentials.Credentials(token)
    except Exception as e:
        print(f"gcloud token failed, falling back to Application Default Credentials: {redact(e, (account,))}")
    return None


def list_input_devices() -> List[str]:
    """Microphone input devices (no stream is opened, nothing is recorded). '*' marks the default device."""
    import sounddevice as sd
    default_in = sd.default.device[0]
    return [
        f"{'*' if i == default_in else ' '}[{i}] {d['name']} (input {d['max_input_channels']}ch, {int(d['default_samplerate'])}Hz)"
        for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0
    ]


class FileAudioSource:
    """Streams a WAV file in real time, 100 ms per chunk, in place of a microphone. 16 kHz, mono, 16-bit only.
    Chunk k (audio k·0.1 to (k+1)·0.1 s) is sent (k+1)·0.1 s after start — when a microphone would have it."""

    def __init__(self, path: str, rate: int = 16000, chunk_duration_sec: float = 0.1):
        self.path = Path(path)
        with wave.open(str(self.path), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (rate, 1, 2):
                raise ValueError(f"WAV must be {rate} Hz, mono, 16-bit: {self.path}")
            self.pcm = w.readframes(w.getnframes())
        self.rate = rate
        self.chunk_sec = chunk_duration_sec
        self.chunk_bytes = int(rate * chunk_duration_sec) * 2
        self.duration = len(self.pcm) / 2 / rate
        self.t0: Optional[float] = None    # wall clock (time.time) at audio 0 s
        self.t0_mono: Optional[float] = None  # monotonic clock at audio 0 s
        self.lags: List[float] = []        # per chunk: actual send time − due time, seconds

    def run(self, push: Callable[[Optional[bytes]], None], stop: Callable[[], bool]):
        start = time.monotonic()
        self.t0, self.t0_mono = time.time(), start
        for k, off in enumerate(range(0, len(self.pcm), self.chunk_bytes)):
            if stop():
                break
            due = start + (k + 1) * self.chunk_sec
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.lags.append(time.monotonic() - due)
            push(self.pcm[off: off + self.chunk_bytes])
        push(None)


class OfflineScriptSTT:
    """Recognition stand-in (--stt offline, file mode only). Counts audio time from the chunks it receives and,
    as each sentence's span passes, emits partial results (growing word by word) and a final result at the
    sentence end. Times come from <wav>.timing.json."""

    PARTIAL_EVERY = 0.3  # seconds between partial results

    def __init__(self, audio_path: str, rate: int = 16000, chunk_duration_sec: float = 0.1):
        self.source = FileAudioSource(audio_path, rate, chunk_duration_sec)
        timing = Path(audio_path).with_suffix(".timing.json")
        self.timing = json.loads(timing.read_text(encoding="utf-8"))["sentences"]
        self.rate = rate
        self.audio_t = 0.0
        self.idx = 0
        self.last_partial = (-1.0, "")
        self.is_running = False
        self.on_partial_callback = self.on_final_callback = self.on_end_callback = None

    def start(self, on_partial=None, on_final=None, on_status=None, on_end=None):
        self.on_partial_callback, self.on_final_callback, self.on_end_callback = on_partial, on_final, on_end
        self.is_running = True
        threading.Thread(target=self.source.run, args=(self._on_chunk, lambda: not self.is_running), daemon=True).start()

    def stop(self):
        self.is_running = False

    def _on_chunk(self, chunk: Optional[bytes]):
        if chunk is None:
            if self.on_end_callback:
                self.on_end_callback()
            return
        self.audio_t += len(chunk) / 2 / self.rate
        while self.idx < len(self.timing):
            s = self.timing[self.idx]
            if self.audio_t >= s["end"]:
                if self.on_final_callback:
                    self.on_final_callback(s["ko"])
                self.idx += 1
                self.last_partial = (-1.0, "")
                continue
            if self.audio_t > s["start"] and self.audio_t - self.last_partial[0] >= self.PARTIAL_EVERY:
                words = s["ko"].split()
                n = int(len(words) * (self.audio_t - s["start"]) / (s["end"] - s["start"]))
                text = " ".join(words[:n])
                if text and text != self.last_partial[1] and self.on_partial_callback:
                    self.on_partial_callback(text)
                self.last_partial = (self.audio_t, text)
            break


class LivecastSTTEngine:
    ROTATE_AFTER = 270.0        # open a new stream before the 5-minute (305 s) stream limit; reconnecting after the limit error drops audio
    TOKEN_MAX_AGE = 45 * 60     # on a planned rotation, fetch a new token only when the current one is this old

    def __init__(
        self,
        project_id: str,
        hints: Optional[List[str]] = None,
        rate: int = 16000,
        chunk_duration_sec: float = 0.1,
        device=None,
        audio_path: Optional[str] = None,
        account: Optional[str] = None,
    ):
        self.project_id = project_id
        self.account = account if account is not None else os.environ.get("LIVECAST_GCP_ACCOUNT")
        self.hints = list(hints or [])
        self.rate = rate
        self.chunk_size = int(rate * chunk_duration_sec)
        self.device = device  # microphone index or part of its name (sounddevice rules); None = default device
        self.source = FileAudioSource(audio_path, rate, chunk_duration_sec) if audio_path else None
        self.source_done = False

        self.audio_q: queue.Queue = queue.Queue()
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.status: Optional[str] = None
        self.clock = time.monotonic      # tests use a fake clock
        self.rotations = 0

        self.on_partial_callback: Optional[Callable[[str], None]] = None
        self.on_final_callback: Optional[Callable[[str], None]] = None
        self.on_status_callback: Optional[Callable[[str], None]] = None
        self.on_end_callback: Optional[Callable[[], None]] = None

    def _audio_callback(self, indata, frames, time_info, status):
        if self.is_running:
            self.audio_q.put(bytes(indata))

    def _set_status(self, state: str):
        if state != self.status:
            self.status = state
            if self.on_status_callback:
                self.on_status_callback(state)

    def _redact(self, e) -> str:
        return redact(e, (self.project_id, self.account))

    def _request_generator(self, speech, started: float):
        """Chunks → requests. After ROTATE_AFTER the requests end, so the server sends the remaining results and
        closes the stream. Chunks that arrive meanwhile stay in the queue and go first to the next stream."""
        while self.is_running:
            if self.clock() - started >= self.ROTATE_AFTER:
                return
            try:
                chunk = self.audio_q.get(timeout=0.5)
                if chunk is None:
                    self.source_done = True
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)
            except queue.Empty:
                continue

    def start(
        self,
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_end: Optional[Callable[[], None]] = None,
    ):
        """Start capture (microphone or file) and cloud streaming on a background thread"""
        self.on_partial_callback = on_partial
        self.on_final_callback = on_final
        self.on_status_callback = on_status
        self.on_end_callback = on_end
        self.is_running = True

        if self.source:
            threading.Thread(
                target=self.source.run, args=(self.audio_q.put, lambda: not self.is_running), daemon=True
            ).start()
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.is_running = False
        self.audio_q.put(None)
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)

    def _capture(self):
        """Microphone capture (none for file input)"""
        if self.source:
            return nullcontext()
        import sounddevice as sd
        return sd.RawInputStream(
            samplerate=self.rate,
            blocksize=self.chunk_size,
            device=self.device,
            dtype="int16",
            channels=1,
            callback=self._audio_callback,
        )

    def _run_loop(self):
        speech, ClientOptions = _gcp()
        creds = get_gcp_credentials(self.account)
        client_options = ClientOptions(quota_project_id=self.project_id)
        client = speech.SpeechClient(credentials=creds, client_options=client_options)

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.rate,
            language_code="ko-KR",
            enable_automatic_punctuation=True,
            model="latest_long",
            speech_contexts=[speech.SpeechContext(phrases=self.hints, boost=15.0)] if self.hints else [],
        )

        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,
        )

        print(f"[GCP STT] streaming client ready ({len(self.hints)} speech hints)")
        token_at = self.clock()

        while self.is_running and not self.source_done:
            try:
                if client is None:  # reconnect after an error: new token and new client (the token may have expired mid-talk)
                    client = speech.SpeechClient(credentials=get_gcp_credentials(self.account), client_options=client_options)
                    token_at = self.clock()
                with self._capture():
                    # planned rotations loop in here: same microphone, client and token, no drop shown
                    while self.is_running and not self.source_done:
                        if self.clock() - token_at >= self.TOKEN_MAX_AGE:
                            client = speech.SpeechClient(credentials=get_gcp_credentials(self.account), client_options=client_options)
                            token_at = self.clock()
                        responses = client.streaming_recognize(
                            config=streaming_config, requests=self._request_generator(speech, self.clock())
                        )

                        for response in responses:
                            if not self.is_running:
                                break
                            self._set_status("connected")
                            if not response.results:
                                continue
                            result = response.results[0]
                            if not result.alternatives:
                                continue

                            transcript = result.alternatives[0].transcript

                            if result.is_final:
                                if self.on_final_callback:
                                    self.on_final_callback(transcript)
                            else:
                                if self.on_partial_callback:
                                    self.on_partial_callback(transcript)
                        if self.is_running and not self.source_done:
                            self.rotations += 1
                            print(f"[GCP STT] stream rotation {self.rotations} (before the 5-minute limit, audio continues)")

            except Exception as e:
                if self.is_running:
                    self._set_status("disconnected")
                    print(f"[GCP STT] stream dropped, reconnecting: {self._redact(e)}")
                    time.sleep(1.0)
                    client = None
                else:
                    break
        if self.on_end_callback:
            self.on_end_callback()
