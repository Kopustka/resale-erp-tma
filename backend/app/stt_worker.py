"""Отдельный процесс распознавания речи.

Запускается на одну запись и умирает. Так сделано намеренно: модель small
занимает около 1.4 ГБ, а на сервере всего 2 ГБ свободных и почти занятая
подкачка. Держи мы её внутри API — процесс раздулся бы навсегда и рано или
поздно утянул бы прод. Здесь пик памяти живёт секунды и полностью
освобождается.

Использование: python -m app.stt_worker <файл> [модель]
Вывод: одна строка JSON {"text": "...", "language": "ru"} в stdout.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "нет пути к файлу"}), flush=True)
        return 2

    path = sys.argv[1]
    model_size = sys.argv[2] if len(sys.argv) > 2 else "small"

    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=2,
        )
        segments, info = model.transcribe(
            path,
            language="ru",
            # beam_size=1 быстрее и на коротких фразах почти не хуже.
            beam_size=1,
            # Отсекает тишину и шум: без этого модель дописывает
            # выдуманные фразы в пустых участках.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        print(
            json.dumps(
                {"text": text, "language": info.language, "prob": info.language_probability},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)[:300]}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
