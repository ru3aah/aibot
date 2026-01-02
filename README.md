# AI Telegram News Bot (Project M4)

Автоматизированный сервис для ведения Telegram-канала на основе новостей с 
сайтов и публичных Telegram-источников с использованием AI-генерации постов. 

Проект реализует полный конвейер:
**парсинг → фильтрация → AI-генерация (с переводом) → публикация в Telegram**  
с возможностью ручного управления и мониторинга через API и Telegram-бота 
администратора. 

---

## 📌 Основные возможности

- Автоматический сбор новостей с:
  - новостных сайтов (RSS / HTML)
  - публичных Telegram-каналов (Telethon)
- Фоновая обработка через **Celery + Redis**
- Генерация постов через **OpenAI API**
- Перевод итогового поста на выбранный язык (`ru / en / es / de`)
- Защита от дублей и повторных публикаций
- Автопубликация в Telegram-канал
- REST API для управления источниками и фильтрами
- Telegram admin-бот (aiogram 3) для управления без API
- Swagger-документация

---

## 🧠 Архитектура проекта
┌──────────────┐
│ Celery Beat  │  (PARSE_INTERVAL_MINUTES)
└──────┬───────┘
▼
┌──────────────┐
│ parse_news   │  ← сайты + Telegram
└──────┬───────┘
▼
┌────────────────────┐
│ generate_chain_post│  ← фильтры + AI + перевод
└──────┬─────────────┘
▼
┌────────────────────┐
│ publish_latest_post│  ← Telethon
└────────────────────┘
## структура проекта

/aibot/
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── api/
│   │   ├── endpoints.py         # REST API
│   │   └── schemas.py
│   ├── news_parser/
│   │   ├── sites.py             # Парсеры сайтов
│   │   └── telegram.py          # Парсер Telegram (Telethon)
│   ├── ai/
│   │   ├── openai_client.py     # OpenAI HTTP client
│   │   └── generator.py         # Генерация + перевод
│   ├── telegram/
│   │   ├── bot.py               # Admin Telegram bot (aiogram)
│   │   └── publisher.py         # Публикация через Telethon
│   ├── database/
│   │   ├── db.py
│   │   └── models.py
│   ├── tasks.py                 # Celery tasks
│   ├── config.py                # Settings (.env)
│   └── utils.py                 # Общие утилиты
├── celery_worker.py              # Celery + Beat
├── requirements.txt
├── README.md
└── .env

---

## 🗄️ Модели данных

### NewsItem
- id (UUID)
- title
- url
- summary
- raw_text
- text100
- source_id
- published_at
- created_at

---

### Post
- id
- generated_text
- status (`GENERATED / PUBLISHED / RETRYABLE`)
- published_at
- telegram_message_id
- input_news_ids (JSON)
- input_key (сигнатура фильтра)

---

### Source
- id
- type (`site / tg`)
- name
- url
- enabled

---

### FilterSettings
- language (ru / en / es / de)
- active_keywords_json
- updated_at

---

## 🌍 Логика генерации и перевода

- Генерация выполняется через OpenAI
- В `system prompt` явно указано:
  - итоговый текст **обязан быть на выбранном языке**
  - если входные новости на другом языке — выполняется **перевод**
- При ошибке OpenAI:
- используется fallback-дайджест
- пайплайн не прерывается
- статус поста остаётся `GENERATED`

---

## ⏱️ Планировщик

Период запуска всей цепочки определяется **только** параметром:

PARSE_INTERVAL_MINUTES 

## запуск Celery worker
uv run celery -A celery_worker.celery_app worker -l INFO -Q aibot -P solo
## запуск Celery Beat
uv run celery -A celery_worker.celery_app beat -l INFO
## запуск FastAPI
uv run python -m app.main
## Swagger UI
http://localhost:8000/docs
## запуск telegramm bot admin
uv run python -m app.telegram.bot

🧪 Ручное управление

Через REST API:
	•	/api/tasks/parse
	•	/api/tasks/generate
	•	/api/tasks/publish

Через Telegram-бот:
	•	кнопки Parse / Generate / Publish
	•	управление источниками
	•	управление ключевыми словами
	•	выбор языка генерации

🐳 Запуск в Docker (Docker Compose)

Проект поднимается одним  docker compose up -d --build  и запускает все 
компоненты: 
	•	api — FastAPI + автосоздание схемы БД (таблицы)
	•	worker — Celery worker (парсинг/генерация/публикация)
	•	beat — Celery Beat (плановый запуск пайплайна)
	•	postgres — база данных Postgres
	•	redis — брокер задач Celery
	•	bot — админ-бот (aiogram 3) для управления источниками/ключевыми 
        словами/языком и ручного запуска задач 

Проверить запуск контейнеров: 
docker compose ps

Посмотреть логи контейнеров: 
docker compose logs -f

✅ Проверка работоспособности

A) Проверка API и Swagger

Swagger должен открываться:
	•	http://localhost:8000/docs

Проверка “жив ли API” (зависит от того, есть ли health endpoint; если нет — 
просто открывай /docs): 
curl -s http://localhost:8000/docs >/dev/null && echo "API OK"

Логи API:
docker compose logs -n 200 api

B) Проверка базы Postgres и таблиц
Показать таблицы:
docker compose exec -T postgres sh -lc 'PGPASSWORD=postgres psql -U postgres 
-d aibot -c "\dt"' 

Проверить, что источники есть (после сидинга):
docker compose exec -T postgres sh -lc 'PGPASSWORD=postgres psql -U postgres 
-d aibot -c "select count(*) as sources_total from sources;"' 

C) Проверка Celery worker / beat
Логи worker:
docker compose logs -n 200 worker

Логи beat:
docker compose logs -n 200 beat

D) Проверка админ-бота (aiogram)
токен бота можно получить через @BotFather
Проверить
docker compose exec -T bot sh -lc 'python -c "import os; print(os.getenv
(\"TG_BOT_TOKEN\") or os.getenv(\"TELEGRAM_BOT_TOKEN\") or os.getenv
(\"BOT_TOKEN\"))"'  

Логи бота:
docker compose logs -n 200 bot

🧩 Автосоздание таблиц и сидинг источников
При старте FastAPI выполняется:
	1.	создание таблиц (Base.metadata.create_all)
	2.	сидинг источников если таблица sources пустая (из seed_sources.json)

Это нужно для “первого запуска”, чтобы пайплайн сразу мог парсить.

🧪 Ручной запуск пайплайна (в Docker)

Через REST API

Запуск задач:
curl -s -X POST http://localhost:8000/api/tasks/parse
curl -s -X POST http://localhost:8000/api/tasks/generate
curl -s -X POST http://localhost:8000/api/tasks/publish

Проверить, что новости появились:
docker compose exec -T postgres sh -lc 'PGPASSWORD=postgres psql -U postgres 
-d aibot -c "select count(*) as news_items_total from news_items;"' 

Проверить, что посты создаются/публикуются:
docker compose exec -T postgres sh -lc 'PGPASSWORD=postgres psql -U postgres 
-d aibot -c "select id, status, telegram_message_id, published_at from posts 
order by created_at desc limit 10;"'  





