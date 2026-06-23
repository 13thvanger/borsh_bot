import base64
import json
import logging
import mimetypes
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
from aiogram import Bot

from bot.config import settings


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
Ты проверяешь фото для шуточного Telegram-бота учета борща.
Задача: определить, есть ли на изображении именно борщ или блюдо, визуально похожее на борщ.
Борщ обычно выглядит как красный/свекольный суп, часто со сметаной, зеленью, мясом, капустой.

Ответь строго валидным JSON без markdown:

{"is_borsh": true/false, "confidence": 0.0-1.0, "reason": "короткое объяснение"}

Если не уверен, ставь is_borsh=false.
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
        "max_tokens": 300,
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
                            "Проверь фото. "
                            "Это борщ? "
                            "Ответь только JSON."
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
        choices = data.get("choices")

        if not choices:
            return (
                False,
                0.0,
                "Агент не вернул choices",
                json.dumps(data, ensure_ascii=False),
            )

        message = choices[0].get("message", {})

        content = message.get("content")

        if content is None:
            return (
                False,
                0.0,
                "Агент вернул пустой ответ",
                json.dumps(data, ensure_ascii=False),
            )

        content = str(content)

        parsed = _parse_json_object(content)

        is_borsh = bool(parsed.get("is_borsh"))

        confidence = float(
            parsed.get("confidence") or 0
        )

        reason = str(
            parsed.get("reason") or ""
        )[:1000]

        return (
            is_borsh,
            confidence,
            reason,
            content,
        )

    except Exception as e:
        logger.exception(
            "Failed to parse agent response"
        )

        return (
            False,
            0.0,
            f"Ошибка разбора ответа агента: {e}",
            json.dumps(data, ensure_ascii=False),
        )


def _parse_json_object(text: str | None) -> dict:

    if not text:
        return {}

    cleaned = str(text).strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()

        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        return json.loads(cleaned)

    except json.JSONDecodeError:

        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start >= 0 and end > start:
            return json.loads(
                cleaned[start : end + 1]
            )

        raise