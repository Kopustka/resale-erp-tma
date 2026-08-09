"""Клонирование дизайна поста: пример из канала -> шаблон с плейсхолдерами.

Пользователь пересылает боту пост, который ему нравится. Модель разбирает
его структуру (эмодзи, порядок блоков, разметку) и заменяет конкретные
значения на плейсхолдеры, сохраняя вёрстку. Ответу модели не доверяем:
результат чинится и валидируется перед сохранением.
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import get_settings
from .ai_describe import AiGenerationError, AiNotConfigured
from .post_template import (
    ALLOWED_TAGS,
    PLACEHOLDERS,
    TemplateError,
    VALID_KEYS,
    _PLACEHOLDER_RE,
    _TAG_RE,
    render_demo,
    validate_body,
)

settings = get_settings()

MAX_SAMPLE_CHARS = 3000

_PLACEHOLDER_DOC = "\n".join(
    f"- {{{p['key']}}} — {p['label']} (например: {p['example']})" for p in PLACEHOLDERS
)

CLONE_PROMPT = f"""Ты превращаешь пример поста из Telegram-канала о продаже одежды в переиспользуемый ШАБЛОН.

Тебе дают текст поста (с HTML-разметкой Telegram). Нужно сохранить его ДИЗАЙН и заменить конкретные данные товара на плейсхолдеры.

Доступные плейсхолдеры (использовать можно ТОЛЬКО их):
{_PLACEHOLDER_DOC}

Правила:
1. Сохраняй структуру один в один: порядок строк, пустые строки, эмодзи, символы-разделители (·, |, —), отступы, регистр подписей.
2. Сохраняй HTML-разметку Telegram: <b>, <i>, <u>, <s>, <code>, <pre>, <a href="...">, <blockquote>, <tg-spoiler>. Другие теги запрещены.
3. Конкретные значения товара заменяй плейсхолдерами: название вещи -> {{title}}, цена -> {{price}} или {{price_line}}, размер -> {{size}}, бренд -> {{brand}}, замеры -> {{measurements}} либо по отдельности {{length}}/{{width}}/{{sleeve}}, хэштеги -> {{hashtags}} и т.д.
4. Постоянный текст оставляй как есть: слова «Размер:», «Цена:», «Замеры», призывы «пишите в личку», названия канала, эмодзи-иконки.
5. Контакт продавца или подпись канала в конце заменяй на {{signature}}.
6. НЕ добавляй строки, которых не было в примере. НЕ придумывай новые блоки.
7. Если в примере есть данные, для которых нет подходящего плейсхолдера, оставь их обычным текстом.
8. Название шаблона придумай короткое и по смыслу (до 40 символов), на русском.

Ответь СТРОГО в формате JSON без markdown и пояснений:
{{"name": "<короткое название шаблона>", "body": "<тело шаблона с переводами строк как \\n>"}}"""


BRIEF_PROMPT = f"""Ты составляешь ШАБЛОН поста для Telegram-канала, где продают б/у одежду (ресейл).

Пользователь словами описывает, какой пост хочет. Твоя задача — собрать шаблон под это описание.

Доступные плейсхолдеры (использовать можно ТОЛЬКО их):
{_PLACEHOLDER_DOC}

Правила:
1. Шаблон — это готовая вёрстка поста: постоянный текст плюс плейсхолдеры вместо данных товара.
2. Разметка только телеграмная: <b>, <i>, <u>, <s>, <code>, <pre>, <a href="...">, <blockquote>, <tg-spoiler>. Другие теги запрещены.
3. Название вещи почти всегда стоит выделить жирным и поставить первым.
4. Не выдумывай плейсхолдеры вне списка. Если нужного поля нет — обойдись постоянным текстом.
5. Каждый блок на отдельной строке. Пустые строки между смысловыми блоками — это нормально и улучшает читаемость.
6. Не пиши инструкций и пояснений внутри шаблона. Только то, что должно оказаться в посте.
7. Держись в пределах 700 символов: подпись поста в Telegram ограничена 1024 вместе с подставленными данными.
8. Название шаблона придумай короткое и по смыслу (до 40 символов), на русском.

Ответь СТРОГО в формате JSON без markdown и пояснений:
{{"name": "<короткое название>", "body": "<тело шаблона с переводами строк как \\n>"}}"""


def _strip_disallowed_tags(body: str) -> str:
    """Убирает теги вне белого списка, оставляя их содержимое."""

    def sub(m: re.Match[str]) -> str:
        return m.group(0) if m.group(2).lower() in ALLOWED_TAGS else ""

    return _TAG_RE.sub(sub, body)


def _strip_all_tags(body: str) -> str:
    return _TAG_RE.sub("", body)


def repair(body: str) -> str:
    """Приводит ответ модели к валидному шаблону, насколько это возможно."""
    # Выдуманные моделью плейсхолдеры молча убираем — иначе они утекут в пост.
    body = _PLACEHOLDER_RE.sub(
        lambda m: m.group(0) if m.group(1) in VALID_KEYS else "", body
    )
    body = _strip_disallowed_tags(body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    try:
        validate_body(body)
        return body
    except TemplateError:
        pass
    # Разметка не сходится — теряем оформление, но сохраняем структуру текста.
    plain = re.sub(r"\n{3,}", "\n\n", _strip_all_tags(body)).strip()
    validate_body(plain)
    return plain


async def _ask_gemini(prompt: str, timeout: int = 45) -> dict:
    """Один запрос к модели со строгим JSON на выходе."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}
        )
    if r.status_code == 429:
        raise AiGenerationError("Квота Gemini исчерпана — попробуйте чуть позже")
    if r.status_code != 200:
        raise AiGenerationError(f"Gemini API: HTTP {r.status_code}")
    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as e:
        raise AiGenerationError(f"Не удалось разобрать ответ модели: {e}")


def _finish(parsed: dict, fallback_name: str) -> dict[str, str]:
    """Общая для обоих режимов проверка и починка ответа модели."""
    name = str(parsed.get("name") or "").strip()[:60]
    try:
        tpl_body = repair(str(parsed["body"]))
    except KeyError:
        raise AiGenerationError("Модель не вернула тело шаблона")
    except TemplateError as e:
        raise AiGenerationError(f"Модель вернула негодный шаблон: {e}")
    if not render_demo(tpl_body).strip():
        raise AiGenerationError("Шаблон не даёт текста на демо-данных")
    return {"name": name or fallback_name, "body": tpl_body}


async def generate_template_from_brief(brief: str) -> dict[str, str]:
    """Собирает шаблон по словесному описанию. {"name","body"}."""
    if not settings.gemini_api_key:
        raise AiNotConfigured()
    brief = (brief or "").strip()
    if not brief:
        raise AiGenerationError("Пустое описание")
    parsed = await _ask_gemini(BRIEF_PROMPT + "\n\nОПИСАНИЕ ОТ ПОЛЬЗОВАТЕЛЯ:\n" + brief[:1500])
    return _finish(parsed, "Шаблон по описанию")


async def clone_template_from_sample(sample_html: str) -> dict[str, str]:
    """По примеру поста возвращает {"name","body"}. Бросает AiNotConfigured/AiGenerationError."""
    if not settings.gemini_api_key:
        raise AiNotConfigured()

    sample = (sample_html or "").strip()
    if not sample:
        raise AiGenerationError("Пустой пример поста")
    sample = sample[:MAX_SAMPLE_CHARS]

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    body = {
        "contents": [
            {"parts": [{"text": CLONE_PROMPT + "\n\nПРИМЕР ПОСТА:\n" + sample}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            # Низкая температура: копируем чужую вёрстку, творчество здесь вредит.
            "temperature": 0.2,
        },
    }
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(
            url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}
        )
    if r.status_code == 429:
        raise AiGenerationError("Квота Gemini исчерпана — попробуйте чуть позже")
    if r.status_code != 200:
        raise AiGenerationError(f"Gemini API: HTTP {r.status_code}")

    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        name = str(parsed.get("name") or "").strip()[:60]
        tpl_body = str(parsed["body"])
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as e:
        raise AiGenerationError(f"Не удалось разобрать ответ модели: {e}")

    try:
        tpl_body = repair(tpl_body)
    except TemplateError as e:
        raise AiGenerationError(f"Модель вернула негодный шаблон: {e}")

    if not render_demo(tpl_body).strip():
        raise AiGenerationError("Шаблон не даёт текста на демо-данных")

    return {"name": name or "Шаблон из поста", "body": tpl_body}
