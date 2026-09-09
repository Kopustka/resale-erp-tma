"""Конвертация валют по официальному курсу Нацбанка РБ (api.nbrb.by).

База расчёта — BYN: НБ РБ даёт курс инвалюты к BYN. Любую валюту переводим
в BYN, затем в целевую. Курсы кэшируются (обновляются раз в день).
Если НБ РБ недоступен — используем последний кэш или запасные значения,
чтобы приложение никогда не падало на сохранении цены.
"""
from __future__ import annotations

import logging
import asyncio
import time
from decimal import Decimal, ROUND_HALF_UP

import httpx

log = logging.getLogger("fx")

SUPPORTED = ("BYN", "RUB", "USD", "EUR")
SYMBOLS = {"BYN": "Br", "RUB": "₽", "USD": "$", "EUR": "€"}

# Сколько единиц BYN стоит 1 единица валюты. BYN=1 всегда.
# Запасные значения (актуальны ~2025) — на случай недоступности НБ РБ.
_FALLBACK_TO_BYN: dict[str, Decimal] = {
    "BYN": Decimal("1"),
    "RUB": Decimal("0.033"),
    "USD": Decimal("3.10"),
    "EUR": Decimal("3.40"),
}

_cache: dict[str, Decimal] = {}
_cache_ts: float = 0.0
_TTL = 6 * 3600  # 6 часов
#: Сколько ждать перед новой попыткой, если Нацбанк не ответил. Без паузы
#: каждая конвертация лезла в сеть заново и висела по 12 секунд: сохранение
#: одной вещи в валюте превращалось в минуту с лишним.
_RETRY_AFTER_FAIL = 300
_lock = asyncio.Lock()
_NBRB_URL = "https://api.nbrb.by/exrates/rates?periodicity=0"


async def _refresh() -> None:
    global _cache, _cache_ts
    rates: dict[str, Decimal] = {"BYN": Decimal("1")}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(_NBRB_URL)
            r.raise_for_status()
            for row in r.json():
                abbr = row.get("Cur_Abbreviation")
                if abbr in SUPPORTED and abbr != "BYN":
                    scale = Decimal(str(row["Cur_Scale"]))
                    official = Decimal(str(row["Cur_OfficialRate"]))
                    rates[abbr] = official / scale  # BYN за 1 единицу валюты
        # добираем недостающие из фолбэка
        for cur in SUPPORTED:
            rates.setdefault(cur, _FALLBACK_TO_BYN[cur])
        _cache = rates
        _cache_ts = time.time()
        log.info("NBRB rates refreshed: %s", {k: str(v) for k, v in rates.items()})
    except Exception as e:  # noqa: BLE001
        log.warning("NBRB fetch failed (%s), using %s", e, "cache" if _cache else "fallback")
        if not _cache:
            _cache = dict(_FALLBACK_TO_BYN)
        # Отметку времени двигаем в любом случае — иначе кэш считается
        # просроченным вечно и мы ходим в недоступный Нацбанк на каждую
        # конвертацию. Ставим её в прошлое, чтобы повтор случился через
        # _RETRY_AFTER_FAIL, а не через полный TTL.
        _cache_ts = time.time() - _TTL + _RETRY_AFTER_FAIL


async def _rate_to_byn(cur: str) -> Decimal:
    # Под замком: сохранение вещи конвертирует до шести сумм подряд, и без
    # него холодный кэш означал шесть параллельных запросов к Нацбанку.
    if time.time() - _cache_ts > _TTL:
        async with _lock:
            if time.time() - _cache_ts > _TTL:
                await _refresh()
    return _cache.get(cur, _FALLBACK_TO_BYN.get(cur, Decimal("1")))


async def factor(from_cur: str, to_cur: str) -> Decimal:
    """Множитель перевода без округления до копейки.

    convert() квантует результат до 0.01 — для суммы это правильно, а для
    коэффициента грубо: BYN→USD дало бы 0.32 вместо 0.3226 и увело бы
    пересчёт всего склада на полтора процента.
    """
    from_cur, to_cur = (from_cur or "BYN").upper(), (to_cur or "BYN").upper()
    if from_cur == to_cur:
        return Decimal("1")
    return await _rate_to_byn(from_cur) / await _rate_to_byn(to_cur)


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def convert(amount, from_cur: str, to_cur: str) -> Decimal:
    """Переводит сумму из from_cur в to_cur. amount может быть None/Decimal/число."""
    if amount is None:
        return Decimal("0")
    amt = Decimal(str(amount))
    from_cur = (from_cur or "BYN").upper()
    to_cur = (to_cur or "BYN").upper()
    if from_cur == to_cur:
        return _q(amt)
    in_byn = amt * await _rate_to_byn(from_cur)
    if to_cur == "BYN":
        return _q(in_byn)
    return _q(in_byn / await _rate_to_byn(to_cur))


async def rates_map(base: str = "BYN") -> dict[str, float]:
    """Курсы всех валют к base (сколько base стоит 1 единица валюты) — для фронта."""
    base = (base or "BYN").upper()
    out: dict[str, float] = {}
    for cur in SUPPORTED:
        out[cur] = float(await convert(1, cur, base))
    return out


def symbol(cur: str) -> str:
    return SYMBOLS.get((cur or "BYN").upper(), cur)
