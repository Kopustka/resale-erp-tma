"""Распознавание речи на своём сервере.

Нейросети уходит только текст: аудио никуда не отправляется, квота на него
не тратится, и разбор перестаёт зависеть от лимитов внешнего сервиса.

Модель поднимается в отдельном процессе на одну запись (см. stt_worker) —
иначе полтора гигабайта висели бы в API постоянно. Одновременно считаем
не больше одной записи: два таких пика памяти сервер не переживёт.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("stt")

_lock = asyncio.Lock()


class SttError(Exception):
    """Распознать не удалось — вызывающий решает, чем заменить."""


def enabled() -> bool:
    return bool(getattr(settings, "stt_enabled", True))


def _worker_env() -> dict:
    env = dict(os.environ)
    # Модели кладём рядом с проектом, а не в домашний каталог рута:
    # так их видно, и они переживают смену пользователя сервиса.
    env.setdefault("HF_HOME", str(settings.media_dir) + "/../.models")
    # Один процесс на запись, два потока — по числу ядер.
    env.setdefault("OMP_NUM_THREADS", "2")
    return env


async def transcribe(data: bytes, suffix: str = ".ogg") -> str:
    """Возвращает распознанный текст. Пустая строка — речи нет."""
    if not enabled():
        raise SttError("распознавание на сервере выключено")
    if not data:
        raise SttError("пустая запись")

    timeout = int(getattr(settings, "stt_timeout", 90))
    model = getattr(settings, "stt_model", "small")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = tmp.name

    try:
        # Две записи одновременно дадут два пика по 1.4 ГБ — сервер столько
        # не держит, поэтому строго по очереди.
        async with _lock:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.stt_worker", path, model,
                cwd=str(settings.media_dir) + "/..",
                env=_worker_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise SttError("распознавание затянулось")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if not out:
        detail = (err or b"").decode(errors="replace")[:200]
        log.warning("stt worker без вывода: %s", detail)
        raise SttError("распознавание не отработало")

    try:
        payload = json.loads(out.decode().strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError, UnicodeDecodeError) as e:
        raise SttError(f"нечитаемый ответ распознавателя: {e}")

    if "error" in payload:
        raise SttError(payload["error"])
    return (payload.get("text") or "").strip()
