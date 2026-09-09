"""Уборка файлов и защита диска.

Место на сервере общее: рядом живут база и другие проекты. Папка с фото
росла без всякой границы — загруженный, но не привязанный к вещи снимок
оставался навсегда, как и снимки удалённых вещей. На момент написания
больше половины папки было именно таким мусором.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import Item

settings = get_settings()
log = logging.getLogger("media")

#: Расширения, которые кладёт загрузка. Должны покрывать ALLOWED_TYPES из
#: routers/media (импортировать оттуда нельзя — тот модуль импортирует
#: этот). Расхождение ловит проверка в hardening_test: добавят формат в
#: загрузку, забудут здесь — и его файлы копились бы вечно.
MEDIA_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})

LOCAL_PREFIX = "local:"


def free_mb(path: Path) -> int:
    """Сколько мегабайт свободно на диске, где лежит папка."""
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        # Не смогли узнать — не мешаем работать: отказ по неизвестной причине
        # хуже, чем принятая загрузка.
        return 10 ** 9


async def collect_orphans(dry_run: bool = True) -> tuple[int, int]:
    """Удаляет файлы, на которые не ссылается ни одна вещь.

    Свежие не трогаем: снимок загружают до того, как вещь создана, и между
    этими шагами человек заполняет форму. Удалив такой файл, мы сломали бы
    добавление вещи прямо у него в руках.

    Возвращает (сколько файлов, сколько байт).
    """
    media = Path(settings.media_dir)
    if not media.exists():
        return 0, 0

    async with SessionLocal() as session:
        rows = (await session.execute(select(Item.photo_file_ids))).scalars().all()
    used = {
        entry[len(LOCAL_PREFIX):]
        for lst in rows
        for entry in (lst or [])
        if entry.startswith(LOCAL_PREFIX)
    }

    cutoff = time.time() - settings.orphan_media_hours * 3600
    count = size = 0
    for f in media.iterdir():
        if not f.is_file() or f.name in used:
            continue
        # Только то, что сами и кладём. Иначе сборщик выносит всё, на что
        # нет ссылки в базе, — он уже удалил служебный .gitkeep, которым
        # папка держится в репозитории.
        if f.name.startswith(".") or f.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_mtime > cutoff:
            continue
        count += 1
        size += st.st_size
        if not dry_run:
            try:
                f.unlink()
            except OSError as e:
                log.warning("не удалось удалить %s: %s", f.name, e)
    return count, size
