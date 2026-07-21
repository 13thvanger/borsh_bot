# Borsh Bot

Telegram-бот для групповых чатов: считает съеденные борщи по пользователям внутри каждого чата.

## Команды

- `/borsh` — добавить +1 борщ себе в текущем чате по последнему свежему фото.
- `/borsh` в ответ на свое фото — проверить именно это фото.
- `/stat` — топ-10 текущего чата.
- `/stat N` — топ-N текущего чата, например `/stat 5`.
- `/stat username` — подробная статистика пользователя, например `/stat ivan` или `/stat @ivan`.
- `/stat telegram_user_id` — подробная статистика по Telegram ID.
- `/username name` — задать локальный username для статистики.
- `/me` — показать свою статистику.
- `/addborsh N user` — админская команда: добавить N борщей пользователю по Telegram ID или username.
- `/help` — справка.

## Запуск

1. Создайте бота через BotFather и получите токен.
2. Скопируйте env-файл:

```bash
cp .env.example .env
```

3. Отредактируйте `.env`:

```env
BOT_TOKEN=ваш_токен_бота
BOT_ADMIN_IDS=123456789,987654321
MANUAL_ADD_BORSH_MAX=1000
POSTGRES_USER=borsh
POSTGRES_PASSWORD=сложный_пароль
POSTGRES_DB=borshbot
DATABASE_URL=postgresql+asyncpg://borsh:сложный_пароль@db:5432/borshbot
```

Важно: внутри Docker Compose host базы данных — `db`, а не `localhost`.

`BOT_ADMIN_IDS` — Telegram ID админов бота через запятую, пробел или `;`. Только эти пользователи могут выполнять `/addborsh`.

4. Запустите:

```bash
docker compose up -d --build
```

5. Посмотрите логи:

```bash
docker compose logs -f bot
```

6. Добавьте бота в групповой чат и отправьте:

```text
/borsh
/stat
```

## Обновление

```bash
git pull
docker compose up -d --build
```

## Остановка

```bash
docker compose down
```

С удалением базы:

```bash
docker compose down -v
```

## Проверка борща по фото через ИИ-агента

Начиная с этой версии команда `/borsh` работает так:

1. Пользователь отправляет фото в групповой чат.
2. Пользователь пишет `/borsh`.
3. Бот берет последнее свежее фото этого же пользователя в этом же чате, скачивает фото из Telegram, передает его ИИ-агенту как `data:image/...;base64,...`.
4. Если агент возвращает `is_borsh=true`, бот добавляет +1 борщ.
5. Одно и то же фото не засчитывается повторно: подтверждение фиксируется в таблице `borsh_proofs` с привязкой к `photo_messages.telegram_message_id` и времени сообщения.

Можно ответить командой `/borsh` на конкретное свое фото. Тогда бот проверит именно его, даже если после него были другие снимки.

В `.env` должны быть добавлены параметры:

```env
AGENT_URL=https://bridge-back.admlr.lipetsk.ru/api/v1/chat/completions
AGENT_API_KEY=CHANGE_ME
AGENT_MODEL=cifra48/agent
AGENT_TIMEOUT_SECONDS=60
AGENT_TIMEOUT_BYPASS=true
BORSH_PHOTO_WINDOW_MINUTES=15
```

Если агент не ответил за `AGENT_TIMEOUT_SECONDS` и `AGENT_TIMEOUT_BYPASS=true`, бот засчитает борщ в режиме доверия и сохранит результат для выбранного фото, чтобы его нельзя было засчитать повторно. Чтобы при таймауте не засчитывать борщ, установите:

```env
AGENT_TIMEOUT_BYPASS=false
```

После обновления проекта на сервере:

```bash
git pull
docker compose up -d --build
docker compose logs -f bot
```

Новые таблицы создаются автоматически при запуске приложения через `Base.metadata.create_all()`:

- `photo_messages` — последние фото пользователей, которые бот видел в чате;
- `borsh_proofs` — результаты проверки фото ИИ-агентом.

Важно: Telegram Bot API не дает боту произвольно читать историю чата. Поэтому бот может проверить только те фото, которые он получил после запуска и после добавления бота в чат.

## Режим без ИИ-проверки

Проверка фото включается только если в `.env` одновременно заданы:

```env
AGENT_URL=https://bridge-back.admlr.lipetsk.ru/api/v1/chat/completions
AGENT_API_KEY=...
```

Если `AGENT_URL` или `AGENT_API_KEY` отсутствуют или пустые, бот запускается без ИИ-проверки. При `AGENT_REQUIRED=false` команда `/borsh` сразу добавляет +1 борщ без фото.

```env
AGENT_REQUIRED=false
```

Если нужна строгая проверка и нельзя засчитывать борщ без агента:

```env
AGENT_REQUIRED=true
```

В этом режиме, если агент не настроен, `/borsh` не добавит борщ.

Проверить режим можно командой:

```text
/health
```

## Смешные сообщения

Фразы лежат в папке `messages/`. Бот случайно выбирает строку из нужного файла.

- `messages/confirmed.txt` — когда ИИ подтвердил борщ;
- `messages/rejected.txt` — когда ИИ не подтвердил борщ;
- `messages/bypass.txt` — когда ИИ-проверка отключена и борщ засчитан без фото;
- `messages/agent_timeout_bypass.txt` — когда ИИ-агент ушел в таймаут, но борщ засчитан в режиме доверия;
- `messages/no_photo.txt` — когда фото не найдено;
- `messages/already_counted.txt` — когда фото уже засчитано ранее;
- `messages/agent_error.txt` — когда агент вернул ошибку.

Можно редактировать эти файлы без изменения кода. После изменения фраз в Docker-версии нужно пересобрать контейнер:

```bash
docker compose up -d --build
```

Поддерживаемые переменные в текстах:

- `{total}` — текущее число борщей пользователя в чате;
- `{confidence}` — уверенность ИИ;
- `{reason}` — короткое объяснение ИИ;
- `{error}` — текст ошибки агента.
