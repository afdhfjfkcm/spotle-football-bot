import os
import json
import datetime as dt
import random
import string
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

load_dotenv()

DB_PATH = "game.db"
PLAYERS_PATH = "players.json"
PUZZLES_PATH = "puzzles.json"

MAX_ATTEMPTS = 10
SUGGEST_LIMIT = 8

# -------------------- Models --------------------
@dataclass
class Player:
    id: str
    name: str
    aliases: List[str]
    debut_year: int
    iconic_club: str
    fifa_rating: int
    value_eur: int
    position_group: str  # GK/DEF/MID/FWD
    birth_country: str
    club_emoji: str = ""


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
            debut_year=int(x.get("debut_year", 2005)),
            iconic_club=str(x.get("iconic_club", "")),
            fifa_rating=int(x.get("fifa_rating", 0)),
            value_eur=int(x.get("value_eur", 0)),
            position_group=str(x.get("position_group", "MID")).upper(),
            birth_country=str(x.get("birth_country", "")),
            club_emoji=str(x.get("club_emoji", "") or ""),
        )
        by_id[p.id] = p

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

# build search index for substring matches (name + aliases)
SEARCH_INDEX: List[Tuple[str, str]] = []  # (search_blob, player_id)
for pid, p in PLAYERS_BY_ID.items():
    blob = norm(p.name) + " " + " ".join(norm(a) for a in p.aliases)
    SEARCH_INDEX.append((blob, pid))

def find_players_by_substring(q: str, limit: int = SUGGEST_LIMIT) -> List[Player]:
    qn = norm(q)
    if len(qn) < 3:
        return []
    hits = []
    for blob, pid in SEARCH_INDEX:
        pos = blob.find(qn)
        if pos != -1:
            p = PLAYERS_BY_ID[pid]
            hits.append((pos, -p.fifa_rating, pid))
    hits.sort()
    return [PLAYERS_BY_ID[pid] for _, __, pid in hits[:limit]]

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

def random_player_from_pool() -> Player:
    order = PUZZLES.get("order", [])
    pid = random.choice(order) if order else random.choice(list(PLAYERS_BY_ID.keys()))
    return PLAYERS_BY_ID[pid]

def resolve_guess_to_player(text: str) -> Optional[Player]:
    pid = ALIAS_TO_ID.get(norm(text))
    return PLAYERS_BY_ID.get(pid) if pid else None


# -------------------- UI / feedback (Spotle-like) --------------------
GREEN = "🟩"
YELLOW = "🟨"
GREY = "⬛️"  # вместо красного

POS_RU = {"GK": "Вратарь", "DEF": "Защитник", "MID": "Полузащитник", "FWD": "Нападающий"}

COUNTRY_TO_CONTINENT = {
    # Europe
    "italy": "europe", "france": "europe", "spain": "europe", "portugal": "europe",
    "england": "europe", "uk": "europe", "united kingdom": "europe",
    "netherlands": "europe", "germany": "europe", "croatia": "europe", "serbia": "europe",
    "belgium": "europe", "poland": "europe", "sweden": "europe", "norway": "europe",
    "denmark": "europe", "switzerland": "europe", "austria": "europe", "russia": "europe",
    # North America
    "usa": "north_america", "united states": "north_america", "mexico": "north_america", "canada": "north_america",
    # South America
    "brazil": "south_america", "argentina": "south_america", "uruguay": "south_america", "colombia": "south_america", "chile": "south_america",
    # Asia
    "japan": "asia", "south korea": "asia", "korea": "asia", "china": "asia", "iran": "asia", "saudi arabia": "asia", "turkey": "asia",
    # Africa
    "nigeria": "africa", "senegal": "africa", "egypt": "africa", "morocco": "africa", "cameroon": "africa",
    # Oceania
    "australia": "oceania", "new zealand": "oceania",
}

def continent_of(country: str) -> str:
    return COUNTRY_TO_CONTINENT.get(norm(country), "unknown")

def country_color(guess_country: str, answer_country: str) -> str:
    if norm(guess_country) == norm(answer_country):
        return GREEN
    g = continent_of(guess_country)
    a = continent_of(answer_country)
    if g != "unknown" and g == a:
        return YELLOW
    return GREY

def arrow_need(guess_val: int, answer_val: int) -> str:
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

def fmt_money_eur(v: int) -> str:
    if v >= 1_000_000:
        return f"€{v/1_000_000:.0f}m"
    if v >= 1_000:
        return f"€{v/1_000:.0f}k"
    return f"€{v}"

def build_feedback_spotle(guess: Player, answer: Player) -> str:
    debut_color = color_numeric(guess.debut_year, answer.debut_year, near_delta=2)
    debut_arrow = arrow_need(guess.debut_year, answer.debut_year)

    club_ok = norm(guess.iconic_club) == norm(answer.iconic_club)
    club_color = color_bool(club_ok)
    club_value = f"{guess.club_emoji} {guess.iconic_club}".strip()

    fifa_color = color_numeric(guess.fifa_rating, answer.fifa_rating, near_delta=20)
    fifa_arrow = arrow_need(guess.fifa_rating, answer.fifa_rating)

    value_color = color_numeric(guess.value_eur, answer.value_eur, near_delta=5_000_000)
    value_arrow = arrow_need(guess.value_eur, answer.value_eur)

    pos_ok = guess.position_group == answer.position_group
    pos_color = color_bool(pos_ok)

    ctry_color = country_color(guess.birth_country, answer.birth_country)

    tiles = [
        tile("Debut", str(guess.debut_year), debut_color, debut_arrow),
        tile("Club", club_value, club_color, ""),
        tile("FIFA", str(guess.fifa_rating), fifa_color, fifa_arrow),
        tile("Value", fmt_money_eur(guess.value_eur), value_color, value_arrow),
        tile("Position", POS_RU.get(guess.position_group, guess.position_group), pos_color, ""),
        tile("Country", guess.birth_country, ctry_color, ""),
    ]
    return " | ".join(tiles[:3]) + "\n" + " | ".join(tiles[3:])


# -------------------- DB (sessions + suggestions + challenges) --------------------
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS user_sessions (
  user_id INTEGER NOT NULL,
  session_key TEXT NOT NULL,
  answer_id TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  finished INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, session_key)
);

CREATE TABLE IF NOT EXISTS user_attempts (
  user_id INTEGER NOT NULL,
  session_key TEXT NOT NULL,
  n INTEGER NOT NULL,
  guess TEXT NOT NULL,
  feedback TEXT NOT NULL,
  PRIMARY KEY (user_id, session_key, n)
);

CREATE TABLE IF NOT EXISTS user_active (
  user_id INTEGER PRIMARY KEY,
  session_key TEXT
);

CREATE TABLE IF NOT EXISTS challenges (
  code TEXT PRIMARY KEY,
  answer_id TEXT NOT NULL,
  creator_user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_suggestions (
  user_id INTEGER PRIMARY KEY,
  token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  choices_json TEXT NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()

async def set_active_session(db, user_id: int, session_key: str):
    await db.execute(
        "INSERT INTO user_active(user_id, session_key) VALUES(?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET session_key=excluded.session_key",
        (user_id, session_key)
    )

async def get_active_session(db, user_id: int) -> Optional[str]:
    cur = await db.execute("SELECT session_key FROM user_active WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    return row[0] if row else None

async def create_or_reset_session(db, user_id: int, session_key: str, answer_id: str):
    await db.execute(
        "DELETE FROM user_attempts WHERE user_id=? AND session_key=?",
        (user_id, session_key)
    )
    await db.execute(
        "INSERT INTO user_sessions(user_id, session_key, answer_id, attempts, finished, created_at) "
        "VALUES(?, ?, ?, 0, 0, ?) "
        "ON CONFLICT(user_id, session_key) DO UPDATE SET answer_id=excluded.answer_id, attempts=0, finished=0, created_at=excluded.created_at",
        (user_id, session_key, answer_id, dt.datetime.utcnow().isoformat())
    )

async def get_session(db, user_id: int, session_key: str):
    cur = await db.execute(
        "SELECT answer_id, attempts, finished FROM user_sessions WHERE user_id=? AND session_key=?",
        (user_id, session_key)
    )
    return await cur.fetchone()

async def add_attempt(db, user_id: int, session_key: str, guess: str, feedback: str):
    row = await get_session(db, user_id, session_key)
    if not row:
        raise RuntimeError("Session not found when adding attempt")

    answer_id, attempts, finished = row
    n = attempts + 1

    await db.execute(
        "UPDATE user_sessions SET attempts=? WHERE user_id=? AND session_key=?",
        (n, user_id, session_key)
    )
    await db.execute(
        "INSERT INTO user_attempts(user_id, session_key, n, guess, feedback) VALUES(?, ?, ?, ?, ?)",
        (user_id, session_key, n, guess, feedback)
    )

async def finish_session(db, user_id: int, session_key: str):
    await db.execute(
        "UPDATE user_sessions SET finished=1 WHERE user_id=? AND session_key=?",
        (user_id, session_key)
    )

async def get_history(db, user_id: int, session_key: str) -> List[Tuple[int, str, str]]:
    cur = await db.execute(
        "SELECT n, guess, feedback FROM user_attempts WHERE user_id=? AND session_key=? ORDER BY n",
        (user_id, session_key)
    )
    return await cur.fetchall()

def make_code(n: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))

async def create_challenge(db, creator_user_id: int, answer_id: str) -> str:
    for _ in range(20):
        code = make_code(6)
        try:
            await db.execute(
                "INSERT INTO challenges(code, answer_id, creator_user_id, created_at) VALUES(?, ?, ?, ?)",
                (code, answer_id, creator_user_id, dt.datetime.utcnow().isoformat())
            )
            return code
        except Exception:
            continue
    raise RuntimeError("Не удалось создать уникальный код")

async def get_challenge_answer(db, code: str) -> Optional[str]:
    cur = await db.execute("SELECT answer_id FROM challenges WHERE code=?", (code,))
    row = await cur.fetchone()
    return row[0] if row else None

def _token(n: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))

async def set_suggestions(db, user_id: int, choices: List[str]) -> str:
    token = _token(10)
    await db.execute(
        "INSERT INTO user_suggestions(user_id, token, created_at, choices_json) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, created_at=excluded.created_at, choices_json=excluded.choices_json",
        (user_id, token, dt.datetime.utcnow().isoformat(), json.dumps(choices, ensure_ascii=False))
    )
    return token

async def get_suggestions(db, user_id: int) -> Optional[Tuple[str, List[str]]]:
    cur = await db.execute("SELECT token, choices_json FROM user_suggestions WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    if not row:
        return None
    token = row[0]
    try:
        choices = json.loads(row[1])
    except Exception:
        choices = []
    return token, choices

async def clear_suggestions(db, user_id: int):
    await db.execute("DELETE FROM user_suggestions WHERE user_id=?", (user_id,))


# -------------------- Suggestions UI --------------------
def build_suggest_kb(token: str, players: List[Player]) -> InlineKeyboardMarkup:
    rows = []
    for i, p in enumerate(players, 1):
        rows.append([InlineKeyboardButton(text=f"{i}) {p.name}", callback_data=f"sug:{token}:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -------------------- Shared guess handler --------------------
async def handle_guess(user_id: int, reply_fn, guess_player: Player):
    async with aiosqlite.connect(DB_PATH) as db:
        session_key = await get_active_session(db, user_id)
        if not session_key:
            await reply_fn("Сначала начни игру: /play (или /daily, /join CODE)")
            return

        row = await get_session(db, user_id, session_key)
        if not row:
            await reply_fn("Сессия сломалась. Нажми /play чтобы начать заново.")
            return

        answer_id, attempts, finished = row
        if finished == 1:
            await reply_fn("Этот забег уже завершён. Нажми /play чтобы начать новый.")
            return

        answer = PLAYERS_BY_ID.get(answer_id)
        if not answer:
            await reply_fn("Не нашла загаданного игрока в базе. Нажми /play.")
            return

        if attempts >= MAX_ATTEMPTS:
            await finish_session(db, user_id, session_key)
            await db.commit()
            await reply_fn(f"😕 Попытки закончились. Ответ: {answer.name}\n\n/play — новый раунд.")
            return

        fb = build_feedback_spotle(guess_player, answer)
        await add_attempt(db, user_id, session_key, guess_player.name, fb)

        if guess_player.id == answer.id:
            await finish_session(db, user_id, session_key)
            await db.commit()
            await reply_fn(f"🎉 Верно!\n{fb}\n\n✅ Победа за {attempts+1}/{MAX_ATTEMPTS}!\n/play — новый раунд.")
            return

        if attempts + 1 >= MAX_ATTEMPTS:
            await finish_session(db, user_id, session_key)
            await db.commit()
            await reply_fn(f"{fb}\n\n😕 Попытки закончились. Ответ: {answer.name}\n\n/play — новый раунд.")
            return

        await db.commit()
        await reply_fn(fb)


# -------------------- Bot --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Добавь переменную окружения BOT_TOKEN.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "⚽️ Игра угадай футболиста.\n\n"
        "Команды:\n"
        "/play — бесконечная игра (случайный игрок)\n"
        "/daily — игрок дня (опционально)\n"
        "/status — показать текущие попытки\n"
        "/challenge <имя> — загадать игрока и получить код\n"
        "/join <код> — присоединиться к челленджу\n"
        "/help — помощь\n\n"
        "Пиши имя игрока. Если не найдёт — покажу кнопки."
    )

@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(
        "Обозначения:\n"
        "🟩 точно\n"
        "🟨 близко\n"
        "⬛️ не совпало\n"
        "⬆️/⬇️ куда двигаться\n\n"
        f"Попыток на забег: {MAX_ATTEMPTS}\n"
        "Режимы:\n"
        "• /play — бесконечно\n"
        "• /challenge <имя> → код → /join <код>\n"
    )

@dp.message(Command("play"))
async def cmd_play(m: Message):
    p = random_player_from_pool()
    session_key = f"rand:{dt.datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{random.randint(1000,9999)}"

    async with aiosqlite.connect(DB_PATH) as db:
        await create_or_reset_session(db, m.from_user.id, session_key, p.id)
        await set_active_session(db, m.from_user.id, session_key)
        await clear_suggestions(db, m.from_user.id)
        await db.commit()

    await m.answer(
        "🎲 Новый раунд!\n"
        f"Попыток: {MAX_ATTEMPTS}\n"
        "Пиши имя игрока."
    )

@dp.message(Command("daily"))
async def cmd_daily(m: Message):
    day = dt.date.today().isoformat()
    p = puzzle_player_of_the_day()
    session_key = f"daily:{day}"

    async with aiosqlite.connect(DB_PATH) as db:
        await create_or_reset_session(db, m.from_user.id, session_key, p.id)
        await set_active_session(db, m.from_user.id, session_key)
        await clear_suggestions(db, m.from_user.id)
        await db.commit()

    await m.answer(
        f"📅 Игра дня ({day}) началась заново.\n"
        f"Попыток: {MAX_ATTEMPTS}\n"
        "Пиши имя игрока."
    )

@dp.message(Command("challenge"))
async def cmd_challenge(m: Message):
    arg = (m.text or "").split(maxsplit=1)
    if len(arg) < 2:
        await m.answer("Напиши так: /challenge messi")
        return

    p = resolve_guess_to_player(arg[1])
    if not p:
        # попробуем подсказки по подстроке
        sugg = find_players_by_substring(arg[1], limit=SUGGEST_LIMIT)
        if not sugg:
            await m.answer("❓ Не нашла такого игрока. Попробуй другое написание.")
            return
        # если несколько — попросим выбрать кнопкой (через suggestions)
        async with aiosqlite.connect(DB_PATH) as db:
            token = await set_suggestions(db, m.from_user.id, [x.id for x in sugg])
            await db.commit()
        kb = build_suggest_kb(token, sugg)
        await m.answer("🔎 Для челленджа выбери игрока кнопкой:", reply_markup=kb)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        code = await create_challenge(db, m.from_user.id, p.id)
        await db.commit()

    await m.answer(
        "✅ Челлендж создан!\n"
        f"Код: `{code}`\n\n"
        "Отправь другу этот код.\n"
        "Друг запускает: /join CODE"
    )

@dp.message(Command("join"))
async def cmd_join(m: Message):
    arg = (m.text or "").split(maxsplit=1)
    if len(arg) < 2:
        await m.answer("Напиши так: /join ABC123")
        return
    code = arg[1].strip().upper()
    session_key = f"chal:{code}"

    async with aiosqlite.connect(DB_PATH) as db:
        answer_id = await get_challenge_answer(db, code)
        if not answer_id:
            await m.answer("Не нашла такой код 😕 Проверь и попробуй ещё раз.")
            return

        await create_or_reset_session(db, m.from_user.id, session_key, answer_id)
        await set_active_session(db, m.from_user.id, session_key)
        await clear_suggestions(db, m.from_user.id)
        await db.commit()

    await m.answer(
        f"🎯 Челлендж {code} начался!\n"
        f"Попыток: {MAX_ATTEMPTS}\n"
        "Пиши имя игрока."
    )

@dp.message(Command("status"))
async def cmd_status(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        session_key = await get_active_session(db, m.from_user.id)
        if not session_key:
            await m.answer("Нет активной игры. Нажми /play")
            return
        hist = await get_history(db, m.from_user.id, session_key)

    if not hist:
        await m.answer(f"Активная игра: {session_key}\nПока нет попыток. Пиши имя игрока.")
        return

    blocks = []
    for n, guess, fb in hist:
        blocks.append(f"{n}) {guess}\n{fb}")
    await m.answer("\n\n".join(blocks))


# -------------------- Inline suggestions callback --------------------
@dp.callback_query(F.data.startswith("sug:"))
async def on_suggest_click(cb: CallbackQuery):
    # sug:<token>:<idx>
    try:
        _, token, idx_str = cb.data.split(":")
        idx = int(idx_str)
    except Exception:
        await cb.answer("Ошибка кнопки 😕", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        row = await get_suggestions(db, cb.from_user.id)
        if not row:
            await cb.answer("Подсказки устарели. Напиши запрос заново.", show_alert=True)
            return
        saved_token, choices = row
        if saved_token != token:
            await cb.answer("Подсказки устарели. Напиши запрос заново.", show_alert=True)
            return
        if idx < 1 or idx > len(choices):
            await cb.answer("Неверный выбор.", show_alert=True)
            return

        pid = choices[idx - 1]
        await clear_suggestions(db, cb.from_user.id)
        await db.commit()

    p = PLAYERS_BY_ID.get(pid)
    if not p:
        await cb.answer("Игрок не найден.", show_alert=True)
        return

    await cb.answer()

    # Если пользователь нажал кнопку после /challenge ... и НЕТ активной сессии,
    # считаем это выбором игрока для челленджа: создаём код.
    async with aiosqlite.connect(DB_PATH) as db:
        active = await get_active_session(db, cb.from_user.id)
        if not active:
            code = await create_challenge(db, cb.from_user.id, p.id)
            await db.commit()
            await cb.message.answer(
                "✅ Челлендж создан!\n"
                f"Код: `{code}`\n\n"
                "Отправь другу этот код.\n"
                "Друг запускает: /join CODE"
            )
            return

    # Иначе — это обычная догадка в активной игре
    await handle_guess(cb.from_user.id, cb.message.answer, p)


# -------------------- Text guesses --------------------
@dp.message(F.text)
async def on_guess(m: Message):
    text = (m.text or "").strip()

    # 1) exact match via aliases
    p = resolve_guess_to_player(text)
    if p:
        await handle_guess(m.from_user.id, m.answer, p)
        return

    # 2) substring suggestions with buttons
    sugg = find_players_by_substring(text, limit=SUGGEST_LIMIT)
    if not sugg:
        await m.answer("❓ Не нашла такого игрока. Попробуй другое написание (минимум 3 символа).")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        token = await set_suggestions(db, m.from_user.id, [x.id for x in sugg])
        await db.commit()

    kb = build_suggest_kb(token, sugg)
    await m.answer("🔎 Нашла похожих — выбери кнопкой:", reply_markup=kb)


# -------------------- Run --------------------
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
