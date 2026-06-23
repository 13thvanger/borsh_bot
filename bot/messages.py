import random
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MESSAGES_DIR = BASE_DIR / "messages"

DEFAULT_MESSAGES = {
    "confirmed.txt": [
        "🍲 Борщ опознан. Свекольный след подтвержден. +1 к борщевой карме!",
        "✅ Это борщ. Комиссия по красным супам одобряет. +1!",
        "🕵️ ИИ нашел борщ. Маскировка под тарелку не помогла. +1!",
    ],
    "rejected.txt": [
        "🥄 ИИ посмотрел фото и сказал: борщевой ауры недостаточно. +1 не добавлен.",
        "🚫 Борщ не подтвержден. Возможно, это суп под прикрытием.",
        "🤖 На фото не обнаружен борщ. Свекольный протокол не пройден.",
    ],
    "bypass.txt": [
        "🍲 ИИ-инспектор сегодня выходной, поэтому верим на слово. +1 борщ!",
        "🫡 Проверка фото отключена. Борщ засчитан по джентльменскому борщевому соглашению. +1!",
        "🥣 Режим доверия активен: сказал борщ — значит борщ. +1!",
    ],
    "no_photo.txt": [
        "📸 Сначала пришли фото борща, потом жми /borsh. Без улик борщевой суд бессилен.",
        "🥄 Фото не найдено. Борщ без фотодоказательств ушел в легенды.",
    ],
    "already_counted.txt": [
        "♻️ Этот борщ уже был в протоколе. Повторная свекольная магия запрещена.",
        "🧾 Фото уже засчитано. Один борщ — один плюсик.",
    ],
    "agent_error.txt": [
        "🤖 ИИ-инспектор споткнулся о половник и не смог проверить фото.",
        "⚠️ Борщевой анализатор временно не отвечает. Попробуй позже.",
    ],
}


def load_messages(filename: str) -> list[str]:
    path = MESSAGES_DIR / filename
    if not path.exists():
        return DEFAULT_MESSAGES.get(filename, ["🍲 Борщ!"])
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    return lines or DEFAULT_MESSAGES.get(filename, ["🍲 Борщ!"])


def random_message(filename: str, **kwargs) -> str:
    template = random.choice(load_messages(filename))
    try:
        return template.format(**kwargs)
    except Exception:
        return template
