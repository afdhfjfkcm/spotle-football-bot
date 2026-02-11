import os
import json
import datetime as dt
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

load_dotenv()

DB_PATH = "game.db"
PLAYERS_PATH = "players.json"
PUZZLES_PATH = "puzzles.json"

MAX_ATTEMPTS = 6

# -------------------- Models --------------------
@dataclass
class Player:
    id: str
    name: str
    aliases: List[str]
    debut_year: int
    iconic_club: str
    fifa_rating: int
    top_awards: int
    position_group: str  # GK/DEF/MID/FWD
    birth_country: str

# -------------------- Load data --------------------
def norm(s: str) -> str:
    return " ".join(s.strip().lower().split())

def load_players() -> Tuple[Dict[str, Player], Dict[str, str]]:
    with open(PLAYERS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    by_id: Dict[str, Player] = {}
    alias_to_id: Dict[str, str] = {}

    for x in raw:
        p = Player(
            id=str(x["id"]),
            name=str(x["name"]),
            aliases=[norm(a) for a in x.get("aliases", [])],
            debut_year=int(x["debut_year"]),
            iconic_club=str(x["iconic_club"]),
            fifa_rating=int(x["fifa_rating"]),
            top_awards=int(x["top_awards"]),
            position_group=str(x["position_group"]).upper(),
            birth_country=str(x["birth_country"]),
        )
        by_id[p.id] = p

        # Canonical name and aliases map
        alias_to_id[norm(p.id)] = p.id
        alias_to_id[norm(p.name)] = p.id
        for a in p.aliases:
            alias_to_id[norm(a)] = p.id

    return by_id, alias_to_id

def load_puzzles() -> Dict[str, Any]:
    with open(PUZZLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

PLAYERS_BY_ID, ALIAS_TO_ID = load_players()
PUZZLES = load_puzzles()

def puzzle_player_of_the_day(today: Optional[dt.date] = None) -> Player:
    if today is None:
        today = dt.date.today()

    order = PUZZLES.get("order", [])
    if not order:
        raise RuntimeError("puzzles.json: поле order пустое")

    idx = today.toordinal() % len(order)
    pid = order[idx]
    if pid not in PLAYERS_BY_ID:
        raise RuntimeError(f"puzzles.json: player id '{pid}' не найден в players.json")
    return PLAYERS_BY_ID[pid]

# -------------------- Feedback (Spotle-like) --------------------
def arrow_compare(guess_val: int, answer_val: int) -> str:
    if guess_val == answer_val:
        return "✅"
    return "↑" if guess_val < answer_val else "↓"

def eq_mark(guess: str, answer: str) -> str:
    return "✅" if norm(guess) == norm(answer) else "❌"

POS_RU = {"GK": "Вратарь", "DEF": "Защитник", "MID": "Полузащитник", "FWD": "Нападающий"}

def build_feedback(guess: Player, answer: Player) -> str:
    # Year / Rating / Awards: arrow
    year_mark   = arrow_compare(guess.debut_year, answer.debut_year)
    fifa_mark   = arrow_compare(guess.fifa_rating, answer.fifa_rating)
    award_mark  = arrow_compare(guess.top_awards, answer.top_awards)

    # Others: exact match
    club_mark   = eq_mark(guess.iconic_club, answer.iconic_club)
    pos_mark    = "✅" if guess.position_group == answer.position_group else "❌"
    ctry_mark   = eq_mark(guess.birth_country, answer.birth_country)

    # Compact “row” like Spotle
    row = (
        f"Дебют {year_mark} | Клуб {club_mark} | FIFA {fifa_mark} | Награды {award_mark} | "
        f"Позиция {pos_mark} | Страна {ctry_mark}"
    )

    # Helpful details (so arrows make sense)
    details = (
        f"\n\nТвоя догадка: {guess.name}\n"
        f"• Дебют: {guess.debut_year}\n"
        f"• Iconic club: {guess.iconic_club}\n"
        f"• FIFA: {guess.fifa_rating}\n"
        f"• Топ-награды: {guess.top_awards}\n"
        f"• Позиция: {POS_RU.get(guess.position_group, guess.position_group)}\n"
        f"• Страна рождения: {guess.birth_country}"
    )
    return row + details

def resolve_guess_to_player(text: str) -> Optional[Player]:
    key = norm(text)
    pid = ALIAS_TO_ID.get(key)
    if not pid:
        return None
    return PLAYERS_BY_ID[pid]

# -------------------- DB --------------------
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS user_runs (
  user_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  finished INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS user_attempts (
  user_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  n INTEGER NOT NULL,
  guess TEXT NOT NULL,
  feedback TEXT NOT NULL,
  PRIMARY KEY (user_id, day, n)
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()

async def get_run(db, user_id: int, day: str):
    cur = await db.execute(
        "SELECT attempts, finished FROM user_runs WHERE user_id=? AND day=?",
        (user_id, day)
    )
    return await cur.fetchone()

async def ensure_run(db, user_id: int, day: str):
    await db.execute(
        "INSERT OR IGNORE INTO user_runs(user_id, day, attempts, finished) VALUES(?, ?, 0, 0)",
        (user_id, day)
    )

async def add_attempt(db, user_id: int, day: str, guess: str, feedback: str):
    await ensure_run(db, user_id, day)
    row = await get_run(db, user_id, day)
    attempts = row[0] if row else 0
    n = attempts + 1

    await db.execute(
        "UPDATE user_runs SET attempts=? WHERE user_id=? AND day=?",
        (n, user_id, day)
    )
    await db.execute(
        "INSERT INTO user_attempts(user_id, day, n, guess, feedback) VALUES(?, ?, ?, ?, ?)",
        (user_id, day, n, guess, feedback)
    )

async def finish_run(db, user_id: int, day: str):
    await db.execute(
        "UPDATE user_runs SET finished=1 WHERE user_id=? AND day=?",
        (user_id, day)
    )

async def get_history(db, user_id: int, day: str) -> List[Tuple[int, str, str]]:
    cur = await db.execute(
        "SELECT n, guess, feedback FROM user_attempts WHERE user_id=? AND day=? ORDER BY n",
        (user_id, day)
    )
    return await cur.fetchall()

# -------------------- Bot --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Добавь переменную окружения BOT_TOKEN.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "⚽️ Spotle-подобная игра про футболистов.\n\n"
        "Команды:\n"
        "/play — начать игру дня\n"
        "/status — мои попытки сегодня\n"
        "/help — помощь\n\n"
        "Просто отправляй имя игрока сообщением."
    )

@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(
        "Как играть:\n"
        "1) /play\n"
        "2) Пиши имя футболиста (как в базе)\n\n"
        "Обозначения:\n"
        "✅ совпало\n"
        "❌ не совпало\n"
        "↑ нужно больше (позже/выше)\n"
        "↓ нужно меньше (раньше/ниже)\n\n"
        f"Попыток: {MAX_ATTEMPTS}\n"
        "Позиции: GK / DEF / MID / FWD"
    )

@dp.message(Command("play"))
async def cmd_play(m: Message):
    day = dt.date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_run(db, m.from_user.id, day)
        await db.commit()

    await m.answer(
        f"🎯 Игра дня ({day}) началась!\n"
        f"Попыток: {MAX_ATTEMPTS}\n"
        "Напиши имя игрока."
    )

@dp.message(Command("status"))
async def cmd_status(m: Message):
    day = dt.date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        hist = await get_history(db, m.from_user.id, day)

    if not hist:
        await m.answer("Сегодня попыток ещё нет. Нажми /play")
        return

    # показываем компактно: строка фидбека без деталей
    lines = []
    for n, guess, fb in hist:
        first_line = fb.split("\n", 1)[0]
        lines.append(f"{n}) {guess}\n{first_line}")
    await m.answer("\n\n".join(lines))

@dp.message(F.text)
async def on_guess(m: Message):
    day = dt.date.today().isoformat()
    answer = puzzle_player_of_the_day()

    guess_player = resolve_guess_to_player(m.text)
    if not guess_player:
        await m.answer("❓ Не нашёл такого игрока в базе. Попробуй другое написание/алиас.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        row = await get_run(db, m.from_user.id, day)
        if row and row[1] == 1:
            await m.answer("Сегодня ты уже закончил игру 🙂")
            return

        attempts = row[0] if row else 0
        if attempts >= MAX_ATTEMPTS:
            await finish_run(db, m.from_user.id, day)
            await db.commit()
            await m.answer(f"😕 Попытки закончились. Ответ: {answer.name}")
            return

        if guess_player.id == answer.id:
            fb = "🎉 Верно!\n" + build_feedback(guess_player, answer)
            await add_attempt(db, m.from_user.id, day, m.text, fb)
            await finish_run(db, m.from_user.id, day)
            await db.commit()
            await m.answer(f"{fb}\n\n✅ Победа за {attempts+1}/{MAX_ATTEMPTS} попыток!")
            return

        fb = build_feedback(guess_player, answer)
        await add_attempt(db, m.from_user.id, day, m.text, fb)

        # если это была последняя попытка
        if attempts + 1 >= MAX_ATTEMPTS:
            await finish_run(db, m.from_user.id, day)
            await db.commit()
            await m.answer(f"{fb}\n\n😕 Попытки закончились. Ответ: {answer.name}")
            return

        await db.commit()

    await m.answer(fb)

# -------------------- Run --------------------
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
