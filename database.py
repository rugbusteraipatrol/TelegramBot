import sqlite3
import json
import os
from datetime import date, datetime, timedelta

# Use persistent volume on Railway, or local directory for development
DATA_DIR = "/app/data" if os.path.exists("/app") else "."
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "pricebot.db")

print(f"📊 Using database: {DB_PATH}")


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


def can_search(user_id: int) -> bool:
    """Check if user can perform a search today (free plan limit: 1/day)."""
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            return False

        if user["plan"] == "premium":
            return True

        today = str(date.today())
        if user["last_search_date"] != today:
            return True

        return user["searches_today"] < 1


def increment_search(user_id: int):
    """Increment search counter for today."""
    with get_conn() as conn:
        today = str(date.today())
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


def set_premium(user_id: int, premium: bool = True):
    """Set user plan to premium or free."""
    plan = "premium" if premium else "free"
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan=? WHERE user_id=?", (plan, user_id))
        conn.commit()


# ─── Ad tracking ───────────────────────────────────────────────────────────────

def add_tracked_ad(user_id: int, category: str, search_term: str, max_price: float, site: str) -> int:
    """Add a new ad to track (returns ad ID)."""
    expires_at = (datetime.now() + timedelta(days=5)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tracked_ads
               (user_id, category, search_term, max_price, site, expires_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (user_id, category, search_term, max_price, site, expires_at),
        )
        conn.commit()
        return cur.lastrowid


def get_all_active_ads() -> list[dict]:
    """Get all active ads across all users."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tracked_ads WHERE is_active=1"
        ).fetchall()
        return [dict(row) for row in rows]


def deactivate_ad(ad_id: int):
    """Mark ad as inactive."""
    with get_conn() as conn:
        conn.execute("UPDATE tracked_ads SET is_active=0 WHERE id=?", (ad_id,))
        conn.commit()


def update_ad_known_urls(ad_id: int, urls: list):
    """Update the list of known URLs for an ad."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE tracked_ads SET known_urls=?, last_check=? WHERE id=?",
            (json.dumps(urls), datetime.now().isoformat(), ad_id),
        )
        conn.commit()


# ─── Stats ───────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Get bot statistics."""
    with get_conn() as conn:
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
