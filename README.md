# edge-tts-mcp

Асинхронный MCP-сервер синтеза речи Microsoft Edge с транспортом stdio.
Создаёт MP3, OGG/Opus, субтитры SRT/WebVTT и JSON с таймингами слов.
Аудиофайл создаётся всегда; субтитры и тайминги добавляются по запросу.

Требуется Python 3.12+. Для MP3 и субтитров FFmpeg не нужен.
Для OGG/Opus нужен FFmpeg с поддержкой libopus.
Текст отправляется в онлайн-сервис Microsoft; офлайн-режима нет.

## Структура

```text
main.py                 # запуск из корня проекта
edge_tts_mcp/
    __main__.py         # запуск через python -m edge_tts_mcp
    cli.py              # аргументы CLI и запуск сервера
    server.py           # MCP-инструменты и настройки окружения
    service.py          # синтез и публикация файлов
    audio.py            # проверка аудио и конвертация FFmpeg
    timings.py          # тайминги слов и формирование субтитров
Dockerfile
compose.yaml
tests/
pyproject.toml          # метаданные пакета и настройки разработки
requirements.txt        # прямые зависимости с фиксированными версиями
```

Все способы запуска используют одну функцию `edge_tts_mcp.cli:main`:
`python main.py`, `python -m edge_tts_mcp` и установленная команда `edge-tts-mcp`.

## Установка и запуск

Из корня проекта, Windows / PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --help
.\.venv\Scripts\python.exe main.py
```

Linux / macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --help
.venv/bin/python main.py
```

Сервер ожидает сообщения MCP в stdin. Это не интерактивная TTS-консоль.
Для установки как пакета выполните `python -m pip install .` в выбранном окружении.
После этого команда `edge-tts-mcp` доступна и вне каталога проекта.

`requirements.txt` и метаданные пакета содержат одинаковые прямые зависимости
в формате `библиотека==версия`; их соответствие проверяется тестом.
Транзитивные зависимости разрешает установщик. Lock-файл для установки не требуется.

## Подключение MCP

Укажите абсолютный путь к Python из окружения и к `main.py`:

```json
{
  "mcpServers": {
    "edge-tts-mcp": {
      "command": "/absolute/path/to/project/.venv/bin/python",
      "args": ["/absolute/path/to/project/main.py"],
      "env": {"EDGE_TTS_OUTPUT_DIR": "/absolute/path/to/output"}
    }
  }
}
```

На Windows используйте путь вида `F:/Projects/edge-tts-mcp/.venv/Scripts/python.exe`.
Если пакет установлен, вместо пути к `main.py` можно передать `["-m", "edge_tts_mcp"]`.
Таймаут MCP-клиента должен быть больше таймаута инструмента, например 60 секунд
при стандартном лимите синтеза 45 секунд.

## Инструменты

### `get_capabilities()`

Возвращает форматы, наличие исполняемого FFmpeg, ограничения, настройки голоса,
профили и доступность субтитров и таймингов. Сетевых запросов не делает.
Наличие FFmpeg не гарантирует поддержку libopus.

### `list_voice(locale="", gender="", query="", offset=0, limit=null)`

Без аргументов возвращает все голоса. `locale` фильтрует по префиксу без учёта
регистра (`ru`, `ru-RU`, `en`), `gender` принимает `Female`, `Male` или пустую строку.
`query` ищет по метаданным, `offset` и `limit` задают страницу результатов.
Поле `ShortName` из ответа передаётся в `tts.voice`.
Сетевой запрос ограничен 30 секундами; ошибка не маскируется пустым списком.

### `tts`

| Аргумент | По умолчанию | Назначение |
| --- | --- | --- |
| `text` | обязательный | Непустой текст, до 100000 символов |
| `voice` | настройка сервера | Изначально `ru-RU-SvetlanaNeural` |
| `rate` | настройка или профиль | Скорость, например `+15%` |
| `volume` | настройка или профиль | Громкость, например `-10%` |
| `pitch` | настройка или профиль | Высота, например `+5Hz` |
| `audio_format` | `mp3` | `mp3` или `ogg` |
| `subtitles` | `none` | `none`, `srt` или `vtt` |
| `word_timings` | `false` | Сохранять JSON с таймингами слов |
| `profile` | `default` | `default`, `narration`, `notification`, `slow` |
| `timeout` | `45` | Общий лимит: больше 0, не больше 300 секунд |

Обычное аудио:

```json
{"text": "Привет! Проверка синтеза речи."}
```

Аудио OGG/Opus и субтитры:

```json
{"text": "Привет, мир!", "audio_format": "ogg", "subtitles": "vtt"}
```

Аудио и тайминги слов:

```json
{"text": "Привет, мир!", "word_timings": true}
```

Тайминги соответствуют созданной озвучке с выбранным голосом и скоростью.
Это не распознавание существующей записи и не выравнивание по стороннему аудио или видео.

Успешный ответ содержит `success: true` и `path` к основному файлу:

- Всегда: `audio_path`, `size`, `duration_seconds`, `format`, `mime_type` — данные аудиофайла.
- С субтитрами: `subtitle_path`, `subtitle_format`.
- С таймингами: `word_timings_path`, `word_count`. JSON содержит объекты
  `{"word": "Привет", "start_ms": 100, "end_ms": 500}`.

По умолчанию создаётся только аудио. Субтитры и тайминги появляются по запросу.
Если события слов отсутствуют или некорректны, запрос возвращает аудио
и предупреждение `timing_metadata_unavailable`. Пустые субтитры и выдуманные
тайминги не публикуются.
Слова группируются в короткие фразы; пунктуация и написание могут быть
нормализованы сервисом. Субтитры не встраиваются в видео.

Профиль `default` использует настройки сервера. `narration` задаёт скорость `-10%`,
`notification` — `+15%`, `slow` — `-25%`, с нейтральной громкостью и высотой.
Явные параметры вызова перекрывают профиль. Профили не меняют голос и не задают эмоции.

Таймаут включает очередь, синтез, конвертацию и запись результатов.
Длинный текст разбивается библиотекой с сохранением сквозных смещений.
Для него увеличьте таймаут инструмента и клиента; лимит 100000 символов
не гарантирует завершения за 300 секунд.

Ошибки возвращают `success: false`, `error_code` и `error`.
Коды: `invalid_input`, `timeout`, `no_audio`, `audio_error`, `io_error`,
`service_error`, `feature_disabled`.
Нарушение MCP-схемы аргументов возвращается как ошибка инструмента.

## Настройки

| Переменная окружения | По умолчанию | Аргумент CLI |
| --- | --- | --- |
| `EDGE_TTS_OUTPUT_DIR` | `~/.edge-tts-mcp/output` | `--output-dir` |
| `EDGE_TTS_FFMPEG` | `ffmpeg` в PATH | `--ffmpeg` |
| `EDGE_TTS_VOICE` | `ru-RU-SvetlanaNeural` | `--voice` |
| `EDGE_TTS_RATE` | `+0%` | — |
| `EDGE_TTS_VOLUME` | `+0%` | — |
| `EDGE_TTS_PITCH` | `+0Hz` | — |
| `EDGE_TTS_ALLOW_SIDECARS` | `1` | `--disable-sidecars` запрещает субтитры/тайминги |
| `EDGE_TTS_MAX_CONCURRENT` | `2`, диапазон 1–16 | `--max-concurrent` |

CLI имеет приоритет над окружением. Файлы `.env` автоматически не загружаются.
Булевы настройки принимают `1/0`, `true/false`, `yes/no`, `on/off`.
Неверные настройки останавливают запуск с понятной ошибкой.

Пути в ответе относятся к машине MCP-сервера. Сервер создаёт файлы локально,
не воспроизводит их и никуда не загружает. Готовые файлы остаются до удаления
пользователем. Временные файлы удаляются при обычных ошибках и отмене запроса.
Каждый файл публикуется атомарно; набор файлов не является транзакцией
при аварийном завершении процесса. Новые каталоги на POSIX создаются с правами
`0700`, готовые файлы — `0600`; на Windows действуют наследуемые ACL.

## Docker

Сборка из корня проекта:

```sh
docker build -t edge-tts-mcp:local .
docker run --rm edge-tts-mcp:local --version
docker run --rm --init -i -v speech-output:/data/output edge-tts-mcp:local
```

Или через Compose:

```sh
docker compose build
docker compose run --rm -T edge-tts-mcp
```

Контейнер содержит FFmpeg и работает от непривилегированного пользователя.
Для stdio используйте `-i` без `-t`; сетевых портов нет.
Результаты в примере сохраняются в именованном томе.
Для доступа с хоста используйте bind mount и учитывайте отображение путей:
возвращаемый `/data/output/...` — путь внутри контейнера.
При необходимости задайте `APP_UID` и `APP_GID` при сборке под владельца каталога хоста.

## Разработка

В виртуальном окружении установите проект и инструменты разработки:

```sh
python -m pip install -e . --group dev
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m bandit -q -r edge_tts_mcp
python -m pytest -q --cov=edge_tts_mcp
python -m pip wheel --no-deps --wheel-dir dist .
```

Опция `--group` требует pip 25.1+. Вместо pip можно использовать
`uv pip install -e . --group dev` с выбранным виртуальным окружением.
FFmpeg для офлайн-тестов поставляется отдельной тестовой зависимостью.
Обычный запуск тестов не обращается к Microsoft и не требует Docker.

Проверки настоящего сервиса включаются явно, например в PowerShell:

```powershell
$env:EDGE_TTS_LIVE_TESTS = "1"
python -m pytest -q -m live -k "not long_text and not container"
Remove-Item Env:EDGE_TTS_LIVE_TESTS
```

Тесты контейнера требуют собранного образа и запущенного Docker:

```powershell
$env:EDGE_TTS_DOCKER_TESTS = "1"
python -m pytest -q -m "docker and not live"
Remove-Item Env:EDGE_TTS_DOCKER_TESTS
```

Лицензия кода проекта: MIT, см. [LICENSE](LICENSE).
Зависимости распространяются по собственным лицензиям.
Проект не является официальным продуктом Microsoft.
