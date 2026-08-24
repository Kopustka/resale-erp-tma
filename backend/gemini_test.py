"""Обращение к Gemini: повторы, переход на запасную модель, бюджет времени.

Ответы подменяются — тест не ходит в сеть и не зависит от того, перегружена
ли модель прямо сейчас. Проверяется ровно то, из-за чего пользователь ловил
504: перегруженная модель не должна съедать всё время до таймаута прокси.
"""
import asyncio, sys, time
from types import SimpleNamespace

from app.services import gemini

ok = bad = 0


def chk(c, n, e=""):
    global ok, bad
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"\n       {e}"))
    if c:
        ok += 1
    else:
        bad += 1


class FakeResponse:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("не JSON")
        return self._payload


def good(obj: str = '{"title":"ok"}'):
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": obj}]}}]})


def error(status: int, message: str):
    return FakeResponse(status, {"error": {"message": message}}, text=message)


class Recorder:
    """Подменяет _post и запоминает, к каким моделям обращались."""

    def __init__(self, plan: dict):
        self.plan = plan          # модель -> список ответов по порядку
        self.calls: list[str] = []

    async def __call__(self, model, body, timeout):
        self.calls.append(model)
        seq = self.plan.get(model, [])
        idx = min(self.calls.count(model) - 1, len(seq) - 1)
        if not seq:
            return error(404, "нет такой модели")
        return seq[idx]


async def main():
    global ok, bad
    real_post = gemini._post
    real_models = gemini._models
    PRIMARY, FALLBACK = "primary-model", "fallback-model"
    gemini._models = lambda: [m for m in (PRIMARY, FALLBACK) if not gemini._is_out(m)] or [PRIMARY]

    try:
        print("\n[1] Текст ошибки достаётся из ответа Google")
        chk(gemini._error_text(error(503, "This model is currently experiencing high demand."))
            == "This model is currently experiencing high demand.",
            "сообщение вытащено")
        chk(gemini._error_text(FakeResponse(400, None, text="bad request")) == "bad request",
            "не-JSON тело не теряется")

        print("\n[2] Перегруженная основная — уходим на запасную")
        gemini._quota_out.clear()
        rec = Recorder({PRIMARY: [error(503, "high demand")], FALLBACK: [good()]})
        gemini._post = rec
        t = time.monotonic()
        res = await gemini.call_json([{"text": "x"}], attempts=3, budget=20)
        dt = time.monotonic() - t
        chk(res == {"title": "ok"}, "ответ получен от запасной", str(res))
        chk(rec.calls.count(PRIMARY) == 3, "основную пробовали трижды", str(rec.calls))
        chk(rec.calls[-1] == FALLBACK, "последней спрашивали запасную", str(rec.calls))

        print("\n[3] Занятая модель запоминается — второй запрос не тратит на неё время")
        chk(gemini._is_out(PRIMARY), "основная помечена занятой")
        rec2 = Recorder({PRIMARY: [error(503, "high demand")], FALLBACK: [good()]})
        gemini._post = rec2
        t = time.monotonic()
        res2 = await gemini.call_json([{"text": "x"}], attempts=3, budget=20)
        dt2 = time.monotonic() - t
        chk(res2 == {"title": "ok"}, "ответ снова получен")
        chk(PRIMARY not in rec2.calls, "к занятой модели не обращались вовсе", str(rec2.calls))
        chk(dt2 < 1, "и это быстро", f"{dt2:.2f}с против {dt:.2f}с в первый раз")

        print("\n[4] Метка временная, а не навсегда")
        chk(gemini.BUSY_MEMO_SECONDS < gemini.QUOTA_MEMO_SECONDS,
            "перегрузка забывается быстрее исчерпанной квоты",
            f"{gemini.BUSY_MEMO_SECONDS} vs {gemini.QUOTA_MEMO_SECONDS}")
        gemini._quota_out[PRIMARY] = time.monotonic() - 1  # срок истёк
        chk(not gemini._is_out(PRIMARY), "по истечении срока модель снова в игре")

        print("\n[5] Ошибка запроса не повторяется")
        gemini._quota_out.clear()
        rec3 = Recorder({PRIMARY: [error(400, "Invalid image data")], FALLBACK: [good()]})
        gemini._post = rec3
        res3 = await gemini.call_json([{"text": "x"}], attempts=3, budget=20)
        chk(rec3.calls.count(PRIMARY) == 1, "400 пробовали один раз, без повторов",
            str(rec3.calls))
        chk(res3 == {"title": "ok"}, "и всё равно ответили с запасной")
        chk(not gemini._is_out(PRIMARY), "модель не помечена занятой из-за 400")

        print("\n[6] Исчерпанная квота — сразу к запасной")
        gemini._quota_out.clear()
        rec4 = Recorder({PRIMARY: [error(429, "quota")], FALLBACK: [good()]})
        gemini._post = rec4
        await gemini.call_json([{"text": "x"}], attempts=3, budget=20)
        chk(rec4.calls.count(PRIMARY) == 1, "по квоте не повторяем", str(rec4.calls))
        chk(gemini._is_out(PRIMARY), "модель отложена надолго")

        print("\n[7] Обе недоступны — понятная ошибка, а не зависание")
        gemini._quota_out.clear()
        rec5 = Recorder({PRIMARY: [error(503, "high demand")],
                         FALLBACK: [error(503, "high demand")]})
        gemini._post = rec5
        t = time.monotonic()
        try:
            await gemini.call_json([{"text": "x"}], attempts=2, budget=6)
            chk(False, "должно было бросить исключение")
        except gemini.GeminiUnavailable as e:
            spent = time.monotonic() - t
            chk("503" in str(e), "в тексте виден код", str(e))
            chk("high demand" in str(e), "и причина от Google", str(e))
            chk(spent <= 7, "уложились в бюджет", f"{spent:.1f}с при бюджете 6с")

        print("\n[8] Бюджет описания меньше таймаута прокси")
        from app.services.ai_describe import DESCRIBE_BUDGET
        chk(DESCRIBE_BUDGET <= 30, "бюджет не превышает прежний лимит nginx (30 c)",
            str(DESCRIBE_BUDGET))
        nginx = open("/etc/nginx/sites-available/shmotkamanadjer.duckdns.org",
                     encoding="utf-8").read()
        import re
        api_block = nginx.split("location /api/ {")[1].split("}")[0]
        m = re.search(r"proxy_read_timeout (\d+)s", api_block)
        chk(m is not None and int(m.group(1)) > DESCRIBE_BUDGET,
            "у прокси есть запас над бюджетом",
            f"nginx={m.group(1) if m else '?'}с, бюджет={DESCRIBE_BUDGET}с")
    finally:
        gemini._post = real_post
        gemini._models = real_models
        gemini._quota_out.clear()

    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)


asyncio.run(main())
