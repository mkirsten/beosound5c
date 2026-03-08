# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""Shared TTS announce function for BeoSound 5c services.

Uses piper (local neural TTS) with pre-caching: audio is generated on each
track change and held in RAM, so announcing on button press is near-instant.
"""

import array
import asyncio
import logging
import os
import subprocess

log = logging.getLogger("beo-tts")

_announce_lock = asyncio.Lock()
_cached_audio: bytes | None = None
_cached_text: str | None = None

PIPER_BIN = "/opt/piper/piper/piper"
PIPER_MODEL = "/opt/piper/voices/en_US-lessac-medium.onnx"
PIPER_LIB = "/opt/piper/piper"


def _clean_audio(raw: bytes) -> bytes:
    """Trim trailing silence and apply a short fade-out to raw 16-bit PCM."""
    samples = array.array('h')
    samples.frombytes(raw[:len(raw) - len(raw) % 2])
    # Trim trailing silence (samples below threshold)
    threshold = 200
    end = len(samples)
    while end > 0 and abs(samples[end - 1]) < threshold:
        end -= 1
    if end == 0:
        return raw
    # Fade out last 50ms (1102 samples at 22050 Hz)
    fade_len = min(1102, end)
    for i in range(fade_len):
        pos = end - fade_len + i
        samples[pos] = int(samples[pos] * (1.0 - i / fade_len))
    return samples[:end].tobytes()


def _piper_env() -> dict:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env["LD_LIBRARY_PATH"] = PIPER_LIB
    return env


async def tts_precache(text: str):
    """Pre-generate TTS audio and store in RAM. Call on track changes."""
    global _cached_audio, _cached_text
    if not text or text == _cached_text:
        return
    try:
        piper = await asyncio.create_subprocess_exec(
            PIPER_BIN, "--model", PIPER_MODEL, "--output-raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_piper_env(),
        )
        audio, _ = await piper.communicate(input=text.encode())
        if piper.returncode == 0 and audio:
            _cached_audio = _clean_audio(audio)
            _cached_text = text
            log.info("Pre-cached TTS (%d bytes): %s", len(audio), text)
        else:
            log.warning("Piper pre-cache failed (rc=%s)", piper.returncode)
    except Exception as e:
        log.warning("TTS pre-cache failed: %s", e)


async def tts_announce(text: str, volume: int = 100):
    """Play a TTS announcement via PulseAudio.

    Uses pre-cached audio if available for the given text, otherwise
    generates on the fly. Skips if already speaking.
    """
    global _cached_audio, _cached_text

    if _announce_lock.locked():
        log.info("Announce skipped — already speaking")
        return

    async with _announce_lock:
        env = _piper_env()
        audio = None

        # Use cache if it matches
        if text == _cached_text and _cached_audio:
            audio = _cached_audio
            log.info("Announcing (cached): %s", text)
        else:
            # Generate on the fly
            log.info("Announcing (live): %s", text)
            try:
                piper = await asyncio.create_subprocess_exec(
                    PIPER_BIN, "--model", PIPER_MODEL, "--output-raw",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                )
                audio, _ = await piper.communicate(input=text.encode())
                if piper.returncode != 0 or not audio:
                    log.error("Piper failed (rc=%s)", piper.returncode)
                    return
                audio = _clean_audio(audio)
            except Exception as e:
                log.error("TTS announce failed: %s", e)
                return

        try:
            # Pipe raw PCM to pw-cat (piper --output-raw = 16-bit mono 22050 Hz)
            play = subprocess.Popen(
                ["pw-cat", "--playback", "--raw", "--format=s16", "--rate=22050", "--channels=1",
                 f"--volume={volume / 100:.2f}", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=env,
            )
            await asyncio.get_running_loop().run_in_executor(None, play.communicate, audio)
        except Exception as e:
            log.error("TTS playback failed: %s", e)
