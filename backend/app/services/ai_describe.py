"""AI-генерация названия и описания вещи по фото (Gemini API).

Промт задан владельцем проекта (стиль ресейл-карточек: английское название
с поисковым словом + описание ровно в 2 предложения). Модель обязана вернуть
строгий JSON {"title","description"} — так поля формы заполняются надёжно.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

from ..config import get_settings
from . import gemini

settings = get_settings()

# Промт владельца (основа генерации). Правится здесь.
OWNER_PROMPT = """Ты — эксперт по описанию одежды для маркетплейсов и локальных ресейл-площадок (Kufar, Avito и др.). Твоя задача — анализировать загруженные фотографии одежды и сразу выдавать краткое карточечное описание.

Правила генерации:
1. НАЗВАНИЕ (Обязательное условие):
   - Должно быть на английском языке, в лаконичном формате.
   - ОБЯЗАТЕЛЬНО должно содержать хотя бы одно популярное, легко находимое поисковое слово/категорию (например: Archive, Vintage, Graphic, Basic, Longsleeve, Tee, Hoodie, Zip-up, Cargo, Draped и т.д.), чтобы покупатель легко нашел вещь через поиск.
2. ОПИСАНИЕ:
   - Максимально краткое: 2 предложения (не больше).
   - Стиль: лаконичный, адаптированный под молодёжную и альтернативную fashion-эстетику (archive, grunge, opium, darkwear), но без излишнего сленга (избегай слов «залетит», «готический» и т.п.).
   - Содержание: фасон/крой, материал/текстура, цвет/оттенок, ключевые детали (принт, вырез, кнопки, шнурки и т.д.) и то, как вещь смотрится в аутфите.
   - НЕ упоминай слова «фото», «снимок», «на изображении» или названия файлов."""

JSON_INSTRUCTION = """

Ответь СТРОГО в формате JSON без пояснений и markdown:
{"title": "<название по правилам>", "description": "<описание ровно в 2 предложения на русском>"}"""

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 7 * 1024 * 1024


class AiNotConfigured(Exception):
    """GEMINI_API_KEY не задан."""


class AiGenerationError(Exception):
    """Ошибка вызова/разбора ответа модели."""


async def _load_photo(entry: str) -> tuple[bytes, str] | None:
    """Байты фото по записи photo_file_ids: local-файл или Telegram file_id."""
    if entry.startswith("local:"):
        name = entry[len("local:"):]
        if "/" in name or "\\" in name or ".." in name:
            return None
        path = Path(settings.media_dir) / name
        if not path.exists():
            return None
        data = path.read_bytes()
        mime = mimetypes.guess_type(name)[0] or "image/jpeg"
        return data, mime
    # Telegram file_id — тянем через Bot API
    try:
        from ..routers.media import _fetch_telegram_file

        return await _fetch_telegram_file(entry)
    except Exception:
        return None


def _build_hints(fields: dict[str, str | None]) -> str:
    known = {
        "Бренд": fields.get("brand"),
        "Категория": fields.get("category"),
        "Размер": fields.get("size"),
        "Цвет": fields.get("color"),
        "Состояние": fields.get("condition"),
    }
    lines = [f"- {k}: {v}" for k, v in known.items() if v and str(v).strip()]
    if not lines:
        return ""
    return (
        "\n\nДополнительно известны данные о вещи (используй их, если не "
        "противоречат фотографиям):\n" + "\n".join(lines)
    )


async def generate_item_description(
    photo_entries: list[str], fields: dict[str, str | None]
) -> dict[str, str]:
    """Возвращает {"title","description"}. Бросает AiNotConfigured/AiGenerationError."""
    if not settings.gemini_api_key:
        raise AiNotConfigured()

    parts: list[dict] = [{"text": OWNER_PROMPT + _build_hints(fields) + JSON_INSTRUCTION}]
    attached = 0
    for entry in photo_entries[:MAX_PHOTOS]:
        loaded = await _load_photo(entry)
        if loaded is None:
            continue
        data, mime = loaded
        if len(data) > MAX_PHOTO_BYTES:
            continue
        parts.append(
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}
        )
        attached += 1
    if attached == 0:
        raise AiGenerationError("Не удалось загрузить ни одного фото")

    # Через общий вызов: повторы на перегрузке и переход на запасную
    # модель при исчерпанной квоте. Раньше здесь была своя копия логики,
    # и генерация падала на первом же 503.
    try:
        parsed = await gemini.call_json(parts, timeout=45, temperature=0.7)
    except gemini.GeminiQuotaExceeded:
        raise AiGenerationError("Дневной лимит Gemini исчерпан — обновите ключ")
    except gemini.GeminiUnavailable as e:
        raise AiGenerationError(f"Модель недоступна: {e}")

    try:
        title = str(parsed["title"]).strip()[:100]
        description = str(parsed["description"]).strip()
    except (KeyError, TypeError) as e:
        raise AiGenerationError(f"Не удалось разобрать ответ модели: {e}")

    if not title or not description:
        raise AiGenerationError("Модель вернула пустые поля")
    return {"title": title, "description": description}
