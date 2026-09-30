# KLIPANI

Локальное веб-приложение для создания вертикальных клипов из записей Twitch.

Сейчас реализованы этапы 0–1: загрузка → `ffprobe` → optional Whisper → Mock/Ollama моменты → ranking/dedup/audio score → правка границ → FFmpeg vertical render → просмотр и скачивание MP4.

## Требования

- macOS с Apple Silicon;
- Homebrew;
- Node.js 20+;
- Python 3.9+ (рекомендуется 3.11+);
- FFmpeg;
- (опционально) [Ollama](https://ollama.com) с моделью из `.env` (по умолчанию `qwen3:1.7b`).

Установите FFmpeg (нужен также `ffprobe`):

```bash
brew install ffmpeg
```

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

cd frontend
npm install
cd ..

cp .env.example .env
```

В `.env` можно выбрать:

- `WHISPER_MODEL=base|small` — размер модели распознавания;
- `LLM_PROVIDER=mock|ollama` — источник моментов (Ollama только если сервис и модель доступны).

## Запуск

```bash
./start.sh
```

Откройте [http://localhost:3000](http://localhost:3000). Backend: [http://localhost:8000/api/health](http://localhost:8000/api/health).

## Как пользоваться

1. Перетащите MP4, MKV, MOV или WEBM.
2. Нажмите «Начать анализ».
   - Если Whisper установлен — сначала транскрипция, затем отбор моментов.
   - Если Ollama недоступна — mock по транскрипту или по длительности.
3. При необходимости поправьте старт/конец момента (секунды) и сохраните.
4. «Создать клип» — FFmpeg соберёт H.264/AAC 1080×1920 с pre/post-roll и прожигом субтитров из транскрипта (если есть).

Удаление видео (кнопка «Удалить» на карточке) стирает исходник, клипы, транскрипты и записи из БД; занятое место видно в шапке.

Водяной знак настраивается в секции под моментами: площадка (Twitch/YouTube), ник, позиция (углы с учётом слепых зон интерфейса TikTok/Shorts).
Логотип подтягивается сам (официальный, кэшируется в `storage/watermarks/`); свой PNG положите как `storage/watermarks/twitch.png` или `youtube.png` — он будет в приоритете. Без сети останется только @ник текстом.
5. Посмотрите и скачайте результат.

Файлы: `storage/uploads`, `clips`, `thumbnails`, `transcripts`; метаданные — `storage/klipani.db`.

## Публикация на YouTube

1. В [Google Cloud Console](https://console.cloud.google.com/) создайте проект, включите **YouTube Data API v3**.
2. Создайте OAuth client (Desktop app), скачайте JSON и положите как `storage/yt_client.json`.
3. В клиенте добавьте redirect URI: `http://127.0.0.1:8000/api/publish/youtube/callback`.
4. В UI на карточке клипа нажмите «YouTube» → «Подключить YouTube», пройдите OAuth.
5. Укажите название/приватность и публикуйте. Токен хранится в `storage/yt_token.json`.

## Публикация в TikTok

1. На [developers.tiktok.com](https://developers.tiktok.com/) создайте приложение, добавьте продукты **Login Kit** и **Content Posting API** (включите Direct Post).
2. Задайте в `.env`: `TIKTOK_CLIENT_KEY=...`, `TIKTOK_CLIENT_SECRET=...`, redirect URI в приложении: `http://127.0.0.1:8000/api/publish/tiktok/callback`.
3. В UI на карточке клипа: вкладка TikTok → «Подключить TikTok» → OAuth.
4. Публикация идёт в **приват** (без аудита TikTok иначе нельзя) — откройте видео в приложении TikTok и переключите «Кто может смотреть» → «Все». Токен хранится в `storage/tt_token.json`.

## Мобильный компаньон (та же Wi-Fi сеть)

1. Узнайте IP мака: `ipconfig getifaddr en0` (например `192.168.1.10`);
2. Запустите бэкенд на всех интерфейсах: `python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`;
3. Запустите фронт с адресом API: `NEXT_PUBLIC_API_URL=http://192.168.1.10:8000 npm run dev -- --hostname 0.0.0.0`;
4. На телефоне откройте `http://192.168.1.10:3000` и добавьте на домашний экран (PWA-манифест уже встроен).

## Тесты

```bash
source .venv/bin/activate
PYTHONPATH=backend pytest backend/tests
```

## Что дальше (этап 2)

Субтитры, `MontagePlan`, эффекты и варианты vertical layout — см. [PLAN.md](PLAN.md).
