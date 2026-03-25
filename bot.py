import json
import logging
import os
import re
from datetime import datetime

import httpx
from dotenv import load_dotenv
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import scraper

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
STRIPE_LINK = os.getenv("STRIPE_LINK", "https://buy.stripe.com/your_link_here")

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ti si PriceBot Srbija — asistent za pronalaženje najjeftinijih cijena u Srbiji.

Prioritet sajtova: cenoteka.rs, ananas.rs, gigatron.rs
Ako ne nađeš na prioritetnim sajtovima, pretraži i druge srpske prodavnice (shoppster.com, tehnomanija.rs, itd.).

Format za svaki rezultat:
🛒 Naziv proizvoda • Sajt: Cena RSD 🔗 Link

Prikaži top 3-5 najjeftinijih opcija sortirano od najjeftinije ka najskupljoj.
Uvijek odgovaraj na srpskom jeziku.
Ako ne možeš naći proizvod, obavijesti korisnika i predloži alternativu."""

# ─── Labele dugmadi (konstante, koriste se za match u message_handleru) ────────

BTN_TRACK   = "🔔 Prati oglas"
BTN_SEARCH  = "🔍 Pretraži cijenu"
BTN_PREMIUM = "💎 Premium"
BTN_HELP    = "ℹ️ Pomoć"
BTN_CANCEL  = "❌ Otkaži"
BTN_AUTO    = "🚗 Auto"
BTN_TEHNIKA = "📱 Tehnika"
BTN_STAN    = "🏠 Stan"
BTN_OSTALO  = "🛍️ Ostalo"

# ─── Tastature ────────────────────────────────────────────────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK),   KeyboardButton(BTN_SEARCH)],
        [KeyboardButton(BTN_PREMIUM), KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

CATEGORY_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_AUTO)],
        [KeyboardButton(BTN_TEHNIKA)],
        [KeyboardButton(BTN_STAN)],
        [KeyboardButton(BTN_OSTALO)],
        [KeyboardButton(BTN_CANCEL)],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

CATEGORY_SITES = {
    BTN_AUTO:    ("🚗 Auto",    "polovniautomobili.com"),
    BTN_TEHNIKA: ("📱 Tehnika", "kupujemprodajem.com"),
    BTN_STAN:    ("🏠 Stan",    "halooglasi.com"),
    BTN_OSTALO:  ("🛍️ Ostalo",  "kupujemprodajem.com"),
}

WELCOME_TEXT = (
    "👋 Dobrodošao u PriceBot Srbija! Pomažem ti da uštediš novac na kupovini!\n\n"
    "🆓 *Besplatno:*\n"
    "• 1 pretraga cijene dnevno (AI)\n"
    "• Praćenje 1 oglasa max 5 dana\n\n"
    "💎 *Premium (3€/mj):*\n"
    "• Neograničene AI pretrage\n"
    "• Neograničeno praćenje oglasa\n"
    "• Praćenje akcija i popusta\n\n"
    "Kako koristiti:\n"
    "• Napiši šta tražiš npr. 'koliko košta iPhone 15'\n"
    "• Ili klikni dugme ispod 👇"
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_ad_query(text: str) -> tuple[str, float | None]:
    """Parsira 'iPhone 17 700€' → ('iPhone 17', 700.0)."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:€|eur|euro)\b", text, re.IGNORECASE)
    if match:
        price = float(match.group(1).replace(",", "."))
        term = text[: match.start()].strip()
        return (term or text), price
    return text, None


async def ask_claude(user_message: str) -> str:
    """Šalje upit Gemini 2.0 Flash-u sa Google Search toolom i vraća odgovor."""
    try:
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": user_message}]
            }],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "tools": [{
                "googleSearch": {}
            }]
        }

        async with httpx.AsyncClient() as httpx_client:
            response = await httpx_client.post(
                f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

        # Ekstrahuj tekst iz odgovora
        if "candidates" in data and data["candidates"]:
            content = data["candidates"][0].get("content", {})
            parts = content.get("parts", [])
            texts = [p.get("text", "") for p in parts if "text" in p]
            return "\n".join(texts) or "Nisam pronašao rezultate."

        return "Nisam pronašao rezultate."

    except Exception as e:
        logger.error(f"Greška pri Gemini pretrazi: {e}")
        raise


async def do_search(update: Update, user_id: int, text: str, is_premium: bool):
    """Izvršava AI pretragu i šalje rezultat korisniku."""
    if not is_premium and not db.can_search(user_id):
        await update.message.reply_text(
            "🚫 Iskoristio si 1 besplatnu pretragu za danas.\n\n"
            f"💎 Nadogradi na *Premium* za neograničene pretrage!\n\n"
            f"👉 [Aktiviraj Premium]({STRIPE_LINK})",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    thinking = await update.message.reply_text("🔍 Pretražujem cijene, molim sačekaj...")

    try:
        if not is_premium:
            db.increment_search(user_id)
        reply = await ask_claude(text)
    except Exception as e:
        if "429" in str(e) or "rate_limit" in str(e).lower():
            reply = "⚠️ Previše zahteva. Pokušaj ponovo za nekoliko sekundi."
        elif "401" in str(e) or "authentication" in str(e).lower():
            reply = "❌ Greška u konfiguraciji API ključa. Kontaktiraj admina."
        else:
            logger.error(f"Gemini greška: {e}")
            reply = "❌ Došlo je do greške pri pretrazi. Pokušaj ponovo."

    await thinking.delete()
    await update.message.reply_text(reply, reply_markup=MAIN_KEYBOARD)


# ─── Command handleri ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.get_or_create_user(user.id, user.username or user.first_name)
    context.user_data.clear()
    await update.message.reply_text(
        WELCOME_TEXT, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD
    )


async def cmd_setpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin komanda: /setpremium {user_id}"""
    if not context.args:
        await update.message.reply_text("Koristi: /setpremium {user_id}")
        return
    try:
        target_id = int(context.args[0])
        db.set_premium(target_id)
        await update.message.reply_text(f"✅ Korisnik {target_id} je sada Premium!")
    except Exception as e:
        await update.message.reply_text(f"❌ Greška: {e}")


# ─── Message handler ──────────────────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    db.get_or_create_user(user.id, user.username or user.first_name)
    user_info = db.get_user(user.id)
    is_premium = user_info["plan"] == "premium"
    state = context.user_data.get("state")

    # ── Otkaži / Nazad ─────────────────────────────────────────────────────────

    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text(
            "↩️ Vraćen si na početak.", reply_markup=MAIN_KEYBOARD
        )
        return

    # ── Prati oglas ────────────────────────────────────────────────────────────

    if text == BTN_TRACK:
        if not is_premium and db.count_user_active_ads(user.id) >= 1:
            await update.message.reply_text(
                "🚫 Besplatni plan dozvoljava praćenje samo *1 oglasa*.\n\n"
                "💎 Nadogradi na *Premium* za neograničeno praćenje!\n\n"
                f"👉 [Aktiviraj Premium]({STRIPE_LINK})",
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        context.user_data["state"] = "select_category"
        await update.message.reply_text(
            "📂 Izaberi kategoriju oglasa:",
            reply_markup=CATEGORY_KEYBOARD,
        )
        return

    # ── Kategorija izabrana ────────────────────────────────────────────────────

    if text in CATEGORY_SITES and state == "select_category":
        emoji_name, site = CATEGORY_SITES[text]
        context.user_data.update({
            "state": "await_ad_query",
            "selected_category": text,
            "selected_site": site,
            "category_name": emoji_name,
        })
        await update.message.reply_text(
            f"*{emoji_name}* → pretraga na `{site}`\n\n"
            "📝 Upiši naziv i maksimalnu cijenu:\n"
            "_Npr: iPhone 17 700€_\n\n"
            "_Ako ne napiješ cijenu, praćenje je bez limita cijene._",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # ── Upit za oglas primljen ─────────────────────────────────────────────────

    if state == "await_ad_query":
        search_term, max_price = parse_ad_query(text)
        site = context.user_data.get("selected_site", "kupujemprodajem.com")
        category = context.user_data.get("selected_category", BTN_OSTALO)

        db.add_tracked_ad(
            user_id=user.id,
            category=category,
            search_term=search_term,
            max_price=max_price,
            site=site,
            is_premium=is_premium,
        )
        context.user_data.clear()

        limit_text = "" if is_premium else "\n⏰ _Praćenje aktivno 5 dana (besplatni plan)_"
        price_text = f" do *{max_price:.0f}€*" if max_price else ""

        await update.message.reply_text(
            f"✅ *Praćenje aktivirano!*\n\n"
            f"📦 {search_term}{price_text}\n"
            f"📍 Sajt: `{site}`\n"
            f"🔄 Provjera svakih 12 sati{limit_text}\n\n"
            f"Obavijestit ću te čim nađem novi oglas! 🔔",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # ── Pretraži cijenu ────────────────────────────────────────────────────────

    if text == BTN_SEARCH:
        if not is_premium and not db.can_search(user.id):
            await update.message.reply_text(
                "🚫 Iskoristio si 1 besplatnu pretragu za danas.\n\n"
                f"💎 Nadogradi na *Premium* za neograničene pretrage!\n\n"
                f"👉 [Aktiviraj Premium]({STRIPE_LINK})",
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        limit_note = "" if is_premium else "\n_Imaš 1 besplatnu pretragu dnevno._"
        context.user_data["state"] = "await_search"
        await update.message.reply_text(
            f"🔍 Šta tražiš? Upiši naziv proizvoda:{limit_note}\n\n"
            "_Npr: iPhone 15 Pro, Samsung TV 55, Nike Air Max..._",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # ── Upit za pretragu primljen ──────────────────────────────────────────────

    if state == "await_search":
        context.user_data.clear()
        await do_search(update, user.id, text, is_premium)
        return

    # ── Premium info ───────────────────────────────────────────────────────────

    if text == BTN_PREMIUM:
        await update.message.reply_text(
            "💎 *Premium plan — 3€/mj*\n\n"
            "✅ Neograničene AI pretrage cijena\n"
            "✅ Neograničeno praćenje oglasa\n"
            "✅ Bez vremenskog limita na praćenje\n"
            "✅ Praćenje akcija i popusta\n\n"
            f"👉 [Aktiviraj Premium]({STRIPE_LINK})",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # ── Pomoć ──────────────────────────────────────────────────────────────────

    if text == BTN_HELP:
        context.user_data.clear()
        await update.message.reply_text(
            WELCOME_TEXT, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD
        )
        return

    # ── Slobodan tekst → AI pretraga ───────────────────────────────────────────

    await do_search(update, user.id, text, is_premium)


# ─── Background job — provjera oglasa svakih 12h ──────────────────────────────

async def check_ads_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Pokrenuta provjera aktivnih oglasa...")
    ads = db.get_all_active_ads()

    for ad in ads:
        try:
            if ad["expires_at"]:
                expires = datetime.fromisoformat(ad["expires_at"])
                if datetime.now() > expires:
                    db.deactivate_ad(ad["id"])
                    await context.bot.send_message(
                        chat_id=ad["user_id"],
                        text=(
                            f"⏰ Praćenje za *{ad['search_term']}* je isteklo!\n\n"
                            "Besplatni plan dozvoljava praćenje max 5 dana.\n\n"
                            f"💎 Nastavi bez limita uz *Premium*!\n\n"
                            f"👉 [Aktiviraj Premium]({STRIPE_LINK})"
                        ),
                        parse_mode="Markdown",
                        reply_markup=MAIN_KEYBOARD,
                    )
                    continue

            results = scraper.scrape_site(ad["site"], ad["search_term"], ad["max_price"])
            known_urls = json.loads(ad.get("known_urls") or "[]")
            new_results = [r for r in results if r["url"] not in known_urls]

            for result in new_results:
                price_str = result.get("price_text") or "Cijena nije navedena"
                await context.bot.send_message(
                    chat_id=ad["user_id"],
                    text=(
                        f"🔔 *Novi oglas — {ad['search_term']}*\n\n"
                        f"📦 {result['title']}\n"
                        f"💰 {price_str}\n"
                        f"📍 {ad['site']}\n"
                        f"🔗 [Pogledaj oglas]({result['url']})"
                    ),
                    parse_mode="Markdown",
                )
                known_urls.append(result["url"])

            db.update_ad_known_urls(ad["id"], known_urls)

        except Exception as e:
            logger.error(f"Greška pri provjeri oglasa #{ad['id']}: {e}")

    logger.info(f"Provjera završena — provjereno {len(ads)} oglasa.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setpremium", cmd_setpremium))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.job_queue.run_repeating(check_ads_job, interval=43200, first=60)

    logger.info("🚀 PriceBot Srbija pokrenut!")
    app.run_polling()


if __name__ == "__main__":
    main()
