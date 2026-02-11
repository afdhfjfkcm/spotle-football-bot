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

MAX_ATTEMPTS = 10

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
    club_emoji: str = ""  # optional


# -------------------- Load data --------------------
def norm(s: str) -> str:
    return " ".join(str(s).strip().lower().split())

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
            club_emoji=str(x.get("club_emoji", "") or ""),
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


# -------------------- Spotle-like tiles --------------------
GREEN = "🟩"
YELLOW = "🟨"
GREY = "⬛️"  # вместо красного (серый/тёмный)

POS_RU = {"GK": "Вратарь", "DEF": "Защитник", "MID": "Полузащитник", "FWD": "Нападающий"}

def arrow_need(guess_val: int, answer_val: int) -> str:
    """
    Стрелка "куда двигаться", чтобы попасть:
    - если ответ БОЛЬШЕ догадки -> нужно ↑
    - если ответ МЕНЬШЕ догадки -> нужно ↓
    """
    if guess_val == answer_val:
        return "✅"
    return "⬆️" if answer_val > guess_val else "⬇️"

def color_numeric(guess_val: int, answer_val: int, near_delta: int) -> str:
    if guess_val == answer_val:
        return GREEN
    if abs(guess_val - answer_val) <= near_delta:
        return YELLOW
    return GREY

def color_bool(ok: bool) -> str:
    return GREEN if ok else GREY

def tile(prefix: str, value: str, color: str, arrow_txt: str = "") -> str:
    extra = f" {arrow_txt}" if arrow_txt else ""
    return f"{color} {prefix}: {value}{extra}"

# --- Continents dictionary (расширяй под свои страны) ---
COUNTRY_TO_CONTINENT = {
    # Europe
    "italy": "europe",
    "france": "europe",
    "spain": "europe",
    "portugal": "europe",
    "england": "europe",
    "uk": "europe",
    "united kingdom": "europe",
    "netherlands": "europe",
    "germany": "europe",
    "croatia": "europe",
    "serbia": "europe",
    "belgium": "europe",
    "poland": "europe",
    "sweden": "europe",
    "norway": "europe",
    "denmark": "europe",
    "switzerland": "europe",
    "austria": "europe",
    "russia": "europe",

    # North America
    "usa": "north_america",
    "united states": "north_america",
    "mexico": "north_america",
    "canada": "north_america",

    # South America
    "brazil": "south_america",
    "argentina": "south_america",
    "uruguay": "south_america",
    "colombia": "south_america",
    "chile": "south_america",

    # Asia
    "japan": "asia",
    "south korea": "asia",
    "korea": "asia",
    "china": "asia",
    "iran": "asia",
    "saudi arabia": "asia",
    "turkey": "asia",

    # Africa
    "nigeria": "africa",
    "senegal": "africa",
    "egypt": "africa",
    "morocco": "africa",
    "cameroon": "africa",

    # Oceania
    "australia": "oceania",
    "new zealand": "oceania",
}

def continent_of(country: str) -> str:
    return COUNTRY_TO_CONTINENT.get(norm(country), "unknown")

def country_color(guess_country: str, answer_country: str) -> str:
    if norm(guess_country) == norm(answer_country):
        return GREEN
    g_cont = continent_of(guess_country)
    a_cont = continent_of(answer_country)
    if g_cont != "unknown" and g_cont == a_cont:
        return YELLOW
    return GREY

def build_feedback_spotle(guess: Player, answer: Player) -> str:
    # Debut: близко +/-2 года
    debut_color = color_numeric(guess.debut_year, answer.debut_year, near_delta=2)
    debut_arrow = arrow_need(guess.debut_year, answer.debut_year)

    # Club: точное совпадение
    club_ok = norm(guess.iconic_club) == norm(answer.iconic_club)
    club_color = color_bool(club_ok)
    club_value = f"{guess.club_emoji} {guess.iconic_club}".strip()

    # FIFA: близко +/-20, стрелка "куда двигаться"
    # пример из твоего сообщения:
    # answer=88, guess=92 -> answer меньше -> нужно ↓
    fifa_color = color_numeric(guess.fifa_rating, answer.fifa_rating, near_delta=20)
    fifa_arrow = arrow_need(guess.fifa_rating, answer.fifa_rating)

    # Awards: близко +/-1, стрелка "куда двигаться"
    awards_color = color_numeric(guess.top_awards, answer.top_awards, near_delta=1)
    awards_arrow = arrow_need(guess.top_awards, answer.top_awards)

    # Position: точное совпадение группы
    pos_ok = guess.position_group == answer.position_group
    pos_color = color_bool(pos_ok)

    # Country: green exact, yellow same continent, grey otherwise
    ctry_color = country_color(guess.birth_country, answer.birth_country)

    tiles = [
        tile("Debut", str(guess.debut_year), debut_color, debut_arrow),
        tile("Club", club_value, club_color, ""),
        tile("FIFA", str(guess.fifa_rating), fifa_color, fifa_arrow),
        tile("Awards", str(guess.top_awards), awards_color, awards_arrow),
        tile("Position", POS_RU.get(guess.position_group, guess.position_group), pos_color, ""),
        tile("Country", guess.birth_country, ctry_color, ""),
    ]

    # 2 строки по 3 "прямоугольника"
    line1 = " | ".join(tiles[:3])
    line2 = " | ".join(tiles[3:])
    return f"{line1}\n{line2}"

def resolve_guess_to_player(text: str) -> Optional[Player]:
    pid = ALIAS_TO_ID.get(norm(text))
    return PLAYERS_BY_ID.get(pid) if pid else None


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

async def reset_run(db, user_id: int, day: str):
    await db.execute(
        "DELETE FROM user_attempts WHERE user_id=? AND day=?",
        (user_id, day)
    )
    await db.execute(
        "INSERT OR REPLACE INTO user_runs(user_id, day, attempts, finished) VALUES(?, ?, 0, 0)",
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
        "/play — начать сегодняшнюю игру заново\n"
        "/status — мои попытки сегодня\n"
        "/help — помощь\n\n"
        "Пиши имя игрока (пример: messi)."
    )

@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(
        "Обозначения:\n"
        "🟩 точно\n"
        "🟨 близко\n"
        "⬛️ далеко/не совпало\n"
        "⬆️ нужно больше / позже\n"
        "⬇️ нужно меньше / раньше\n\n"
        f"Попыток в одном забеге: {MAX_ATTEMPTS}\n"
        "Можно перезапускать сегодня сколько угодно раз командой /play."
    )

@dp.message(Command("play"))
async def cmd_play(m: Message):
    day = dt.date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await reset_run(db, m.from_user.id, day)
        await db.commit()

    await m.answer(
        f"🎯 Игра дня ({day}) началась заново!\n"
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

    blocks = []
    for n, guess, fb in hist:
        blocks.append(f"{n}) {guess}\n{fb}")
    await m.answer("\n\n".join(blocks))

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
            await m.answer("Этот забег уже завершён. Напиши /play чтобы начать сегодняшнюю игру заново.")
            return

        attempts = row[0] if row else 0

        if attempts >= MAX_ATTEMPTS:
            await finish_run(db, m.from_user.id, day)
            await db.commit()
            await m.answer(f"😕 Попытки закончились. Ответ: {answer.name}\n\n/play — чтобы сыграть заново.")
            return

        fb = build_feedback_spotle(guess_player, answer)
        await add_attempt(db, m.from_user.id, day, m.text, fb)

        if guess_player.id == answer.id:
            await finish_run(db, m.from_user.id, day)
            await db.commit()
            await m.answer(f"🎉 Верно!\n{fb}\n\n✅ Победа за {attempts+1}/{MAX_ATTEMPTS}!\n/play — сыграть заново.")
            return

        if attempts + 1 >= MAX_ATTEMPTS:
            await finish_run(db, m.from_user.id, day)
            await db.commit()
            await m.answer(f"{fb}\n\n😕 Попытки закончились. Ответ: {answer.name}\n\n/play — сыграть заново.")
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
