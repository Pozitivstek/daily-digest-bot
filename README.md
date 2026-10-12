# 📬 Daily Digest Bot

**RU** — Telegram-бот, который раз в день присылает одним сообщением погоду, курсы валют ЦБ РФ и новости из выбранных источников. Время, город, валюты и источники настраиваются кнопками. Ключи API не нужны.
**EN** — A Telegram bot that sends a daily digest in one message: weather, Central Bank of Russia exchange rates and news from chosen sources. City, time, currencies and sources are configured with buttons. No API keys required.

Python 3.11+ · aiogram 3 · aiohttp · SQLite · 91 tests

## Что в дайджесте / What is in the digest

```
☀️ Доброе утро, Даниил!
Понедельник, 12 октября

☀️ Погода · Казань
Сейчас +17°, ясно
Сегодня: пасмурно, +7°…+17°, осадки 5%, ветер до 3 м/с

💱 Курсы ЦБ РФ на 10.10
USD — 84,90 ₽ (−0,51)
100 KZT — 18,91 ₽ (−0,19)

📰 Новости
РБК · Хабр · OpenNet · Real Python — по 3 заголовка со ссылками
```

| Данные / Data | Источник / Source | Ключ / Key |
|---|---|---|
| Курсы валют / Rates | ЦБ РФ `XML_daily.asp` | нет / none |
| Погода и поиск города / Weather, geocoding | [Open-Meteo](https://open-meteo.com) | нет / none |
| Новости / News | RSS: РБК, Лента.ру, Хабр, OpenNet, Real Python | нет / none |

## Как это устроено / Design

- **Секции независимы.** Если упал один источник, в дайджесте будет «временно недоступно» только для него.
- **Общий кэш с TTL** (курсы 30 мин, погода 20 мин, новости 10 мин): сто подписчиков одного города — один запрос погоды. Ошибки в кэш не попадают.
- **Время рассылки — по поясу города пользователя**; отметка «сегодня отправлено» хранится в БД, поэтому перезапуск не даёт дублей, а после простоя бот догоняет рассылку в пределах 3 часов.
- **Безопасность разбора:** `defusedxml` (защита от XML-бомб), только `http(s)`-ссылки, вырезаются `utm_*`, лимит размера ответа, весь пользовательский и сетевой текст экранируется.
- Заблокировавший бота пользователь автоматически отключается от рассылки.

## Запуск / Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # впишите BOT_TOKEN от @BotFather
.venv/bin/python -m digestbot
```

Команды бота: `/start`, `/settings`, `/digest` (прислать сейчас), `/help`.

## Тесты / Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```

Парсеры проверяются на фикстурах (включая XML-бомбу и `javascript:`-ссылки), `HttpFetcher` — против локального сервера (не-200, лимит размера, таймаут, обрыв), диалоги и рассылка — через подменённый Telegram API.

## Структура / Structure

```
digestbot/
  sources/        cbr.py · geo.py · weather.py · rss.py  — парсеры и загрузчики
  services.py     DataHub: общий кэш с TTL
  digest.py       сборка и разбиение сообщения
  schedule.py     is_due, рассылка по часовым поясам
  handlers.py     настройки: кнопки, поиск города, своё время
  db.py           подписчики (SQLite)
  http.py         HTTP-клиент: таймаут, лимит размера
tests/
```
