import psycopg2
from psycopg2.extras import RealDictCursor
import json
import os
from datetime import date, datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Get DATABASE_URL from environment (Railway provides this automatically)
DATABASE_URL = os.getenv("DATABASE_URL")

# For local development, use SQLite as fallback
USE_POSTGRESQL = DATABASE_URL is not None
if USE_POSTGRESQL:
    print(f"✅ Using PostgreSQL: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'configured'}")
else:
    print(f"⚠️ No DATABASE_URL - SQLite fallback for local development")
    import sqlite3
    DATA_DIR = "/app/data" if os.path.exists("/app") else "."
    os.makedirs(DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(DATA_DIR, "pricebot.db")
    print(f"Using database: {DB_PATH}")


# ─── Connection Management ───────────────────────────────────────────────────────

def get_conn():
    """Get database connection (PostgreSQL or SQLite)."""
    if USE_POSTGRESQL:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def init_db():
    """Initialize database schema."""
    if USE_POSTGRESQL:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Create users table
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

                # Create tracked_ads table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tracked_ads (
                        id          SERIAL PRIMARY KEY,
                        user_id     BIGINT NOT NULL,
                        category    TEXT NOT NULL,
                        search_term TEXT NOT NULL,
                        max_price   REAL,
                        site        TEXT NOT NULL,
                        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at  TIMESTAMP,
                        is_active   INTEGER DEFAULT 1,
                        last_check  TIMESTAMP,
                        known_urls  TEXT DEFAULT '[]',
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    );
                """)

                # Create indexes for performance
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tracked_ads_user_active
                    ON tracked_ads(user_id, is_active);
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tracked_ads_active
                    ON tracked_ads(is_active);
                """)

                conn.commit()
    else:
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
    """Get or create user."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
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
        else:
            with conn:
                row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
                if not row:
                    conn.execute(
                        "INSERT INTO users (user_id, username) VALUES (?, ?)",
                        (user_id, username),
                    )
                    conn.commit()
                    return {"user_id": user_id, "plan": "free", "searches_today": 0, "last_search_date": None}
                return dict(row)
    finally:
        conn.close()


def get_user(user_id: int) -> dict:
    """Get user info by ID."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return {"user_id": user_id, "plan": "free", "searches_today": 0, "last_search_date": None}
                return dict(row)
        else:
            with conn:
                row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
                if not row:
                    return {"user_id": user_id, "plan": "free", "searches_today": 0, "last_search_date": None}
                return dict(row)
    finally:
        conn.close()


def count_user_active_ads(user_id: int) -> int:
    """Count active ads for a user."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) as count FROM tracked_ads WHERE user_id=%s AND is_active=1",
                    (user_id,),
                )
                row = cur.fetchone()
                return row[0] if row else 0
        else:
            with conn:
                row = conn.execute(
                    "SELECT COUNT(*) as count FROM tracked_ads WHERE user_id=? AND is_active=1",
                    (user_id,),
                ).fetchone()
                return row["count"] if row else 0
    finally:
        conn.close()


def can_search(user_id: int) -> bool:
    """Check if user can perform a search today (free plan limit: 1/day)."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return False

                if user["plan"] == "premium":
                    return True

                today = str(date.today())
                if user["last_search_date"] != today:
                    return True

                return user["searches_today"] < 1
        else:
            with conn:
                user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
                if not user:
                    return False

                if user["plan"] == "premium":
                    return True

                today = str(date.today())
                if user["last_search_date"] != today:
                    return True

                return user["searches_today"] < 1
    finally:
        conn.close()


def increment_search(user_id: int):
    """Increment search counter for today."""
    conn = get_conn()
    try:
        today = str(date.today())
        if USE_POSTGRESQL:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
                user = cur.fetchone()

                if user["last_search_date"] != today:
                    cur.execute(
                        "UPDATE users SET searches_today=1, last_search_date=%s WHERE user_id=%s",
                        (today, user_id),
                    )
                else:
                    cur.execute(
                        "UPDATE users SET searches_today=searches_today+1 WHERE user_id=%s",
                        (user_id,),
                    )
                conn.commit()
        else:
            with conn:
                user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

                if user["last_search_date"] != today:
                    conn.execute(
                        "UPDATE users SET searches_today=1, last_search_date=? WHERE user_id=?",
                        (today, user_id),
                    )
                else:
                    conn.execute(
                        "UPDATE users SET searches_today=searches_today+1 WHERE user_id=?",
                        (user_id,),
                    )
                conn.commit()
    finally:
        conn.close()


def set_premium(user_id: int, premium: bool = True):
    """Set user plan to premium or free."""
    plan = "premium" if premium else "free"
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET plan=%s WHERE user_id=%s", (plan, user_id))
                conn.commit()
        else:
            with conn:
                conn.execute("UPDATE users SET plan=? WHERE user_id=?", (plan, user_id))
                conn.commit()
    finally:
        conn.close()


# ─── Ad tracking ───────────────────────────────────────────────────────────────

def add_tracked_ad(user_id: int, category: str, search_term: str, max_price: float, site: str, is_premium: bool = False) -> int:
    """Add a new ad to track (returns ad ID)."""
    days = 30 if is_premium else 5
    expires_at = (datetime.now() + timedelta(days=days)).isoformat()

    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO tracked_ads
                       (user_id, category, search_term, max_price, site, expires_at, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, 1)
                       RETURNING id""",
                    (user_id, category, search_term, max_price, site, expires_at),
                )
                ad_id = cur.fetchone()[0]
                conn.commit()
                return ad_id
        else:
            with conn:
                cur = conn.execute(
                    """INSERT INTO tracked_ads
                       (user_id, category, search_term, max_price, site, expires_at, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, 1)""",
                    (user_id, category, search_term, max_price, site, expires_at),
                )
                conn.commit()
                return cur.lastrowid
    finally:
        conn.close()


def get_all_active_ads() -> list[dict]:
    """Get all active ads across all users."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM tracked_ads WHERE is_active=1")
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        else:
            with conn:
                rows = conn.execute(
                    "SELECT * FROM tracked_ads WHERE is_active=1"
                ).fetchall()
                return [dict(row) for row in rows]
    finally:
        conn.close()


def deactivate_ad(ad_id: int):
    """Mark ad as inactive."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor() as cur:
                cur.execute("UPDATE tracked_ads SET is_active=0 WHERE id=%s", (ad_id,))
                conn.commit()
        else:
            with conn:
                conn.execute("UPDATE tracked_ads SET is_active=0 WHERE id=?", (ad_id,))
                conn.commit()
    finally:
        conn.close()


def update_ad_known_urls(ad_id: int, urls: list):
    """Update the list of known URLs for an ad."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tracked_ads SET known_urls=%s, last_check=%s WHERE id=%s",
                    (json.dumps(urls), datetime.now().isoformat(), ad_id),
                )
                conn.commit()
        else:
            with conn:
                conn.execute(
                    "UPDATE tracked_ads SET known_urls=?, last_check=? WHERE id=?",
                    (json.dumps(urls), datetime.now().isoformat(), ad_id),
                )
                conn.commit()
    finally:
        conn.close()


# ─── Stats ───────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Get bot statistics."""
    conn = get_conn()
    try:
        if USE_POSTGRESQL:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as count FROM users")
                total_users = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) as count FROM users WHERE plan='free'")
                free_users = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) as count FROM users WHERE plan='premium'")
                premium_users = cur.fetchone()[0]

                today = str(date.today())
                cur.execute(
                    "SELECT COUNT(*) as count FROM users WHERE last_search_date=%s", (today,)
                )
                active_today = cur.fetchone()[0]

                cur.execute(
                    "SELECT SUM(searches_today) as total FROM users"
                )
                result = cur.fetchone()
                searches_today = result[0] or 0
        else:
            with conn:
                total_users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()["count"]
                free_users = conn.execute("SELECT COUNT(*) as count FROM users WHERE plan='free'").fetchone()["count"]
                premium_users = conn.execute("SELECT COUNT(*) as count FROM users WHERE plan='premium'").fetchone()["count"]

                today = str(date.today())
                active_today = conn.execute(
                    "SELECT COUNT(*) as count FROM users WHERE last_search_date=?", (today,)
                ).fetchone()["count"]

                searches_today = conn.execute(
                    "SELECT SUM(searches_today) as total FROM users"
                ).fetchone()["total"] or 0

        return {
            "total_users": total_users,
            "free_users": free_users,
            "premium_users": premium_users,
            "active_today": active_today,
            "searches_today": searches_today,
        }
    finally:
        conn.close()
