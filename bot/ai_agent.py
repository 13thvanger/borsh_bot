import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx
from aiogram import Bot

from bot.config import settings


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
Ты классификатор изображений для Telegram-бота учета борща.

Нужно определить, есть ли на фото борщ или близкое к борщу блюдо.

Считать борщом:
- украинский борщ;
- русский борщ;
- московский борщ;
- кубанский борщ;
- флотский борщ;
- постный борщ;
- борщ с фасолью;
- борщ с мясом;
- борщ со сметаной;
- горячий свекольник, если визуально похож на борщ.

Не считать борщом:
- солянку;
- харчо;
- рассольник;
- шурпу;
- лагман;
- уху;
- щи;
- минестроне;
- том-ям;
- фо;
- гуляш-суп;
- бозбаш;
- чорбу;
- томатный суп без капусты/свеклы;
- гороховый суп;
- сырный суп;
- крем-суп.

Признаки борща:
- свекла;
- бордовый, красный или красно-оранжевый цвет;
- капуста;
- картофель;
- морковь;
- мясо;
- фасоль;
- сметана;
- укроп/петрушка.

Признаки НЕ борща:
- лимон;
- оливки/маслины;
- колбаса/сосиски;
- копчености;
- рис;
- лапша;
- много кинзы;
- прозрачный бульон;
- кремовая текстура.

Финальный ответ должен быть строго одним символом:
1 — это борщ или очень близкое к борщу блюдо
0 — это не борщ

Не используй JSON.
Не добавляй объяснения.
""".strip()


async def telegram_photo_to_data_url(bot: Bot, file_id: str) -> str:
    tg_file = await bot.get_file(file_id)

    suffix = Path(tg_file.file_path or "image.jpg").suffix or ".jpg"

    with NamedTemporaryFile(suffix=suffix) as tmp:
        await bot.download_file(
            tg_file.file_path,
            destination=tmp.name,
        )
        data = Path(tmp.name).read_bytes()

    mime_type = mimetypes.types_map.get(
        suffix.lower(),
        "image/jpeg",
    )

    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


async def check_borsh_image(
    data_url: str,
) -> tuple[bool, float, str, str]:
    headers = {
        "Authorization": f"Bearer {settings.agent_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.agent_model,
        "temperature": 0,
        "max_tokens": 2000,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Определи, это борщ или нет. "
                            "В финале ответь только 1 или 0. "
                            "Особенно отличай борщ от солянки, харчо, шурпы, лагмана, рассольника, ухи, щей и томатного супа."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                        },
                    },
                ],
            },
        ],
    }

    async with httpx.AsyncClient(
        timeout=settings.agent_timeout_seconds,
    ) as client:
        response = await client.post(
            settings.agent_url,
            headers=headers,
            json=payload,
        )

        response.raise_for_status()
        data = response.json()

    logger.info("Agent response: %s", data)

    try:
        choices = data.get("choices") or []

        if not choices:
            return (
                False,
                0.0,
                "Агент не вернул choices",
                json.dumps(data, ensure_ascii=False),
            )

        choice = choices[0] or {}
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason")

        content = message.get("content")
        reasoning = message.get("reasoning")

        raw_text = str(content or reasoning or "").strip()

        if not raw_text:
            return (
                False,
                0.0,
                "Агент вернул пустой ответ",
                json.dumps(data, ensure_ascii=False),
            )

        is_borsh, confidence, reason = _classify_agent_text(
            raw_text,
            finish_reason=finish_reason,
        )

        return (
            is_borsh,
            confidence,
            reason,
            raw_text[:4000],
        )

    except Exception as e:
        logger.exception("Failed to parse agent response")

        return (
            False,
            0.0,
            f"Ошибка разбора ответа агента: {e}",
            json.dumps(data, ensure_ascii=False),
        )


def _classify_agent_text(
    text: str,
    finish_reason: str | None = None,
) -> tuple[bool, float, str]:
    cleaned = text.strip()
    upper = cleaned.upper()
    lower = cleaned.lower()

    parsed = _try_parse_json_object(cleaned)
    if parsed:
        is_borsh = bool(parsed.get("is_borsh"))
        confidence = _safe_float(
            parsed.get("confidence"),
            default=0.9 if is_borsh else 0.1,
        )
        reason = str(parsed.get("reason") or "")
        score, score_reason = _score_borsh_text(lower)
        if score >= 3:
            return True, max(confidence, 0.8), f"Борщевой скоринг: {score}. {score_reason}"
        if score <= -3:
            return False, max(confidence, 0.8), f"Борщевой скоринг: {score}. {score_reason}"
        return is_borsh, confidence, reason or "Агент вернул JSON"

    # Сначала считаем скоринг по reasoning/content.
    # Это важнее, чем голый ответ 1/0, потому что агент иногда сам в reasoning видит солянку,
    # но финально ошибается.
    score, score_reason = _score_borsh_text(lower)

    if score >= 3:
        confidence = _confidence_from_score(score)
        return True, confidence, f"Борщевой скоринг: {score}. {score_reason}"

    if score <= -3:
        confidence = _confidence_from_score(score)
        return False, confidence, f"Борщевой скоринг: {score}. {score_reason}"

    # Если скоринг не дал уверенного решения, смотрим финальный короткий ответ агента.
    if cleaned.startswith("1"):
        return True, 0.7, "Агент подтвердил борщ, скоринг не противоречит"
    if cleaned.startswith("0"):
        return False, 0.7, "Агент не подтвердил борщ, скоринг не противоречит"

    if "NOT_BORSH" in upper or "NOT BORSH" in upper:
        return False, 0.75, "Агент ответил NOT_BORSH"

    if "BORSH" in upper:
        return True, 0.75, "Агент ответил BORSH"

    confidence = _extract_confidence(cleaned)

    return (
        False,
        confidence,
        f"Агент не дал однозначного подтверждения борща. Борщевой скоринг: {score}. {score_reason}",
    )


def _score_borsh_text(lower_text: str) -> tuple[int, str]:
    score = 0
    hits: list[str] = []

    positive_rules = [
        (3, "явно борщ", [
            "borscht",
            "borsh",
            "борщ",
            "ukrainian borscht",
            "russian borscht",
            "classic borscht",
            "beet borscht",
            "украинский борщ",
            "русский борщ",
            "московский борщ",
            "кубанский борщ",
            "флотский борщ",
            "постный борщ",
            "борщ с фасолью",
            "борщ с мясом",
        ]),
        (3, "свекольная основа", [
            "beet",
            "beetroot",
            "beet-based",
            "свекла",
            "свёкла",
            "свеколь",
            "свёколь",
            "свекольник",
            "beet soup",
        ]),
        (2, "капуста", [
            "cabbage",
            "shredded cabbage",
            "капуста",
            "капуст",
        ]),
        (2, "сметана", [
            "sour cream",
            "smetana",
            "сметана",
            "сметан",
        ]),
        (1, "красный цвет", [
            "red soup",
            "red broth",
            "reddish",
            "reddish-orange",
            "orange-red",
            "deep red",
            "burgundy",
            "красный суп",
            "красный бульон",
            "красно-оранж",
            "оранжево-крас",
            "бордов",
        ]),
        (1, "картофель", [
            "potato",
            "potatoes",
            "картофель",
            "картошка",
            "картоф",
        ]),
        (1, "морковь", [
            "carrot",
            "carrots",
            "морковь",
            "морков",
        ]),
        (1, "мясо", [
            "meat",
            "beef",
            "pork",
            "bone-in meat",
            "мясо",
            "говядин",
            "свинин",
            "ребр",
            "ребро",
        ]),
        (1, "фасоль", [
            "bean",
            "beans",
            "фасоль",
            "фасол",
        ]),
        (1, "зелень борщевая", [
            "dill",
            "parsley",
            "укроп",
            "петруш",
        ]),
    ]

    negative_rules = [
        (-5, "солянка", [
            "solyanka",
            "солянка",
            "meat solyanka",
            "mixed meat soup",
        ]),
        (-5, "лимон", [
            "lemon",
            "лимон",
        ]),
        (-5, "оливки/маслины", [
            "olive",
            "olives",
            "оливк",
            "маслин",
        ]),
        (-5, "колбаса/сосиски", [
            "sausage",
            "sausages",
            "hot dog",
            "hot dogs",
            "колбас",
            "сосиск",
            "сардель",
        ]),
        (-4, "копчености", [
            "smoked meat",
            "smoked meats",
            "smoked sausage",
            "копчен",
            "копчён",
        ]),
        (-3, "харчо", [
            "kharcho",
            "харчо",
        ]),
        (-3, "шурпа", [
            "shurpa",
            "шурпа",
        ]),
        (-3, "лагман/лапша", [
            "lagman",
            "лагман",
            "noodle",
            "noodles",
            "лапша",
        ]),
        (-3, "рассольник", [
            "rassolnik",
            "pickle soup",
            "рассольник",
            "соленые огурцы",
            "солёные огурцы",
            "pickles",
        ]),
        (-3, "уха", [
            "ukha",
            "fish soup",
            "уха",
            "рыбный суп",
        ]),
        (-3, "щи", [
            "shchi",
            "щи",
            "cabbage soup",
        ]),
        (-3, "томатный суп без борщевых признаков", [
            "tomato soup",
            "томатный суп",
        ]),
        (-3, "минестроне", [
            "minestrone",
            "минестроне",
        ]),
        (-3, "том-ям", [
            "tom yum",
            "tom-yum",
            "том ям",
            "том-ям",
        ]),
        (-3, "фо", [
            "pho",
            "фо бо",
            "фо-бо",
        ]),
        (-3, "гуляш-суп", [
            "goulash soup",
            "гуляш-суп",
            "суп-гуляш",
        ]),
        (-3, "бозбаш/чорба", [
            "bozbash",
            "бозбаш",
            "chorba",
            "чорба",
        ]),
        (-2, "рис", [
            "rice",
            "рис",
        ]),
        (-2, "кинза", [
            "cilantro",
            "coriander",
            "кинза",
        ]),
        (-2, "прозрачный бульон", [
            "clear broth",
            "transparent broth",
            "прозрачный бульон",
        ]),
        (-2, "крем-суп", [
            "cream soup",
            "creamy",
            "pureed",
            "purée",
            "крем-суп",
            "пюре",
        ]),
    ]

    for points, label, markers in positive_rules:
        if any(marker in lower_text for marker in markers):
            score += points
            hits.append(f"+{points} {label}")

    for points, label, markers in negative_rules:
        if any(marker in lower_text for marker in markers):
            score += points
            hits.append(f"{points} {label}")

    # Комбинационные правила.
    has_red = _contains_any(lower_text, [
        "red",
        "reddish",
        "reddish-orange",
        "orange-red",
        "deep red",
        "burgundy",
        "красный",
        "красно-оранж",
        "оранжево-крас",
        "бордов",
    ])
    has_cabbage = _contains_any(lower_text, [
        "cabbage",
        "капуста",
        "капуст",
    ])
    has_potato_or_meat_or_carrot = _contains_any(lower_text, [
        "potato",
        "potatoes",
        "картофель",
        "картошка",
        "meat",
        "beef",
        "pork",
        "мясо",
        "говядин",
        "свинин",
        "carrot",
        "carrots",
        "морковь",
        "морков",
    ])
    has_strong_solyanka = _contains_any(lower_text, [
        "lemon",
        "лимон",
        "olive",
        "olives",
        "оливк",
        "маслин",
        "sausage",
        "sausages",
        "колбас",
        "сосиск",
        "solyanka",
        "солянка",
    ])

    if has_red and has_cabbage and has_potato_or_meat_or_carrot and not has_strong_solyanka:
        score += 2
        hits.append("+2 комбинация: красный суп + капуста + картофель/мясо/морковь")

    # Просто томатный суп без свеклы/капусты не должен становиться борщом.
    has_tomato_soup = _contains_any(lower_text, [
        "tomato soup",
        "томатный суп",
    ])
    has_beet_or_cabbage = _contains_any(lower_text, [
        "beet",
        "beetroot",
        "свекла",
        "свёкла",
        "свеколь",
        "свёколь",
        "cabbage",
        "капуста",
        "капуст",
    ])

    if has_tomato_soup and not has_beet_or_cabbage:
        score -= 3
        hits.append("-3 томатный суп без свеклы/капусты")

    reason = ", ".join(hits) if hits else "нет явных маркеров"
    return score, reason


def _contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def _confidence_from_score(score: int) -> float:
    abs_score = abs(score)

    if abs_score >= 8:
        return 0.95
    if abs_score >= 5:
        return 0.9
    if abs_score >= 3:
        return 0.8

    return 0.6


def _try_parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()

    if not cleaned:
        return None

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start >= 0 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None

    return None


def _extract_confidence(text: str) -> float:
    match = re.search(
        r"""confidence["'`]?\s*[:=]\s*([01](?:\.\d+)?)""",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return 0.0

    return _safe_float(match.group(1), default=0.0)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
