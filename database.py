import psycopg2
from psycopg2.extras import RealDictCursor
import json
import os
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

# Parse DATABASE_URL from Railway environment
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set")


def get_conn():
    """Connect to PostgreSQL database."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def init_db():
    """Initialize PostgreSQL database with required tables."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id          BIGINT PRIMARY KEY,
                    username         TEXT,
                    plan             TEXT    DEFAULT 'free',
                    searches_today   INTEGER DEFAULT 0,
                    last_search_date TEXT,
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tracked_ads (
                    id          SERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL,
                    category    TEXT    NOT NULL,
                    search_term TEXT    NOT NULL,
                    max_price   REAL,
                    site        TEXT    NOT NULL,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at  TEXT,
                    is_active   INTEGER DEFAULT 1,
                    last_check  TEXT,
                    known_urls  TEXT    DEFAULT '[]',
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
            """)
            conn.commit()


# ─── User operacije ───────────────────────────────────────────────────────────

def get_or_create_user(user_id: int, username: str) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO users (user_id, username) VALUES (%s, %s)",
                    (user_id, username),
                )
                conn.commit()
                return {"user_id": user_id, "plan": "free", "searches_today": 0, "last_search_date": None}
            return dict(row)


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def can_search(user_id: int) -> bool:
    return True  # TODO: vratiti limit kada završiš testiranje


def increment_search(user_id: int):
    today = date.today().isoformat()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            user = cur.fetchone()
            if user and user["last_search_date"] == today:
                cur.execute(
                    "UPDATE users SET searches_today = searches_today + 1 WHERE user_id=%s",
                    (user_id,),
                )
            else:
                cur.execute(
                    "UPDATE users SET searches_today=1, last_search_date=%s WHERE user_id=%s",
                    (today, user_id),
                )
            conn.commit()


def set_premium(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET plan='premium' WHERE user_id=%s", (user_id,))
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
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tracked_ads (user_id, category, search_term, max_price, site, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (user_id, category, search_term, max_price, site, expires_at),
            )
            conn.commit()


def count_user_active_ads(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM tracked_ads WHERE user_id=%s AND is_active=1",
                (user_id,),
            )
            return cur.fetchone()[0]


def get_all_active_ads() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM tracked_ads WHERE is_active=1")
            return [dict(r) for r in cur.fetchall()]


def deactivate_ad(ad_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tracked_ads SET is_active=0 WHERE id=%s", (ad_id,))
            conn.commit()


def update_ad_known_urls(ad_id: int, known_urls: list):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tracked_ads SET known_urls=%s, last_check=CURRENT_TIMESTAMP WHERE id=%s",
                (json.dumps(known_urls), ad_id),
            )
            conn.commit()


# ─── Stats operacije ──────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Vraća sve statistike za /stats komandu."""
    today = date.today().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Broj korisnika ukupno
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]

            # Broj free vs premium
            cur.execute("SELECT COUNT(*) FROM users WHERE plan='free'")
            free_users = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM users WHERE plan='premium'")
            premium_users = cur.fetchone()[0]

            # Broj pretraga danas
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE last_search_date=%s",
                (today,),
            )
            searches_today = cur.fetchone()[0]

            # Broj aktivnih korisnika danas
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE last_search_date=%s",
                (today,),
            )
            active_today = cur.fetchone()[0]

            # Ukupno pretraga danas (suma)
            cur.execute(
                "SELECT COALESCE(SUM(searches_today), 0) FROM users WHERE last_search_date=%s",
                (today,),
            )
            total_searches = cur.fetchone()[0]

            return {
                "total_users": total_users,
                "free_users": free_users,
                "premium_users": premium_users,
                "active_today": active_today,
                "searches_today": int(total_searches),
            }
