import sqlite3
import json
from datetime import date, datetime, timedelta

DB_PATH = "pricebot.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id          INTEGER PRIMARY KEY,
                username         TEXT,
                plan             TEXT    DEFAULT 'free',
                searches_today   INTEGER DEFAULT 0,
                last_search_date TEXT,
                created_at       TEXT    DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tracked_ads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                category    TEXT    NOT NULL,
                search_term TEXT    NOT NULL,
                max_price   REAL,
                site        TEXT    NOT NULL,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
                expires_at  TEXT,
                is_active   INTEGER DEFAULT 1,
                last_check  TEXT,
                known_urls  TEXT    DEFAULT '[]',
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
        """)


# ─── User operacije ───────────────────────────────────────────────────────────

def get_or_create_user(user_id: int, username: str) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username),
            )
            conn.commit()
            return {"user_id": user_id, "plan": "free", "searches_today": 0, "last_search_date": None}
        return dict(row)


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def can_search(user_id: int) -> bool:
    return True  # TODO: vratiti limit kada završiš testiranje


def increment_search(user_id: int):
    today = date.today().isoformat()
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if user and user["last_search_date"] == today:
            conn.execute(
                "UPDATE users SET searches_today = searches_today + 1 WHERE user_id=?",
                (user_id,),
            )
        else:
            conn.execute(
                "UPDATE users SET searches_today=1, last_search_date=? WHERE user_id=?",
                (today, user_id),
            )
        conn.commit()


def set_premium(user_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan='premium' WHERE user_id=?", (user_id,))
        conn.commit()


# ─── Ad operacije ─────────────────────────────────────────────────────────────

def add_tracked_ad(
    user_id: int,
    category: str,
    search_term: str,
    max_price: float | None,
    site: str,
    is_premium: bool,
):
    expires_at = None if is_premium else (datetime.now() + timedelta(days=5)).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tracked_ads (user_id, category, search_term, max_price, site, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, category, search_term, max_price, site, expires_at),
        )
        conn.commit()


def count_user_active_ads(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM tracked_ads WHERE user_id=? AND is_active=1",
            (user_id,),
        ).fetchone()[0]


def get_all_active_ads() -> list[dict]:
    with get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute("SELECT * FROM tracked_ads WHERE is_active=1").fetchall()
        ]


def deactivate_ad(ad_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE tracked_ads SET is_active=0 WHERE id=?", (ad_id,))
        conn.commit()


def update_ad_known_urls(ad_id: int, known_urls: list):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tracked_ads SET known_urls=?, last_check=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(known_urls), ad_id),
        )
        conn.commit()
