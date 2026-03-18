# ARTPOL Агент-Сметчик

Telegram-бот для менеджеров ARTPOL. AI-парсер текста замерщика → калькулятор → КП.

## Этап 1: AI-парсер
- Менеджер отправляет текст замера → Claude Haiku извлекает параметры → JSON
- Объекты за городом: расстояние от базы через Яндекс Routes API

## Переменные окружения (Railway Variables)
- `ESTIMATOR_BOT_TOKEN` — токен Telegram-бота
- `ANTHROPIC_API_KEY` — ключ Anthropic API
- `YANDEX_ROUTES_API_KEY` — ключ Яндекс Routes API

## Запуск
```
python bot.py
```
