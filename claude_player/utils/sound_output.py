"""
Browser audio streaming for the PyBoy emulator.

PyBoy emulates the GB APU and fills a per-frame audio buffer (pyboy.sound.ndarray)
after every tick().  This module accumulates those int8 stereo frames, packages them
into self-contained WAV chunks, and queues them for the web server to serve via
GET /audio/chunk.  The browser decodes each chunk with Web Audio API and schedules
them back-to-back for continuous, low-latency playback.

Buffer sizing:
  FRAMES_PER_CHUNK = 8  →  8 / 60 ≈ 133 ms per HTTP request  →  ~7.5 req/s
"""

import queue
import struct
import logging
import numpy as np

logger = logging.getLogger(__name__)

_CHANNELS = 2
_BITS = 16
_FRAMES_PER_CHUNK = 8   # frames buffered before a WAV chunk is queued
_MAX_QUEUED_CHUNKS = 12  # ~1.6 s of audio; older chunks are dropped if full


def _make_wav(pcm16_bytes: bytes, sample_rate: int, channels: int = 2) -> bytes:
    """Wrap raw int16 LE PCM in a minimal RIFF/WAV header."""
    n = len(pcm16_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + n, b"WAVE",
        b"fmt ", 16,
        1,               # PCM
        channels,
        sample_rate,
        sample_rate * channels * (_BITS // 8),  # byte rate
        channels * (_BITS // 8),                # block align
        _BITS,
        b"data", n,
    )
    return header + pcm16_bytes


class SoundOutput:
    """Buffers PyBoy audio frames into WAV chunks for browser streaming."""

    def __init__(self, sample_rate: int = 48000, enabled: bool = True):
        self._sample_rate = sample_rate
        self._enabled = enabled
        self._chunk_queue: queue.Queue = queue.Queue(maxsize=_MAX_QUEUED_CHUNKS)
        self._buf = bytearray()
        self._frame_count = 0
        if enabled:
            logger.info(f"Sound output ready (browser streaming, rate={sample_rate} Hz)")

    def write(self, sound) -> None:
        """
        Consume one frame of PyBoy audio.  Called after every pyboy.tick().

        Args:
            sound: pyboy.sound  (the Sound object on the PyBoy instance)
        """
        if not self._enabled:
            return
        try:
            samples = sound.ndarray          # shape (n, 2), dtype int8
            if samples is None or samples.size == 0:
                return

            # int8 → int16 LE: scale up to fill the 16-bit range
            pcm16 = (samples.astype(np.int16) * 256).reshape(-1, _CHANNELS)
            self._buf.extend(pcm16.tobytes())
            self._frame_count += 1

            if self._frame_count >= _FRAMES_PER_CHUNK:
                wav = _make_wav(bytes(self._buf), self._sample_rate)
                self._buf.clear()
                self._frame_count = 0
                try:
                    self._chunk_queue.put_nowait(wav)
                except queue.Full:
                    self._chunk_queue.get_nowait()   # drop oldest
                    self._chunk_queue.put_nowait(wav)
        except Exception as e:
            logger.debug(f"Sound buffer error: {e}")

    def get_chunk(self, timeout: float = 1.5) -> bytes | None:
        """
        Block until the next WAV chunk is ready (called from Flask worker thread).
        Returns None on timeout (results in HTTP 204 No Content).
        """
        try:
            return self._chunk_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        """Discard buffered audio and disable future writes."""
        self._enabled = False
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except queue.Empty:
                break
