# Team Transcribe

Telegram-бот, который транскрибирует разговоры команды и складывает их в
папки проектов в git-репозитории.

Кидаешь боту **голосовое / аудио / видеофайл (MP4)** или **ссылку на YouTube /
Яндекс.Диск** +
**комментарий** с проектом, каналом, участниками и темой — бот транскрибирует
через [Deepgram](https://deepgram.com), кладёт `.md` в папку проекта,
коммитит в git и оповещает в чате. Временные медиафайлы после транскрибации
удаляются.

## Возможности

- 🎙 Транскрибация через Deepgram `nova-3` (русский из коробки)
- 👥 Разбивка по говорящим (diarization) — «кто что сказал»
- 🎬 Видеофайлы (MP4) — извлечение звука через `ffmpeg`
- ▶️ Ссылки YouTube — скачивание аудио через `yt-dlp` (+ `cookies.txt`)
- ☁️ Ссылки Яндекс.Диск (видео) — скачивание через `yt-dlp`
- 🧹 Временные медиафайлы удаляются после транскрибации
- ⏱ Для длинных аудио — оценка времени и прогресс (≤ 4 сообщения)
- 🗂 Автоматическая маршрутизация по проектам (по комментарию или уточняющим кнопкам)
- 🔐 Список допущенных пользователей + права доступа к проектам
- 🌳 Хранение в git-репозитории по правилам иерархии папок
- 📝 Авто-коммиты с понятными сообщениями
- 🔔 Уведомления в чат проекта / общий чат

## Как это работает

```
Пользователь ──аудио + «Эрмитаж, телефон, Саша + Макс, обсуждение плана»──▶ бот
   │
   ├─ 1. Проверка доступа (allowed_users)
   ├─ 2. Скачивание аудио
   ├─ 3. Разбор: проект → канал → участники → тема (недостающее спрашивает)
   ├─ 4. Deepgram: транскрибация + диаризация
   ├─ 5. Сохранение: <проект>/транскрибации/2026 - 07 20 - телефон - Саша + Макс - обсуждение плана.md
   ├─ 6. git pull --rebase → add → commit → push
   └─ 7. Уведомление в чат
```

## Установка

### 1. Клонировать

```bash
git clone https://github.com/maxnikolaev/team-transcribe.git
cd team-transcribe
```

### 2. Завести бота и ключ Deepgram

- **Telegram:** `t.me/BotFather` → `/newbot` → получить токен.
- **Deepgram:** зарегистрироваться на [console.deepgram.com](https://console.deepgram.com)
  → **API Keys** → создать ключ. При регистрации дают **$200 бесплатных кредитов**.

### 3. Настроить окружение

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# заполни .env своими значениями
```

Минимально нужно заполнить в `.env`:

```
TELEGRAM_BOT_TOKEN=...
DEEPGRAM_API_KEY=...
GIT_REPO_URL=git@github.com:you/your-repo.git
GIT_REPO_DIR=/путь/к/клону/your-repo
GIT_AUTHOR_NAME=...
GIT_AUTHOR_EMAIL=...
```

> Для приватных репозиториев git должен ходить по SSH — настрой
> deploy-ключ (`ssh-keygen` → GitHub → Settings → Deploy keys).

### 4. Настроить проекты и доступы

```bash
cp config/projects.example.yaml config/projects.yaml
# заполни config/projects.yaml своими данными
```

> `config/projects.yaml` не коммитится (в `.gitignore`) — это конфиг с
> реальными Telegram ID команды, он живёт только на сервере.

### 5. Запустить

```bash
.venv/bin/python -m transcribe
```

Либо как сервис (systemd, см. `systemd/team-transcribe.service.example`):

```bash
cp systemd/team-transcribe.service.example ~/.config/systemd/user/team-transcribe.service
systemctl --user daemon-reload
systemctl --user enable --now team-transcribe
journalctl --user -u team-transcribe -f
```

## Конфигурация проектов (`config/projects.yaml`)

Скопируй пример: `cp config/projects.example.yaml config/projects.yaml`.

```yaml
# Кто может обращаться к боту
allowed_users:
  - tg_id: 84225163        # свой id можно узнать у @userinfobot
    name: Макс
    role: owner            # owner | member

# Проекты
projects:
  - id: ermitazh
    name: Усадьба Эрмитаж
    folder: "cases/2025-09-14 - земля - Усадьба Эрмитаж/"
    language: ru           # перекрывает DEEPGRAM_LANGUAGE
    members:               # доступы участников
      - tg_id: 84225163
        role: owner
        can_create_folders: true
      - tg_id: 274335772
        role: member
        can_create_folders: false
    notify_chat: 562953535389958   # чат для уведомлений (опционально)
```

**Правила доступов:**
- Пользователь видит/трогает только проекты, где он в `members`.
- `can_create_folders: false` — не может создавать новые проекты, только добавлять в существующие.
- `role: owner` — полные права (в т.ч. создание новых проектов).

## Правила именования и папок

Транскрипты складываются в подпапку `транскрибации` (меняется через
`TRANSCRIPTS_SUBFOLDER`) внутри папки проекта. В git попадают **только `.md`** —
без JSON и без аудио.

```
<repo>/<project.folder>/транскрибации/
└── 2026 - 07 20 - телефон - Саша + Макс - обсуждение плана.md
```

**Формат имени файла** (без подчёркиваний, через « - »):

```
YYYY - MM DD - канал - участники - тема.md
```

- `YYYY - MM DD` — год - месяц число (MSK)
- `канал` — телефон / телеграм / встреча / зум / диктофон
- `участники` — «Саша + Макс»
- `тема` — «обсуждение плана»

## Видео и YouTube

**Видеофайл (MP4):** просто кинь боту видео — он вытащит звук через `ffmpeg`
(16 kHz mono WAV) и транскрибирует как обычное аудио.

**Ссылка YouTube:** кинь ссылку текстом (`youtube.com/watch?v=…`, `youtu.be/…`,
`shorts/…`). Бот скачает аудио через `yt-dlp` (bestaudio → mp3).

**Cookies (для защищённых роликов):** сервер часто упирается в проверку
«Sign in to confirm you're not a bot», поэтому нужен `cookies.txt`
(Netscape-формат, экспорт расширением «Get cookies.txt LOCALLY»):

1. Пришли `cookies.txt` **файлом** боту — он сохранит его в общий файл.
2. Кинь ссылку текстом — бот скачает с cookies.

Либо сразу: пришли файл `cookies.txt` **с подписью-ссылкой** — бот скачает
сразу этим файлом.

Cookies хранятся **только на сервере** (вне git) в одном общем файле
`.cookies/cookies.txt`. Если YouTube вернёт «Sign in to confirm…» — бот
попросит новый файл; **первый корректный** `cookies.txt` перезаписывает общий
и используется до следующего сбоя.

## Переменные окружения (`.env`)

| Переменная | Описание | По умолчанию |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота от BotFather | — |
| `DEEPGRAM_API_KEY` | ключ Deepgram | — |
| `DEEPGRAM_MODEL` | модель транскрибации | `nova-3` |
| `DEEPGRAM_LANGUAGE` | язык по умолчанию | `ru` |
| `DEEPGRAM_DIARIZE` | разбивка по говорящим | `true` |
| `DEEPGRAM_SMART_FORMAT` | автоформатирование текста | `true` |
| `GIT_REPO_URL` | целевой репозиторий | — |
| `GIT_REPO_DIR` | локальный клон | — |
| `GIT_BRANCH` | ветка | `main` |
| `TRANSCRIPTS_SUBFOLDER` | подпапка транскриптов | `транскрибации` |
| `NOTIFY_CHAT_ID` | глобальный чат уведомлений | — |

## Требования

- Python 3.10+
- `ffmpeg` (извлечение звука из видео, конвертация редких форматов)
- `yt-dlp` + `yt-dlp-ejs` (скачивание с YouTube; ставятся из `requirements.txt`)
- `node` ≥ 22 (JS-runtime для «n challenge» YouTube)
- git + доступ к целевому репозиторию

## Лицензия

MIT — см. [LICENSE](LICENSE).
