"""Приведение записи из браузера к формату, который понимает модель.

MediaRecorder отдаёт разное: Chrome — audio/webm с opus, Safari — audio/mp4
с aac, старые сборки — что-то третье. Gemini документированно принимает
ogg/mp3/wav, и полагаться на то, что он «наверное проглотит» webm, нельзя.

Поэтому всё, что приходит, прогоняем через ffmpeg в ogg/opus. Заодно это
режет вес: голос в 16 кГц моно занимает считанные килобайты.
"""
from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger("audio")

FFMPEG = shutil.which("ffmpeg")
TIMEOUT_SECONDS = 25
# Голос: 16 кГц моно достаточно для распознавания и втрое легче исходника.
SAMPLE_RATE = "16000"
BITRATE = "24k"


class AudioError(Exception):
    """Не удалось перекодировать запись."""


def available() -> bool:
    return FFMPEG is not None


async def to_ogg(data: bytes) -> bytes:
    """Перекодирует любую запись в ogg/opus. Читает stdin, пишет stdout."""
    if not data:
        raise AudioError("пустая запись")
    if FFMPEG is None:
        raise AudioError("ffmpeg не установлен")

    proc = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-vn",                      # видеодорожки в записи быть не должно
        "-ac", "1",
        "-ar", SAMPLE_RATE,
        "-c:a", "libopus",
        "-b:a", BITRATE,
        "-f", "ogg",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=data), timeout=TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise AudioError("перекодирование затянулось")

    if proc.returncode != 0 or not out:
        detail = (err or b"").decode(errors="replace")[:200]
        log.warning("ffmpeg failed: %s", detail)
        raise AudioError("не удалось прочитать запись")
    return out
