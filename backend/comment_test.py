"""Тест автоответов в комментариях (чистые функции, без БД и сети)."""
import sys
from app.services.comment_reply import answer, detect_topics

ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

ITEM={"length_cm":72,"width_cm":58,"sleeve_cm":65,"size":"L","price":150,
      "currency":"Br","condition":"8/10","status":"LISTED"}
SOLD={**ITEM,"status":"SOLD"}
BARE={"status":"LISTED"}   # ничего не заполнено

print("\n[1] Распознавание вопросов")
for q, topic in [
    ("какие замеры?","measurements"), ("длина какая","measurements"),
    ("по плечам сколько см","measurements"), ("ПОГ?","measurements"),
    ("какой размер","size"), ("сядет на 48?","size"),
    ("сколько стоит","price"), ("почём","price"), ("цена?","price"), ("торг есть?","price"),
    ("состояние какое","condition"), ("дефекты есть?","condition"),
    ("ещё актуально?","available"), ("продано?","available"), ("беру","available"),
]:
    got = detect_topics(q)
    chk(topic in got, f"«{q}» -> {topic}", f"получили {got}")

print("\n[2] Молчим, когда нечего сказать")
chk(detect_topics("привет всем")==[], "болтовня без вопроса")
chk(detect_topics("")==[], "пустое сообщение")
chk(detect_topics("а"*400)==[], "простыня игнорируется")
chk(answer(BARE,"какие замеры?") is None, "нет замеров в карточке -> молчим")
chk(answer(BARE,"какой размер?") is None, "нет размера -> молчим")

print("\n[3] Ответы")
r=answer(ITEM,"какие замеры?")
chk(r=="📐 Длина 72 см · Ширина 58 см · Рукав 65 см", "замеры", repr(r))
r=answer(ITEM,"цена и размер?")
chk("Размер: L" in r and "150 Br" in r, "два вопроса в одном — оба ответа", repr(r))
r=answer(ITEM,"ещё актуально?")
chk(r=="✅ Да, вещь актуальна.", "актуальность", repr(r))
r=answer(SOLD,"ещё актуально?")
chk(r=="❌ Уже продано.", "проданное честно помечаем", repr(r))
r=answer(ITEM,"состояние?")
chk(r=="✨ Состояние: 8/10", "состояние", repr(r))
r=answer(BARE,"актуально?")
chk(r=="✅ Да, вещь актуальна.", "актуальность отвечается и без заполненных полей", repr(r))

print(f"\nИТОГ: {ok} успешно, {bad} провалено")
sys.exit(1 if bad else 0)
