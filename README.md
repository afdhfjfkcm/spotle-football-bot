# Spotle Football Telegram Bot (aiogram)

## Локальный запуск
1) Создай токен у @BotFather
2) Установи зависимости:
   pip install -r requirements.txt
3) Запусти, передав токен:
   BOT_TOKEN="xxx" python bot.py

## Деплой на Render (самый простой)
1) Залей этот репозиторий на GitHub
2) Render → New → Background Worker
3) Connect repo
4) Build Command: pip install -r requirements.txt
5) Start Command: python bot.py
6) Environment Variables:
   BOT_TOKEN = твой токен

## Данные
- players.json — база игроков (aliase + признаки)
- puzzles.json — порядок игроков для "игры дня" (daily)
