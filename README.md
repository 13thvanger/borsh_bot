# Borsh Bot

Telegram-бот для групповых чатов: считает съеденные борщи по пользователям внутри каждого чата.

## Команды

- `/borsh` — добавить +1 борщ себе в текущем чате.
- `/stat` — топ-10 текущего чата.
- `/stat N` — топ-N текущего чата, например `/stat 5`.
- `/stat username` — подробная статистика пользователя, например `/stat ivan` или `/stat @ivan`.
- `/stat telegram_user_id` — подробная статистика по Telegram ID.
- `/username name` — задать локальный username для статистики.
- `/me` — показать свою статистику.
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
POSTGRES_USER=borsh
POSTGRES_PASSWORD=сложный_пароль
POSTGRES_DB=borshbot
DATABASE_URL=postgresql+asyncpg://borsh:сложный_пароль@db:5432/borshbot
```

Важно: внутри Docker Compose host базы данных — `db`, а не `localhost`.

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
