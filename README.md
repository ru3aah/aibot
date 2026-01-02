# AI Telegram News Bot (Project M4)

Автоматизированный сервис для ведения Telegram-канала на основе новостей с сайтов и публичных Telegram-источников с использованием AI-генерации постов.

Проект реализует полный конвейер:
**парсинг → фильтрация → AI-генерация (с переводом) → публикация в Telegram**  
с возможностью ручного управления и мониторинга через API и Telegram-бота администратора.

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

