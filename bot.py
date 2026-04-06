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

# ─── KP ključne riječi — ako korisnik pomene ovo, idemo direktno na KP scraping
KP_KEYWORDS = [
    "kupujem prodajem", "kupujemprodajem", "kp", "polovno", "polovni",
    "rabljeno", "second hand", "oglas", "oglasi", "jeftino"
]

# ─── Tehničke kategorije — za KP scraping čak i bez eksplicitnog "kp"
TECH_KEYWORDS = [
    "iphone", "samsung", "xiaomi", "redmi", "poco", "huawei", "oneplus",
    "telefon", "mobitel", "laptop", "notebook", "računar", "kompjuter",
    "monitor", "tv", "televizor", "tablet", "ipad", "airpods", "slušalice",
    "grafička", "gpu", "cpu", "procesor", "ssd", "ram", "memorija",
    "playstation", "xbox", "nintendo", "konzola", "gaming", "kamera",
    "fotoaparat", "punjač", "adapter", "usb", "router", "wifi", "cmf", "nothing",
    "buds", "earbuds", "watch", "smartwatch", "sat"
]

SYSTEM_PROMPT_WEBSHOP = """Ti si PriceBot Srbija — asistent za pronalaženje najjeftinijih cijena u NOVIM webshopovima u Srbiji.

VAŽNO: Tražiš SAMO cijene u regularnim webshopovima (Gigatron, Tehnomanija, Shoppster, Ananas, eModa itd.)
NE tražiš oglase na KupujemProdajem — to je zasebna funkcija.

FORMAT ODGOVORA - STROGO SLIJEDI (svaki rezultat u novom redu):
🏪 Naziv proizvoda • Cijena: XXX RSD • https://direktan-link-na-sajt.rs

PRAVILA:
- Prikaži SAMO 3-5 najjeftinijih opcija
- Sortiraj od najjeftinije ka najskupljoj  
- Samo direktni linkovi na produktne stranice ili pretragu
- Odgovori na srpskom jeziku
- Bez dodatnog teksta, samo lista rezultata"""


# ─── Labele dugmadi
BTN_TRACK = "🔔 Prati oglas"
BTN_SEARCH = "🔍 Pretraži cijenu"
BTN_PREMIUM = "💎 Premium"
BTN_HELP = "ℹ️ Pomoć"
BTN_CANCEL = "❌ Otkaži"
BTN_AUTO = "🚗 Auto"
BTN_TEHNIKA = "📱 Tehnika"
BTN_STAN = "🏠 Stan"
BTN_OSTALO = "🛍️ Ostalo"

# ─── Tastature
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK), KeyboardButton(BTN_SEARCH)],
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
    BTN_AUTO: ("🚗 Auto", "polovniautomobili.com"),
    BTN_TEHNIKA: ("📱 Tehnika", "kupujemprodajem.com"),
    BTN_STAN: ("🏠 Stan", "halooglasi.com"),
    BTN_OSTALO: ("🛍️ Ostalo", "kupujemprodajem.com"),
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
    "• Napiši šta tražiš npr. 'CMF Buds Pro na kupujem prodajem'\n"
    "• Ili samo naziv proizvoda za webshop cijene\n"
    "• Ili klikni dugme ispod 👇"
)


# ─── Helpers

def parse_ad_query(text: str) -> tuple[str, float | None]:
    """Parsira 'Samsung Galaxy A55 400€' → ('Samsung Galaxy A55', 400.0)."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:€|EUR?|EURO|eur|euro)", text, re.IGNORECASE)
    if match:
        try:
            price_text = match.group(1).replace(",", ".")
            price = float(price_text)
            term = text[:match.start()].strip()
            return (term or text), price
        except ValueError:
            return text, None
    return text, None


def is_kp_search(text: str) -> bool:
    """Detektuje da li korisnik traži oglase na KupujemProdajem."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in KP_KEYWORDS)


def is_tech_search(text: str) -> bool:
    """Detektuje da li korisnik traži tehničke proizvode."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in TECH_KEYWORDS)


def extract_search_term(text: str) -> str:
    """Uklanja KP ključne riječi iz upita da dobijemo čist naziv proizvoda."""
    text_clean = text
    remove_phrases = [
        "nadji najpovoljnije na kupujem prodajem",
        "nađi najpovoljnije na kupujem prodajem",
        "na kupujem prodajem",
        "kupujem prodajem",
        "kupujemprodajem",
        "na kp",
        " kp",
        "polovno",
        "rabljeno",
        "najjeftinije",
        "najpovoljnije",
        "nađi",
        "nadji",
        "pronađi",
        "pronadji",
        "koliko košta",
        "koliko kosta",
        "cijena",
        "cena",
        "gdje kupiti",
        "gde kupiti",
    ]
    for phrase in remove_phrases:
        text_clean = re.sub(phrase, "", text_clean, flags=re.IGNORECASE).strip()
    return text_clean.strip()


def format_kp_results(results: list[dict], search_term: str) -> str:
    """Formatuje KP scraping rezultate u lijepu poruku."""
    if not results:
        # Nema rezultata — vrati direktan link na pretragu
        kp_url = f"https://www.kupujemprodajem.com/pretraga?keywords={search_term.replace(' ', '+')}"
        return (
            f"❌ Nisam pronašao direktne rezultate za *{search_term}* na KupujemProdajem.\n\n"
            f"🔗 Pretražite ručno: [KupujemProdajem]({kp_url})"
        )

    lines = [f"🛍️ *Rezultati za: {search_term}* (KupujemProdajem)\n"]
    for i, r in enumerate(results[:5], 1):
        title = r.get("title", "Nepoznat naziv")[:60]
        price_text = r.get("price_text", "Cijena nije navedena")
        url = r.get("url", "")

        # Ako URL nije kompletan, dodaj domenu
        if url and not url.startswith("http"):
            url = "https://www.kupujemprodajem.com" + url

        if url:
            lines.append(f"{i}. 🛒 [{title}]({url})\n   💰 {price_text}")
        else:
            lines.append(f"{i}. 🛒 {title}\n   💰 {price_text}")

    # Dodaj link za širu pretragu
    kp_url = f"https://www.kupujemprodajem.com/pretraga?keywords={search_term.replace(' ', '+')}"
    lines.append(f"\n🔍 [Prikaži sve oglase na KP]({kp_url})")

    return "\n".join(lines)


async def ask_gemini_webshop(user_message: str) -> str:
    """Šalje upit Gemini-u za webshop cijene (Gigatron, Tehnomanija itd.)"""
    try:
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": user_message}]
            }],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT_WEBSHOP}]},
            "tools": [{"googleSearch": {}}]
        }

        async with httpx.AsyncClient() as httpx_client:
            response = await httpx_client.post(
                f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

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
    """
    Glavna search logika:
    - Ako korisnik traži "na kupujem prodajem" ili tehničke proizvode → direktan KP scraping
    - Inače → Gemini webshop pretraga
    """
    logger.info(f"[SEARCH] Korisnik {user_id}: '{text}'")

    thinking = await update.message.reply_text("🔍 Pretražujem, molim sačekaj...")

    try:
        if not is_premium:
            db.increment_search(user_id)

        kp_mode = is_kp_search(text)
        tech_mode = is_tech_search(text)

        if kp_mode or tech_mode:
            # ── Direktan KP scraping
            search_term = extract_search_term(text)
            logger.info(f"[SEARCH] KP mod | term: '{search_term}' | kp_keyword={kp_mode} | tech={tech_mode}")

            await thinking.edit_text(f"🔍 Tražim *{search_term}* na KupujemProdajem...")

            # Scraping u asyncio thread da ne blokira bota
            import asyncio
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: scraper.scrape_kupujemprodajem(search_term)
            )

            reply = format_kp_results(results, search_term)

            # Ako KP ne vrati ništa i nije eksplicitno tražen KP, pokušaj Gemini kao fallback
            if not results and not kp_mode:
                logger.info("[SEARCH] KP vratio 0 rezultata, fallback na Gemini")
                await thinking.edit_text("🔍 Pretražujem webshopove...")
                reply = await ask_gemini_webshop(text)

        else:
            # ── Gemini webshop pretraga
            logger.info(f"[SEARCH] Gemini webshop mod za: '{text}'")
            reply = await ask_gemini_webshop(text)

    except Exception as e:
        logger.error(f"[SEARCH] EXCEPTION: {type(e).__name__}: {e}", exc_info=True)
        if "429" in str(e) or "rate_limit" in str(e).lower():
            reply = "⚠️ Previše zahteva. Pokušaj ponovo za nekoliko sekundi."
        elif "401" in str(e) or "authentication" in str(e).lower():
            reply = "❌ Greška u konfiguraciji API ključa. Kontaktiraj admina."
        else:
            reply = "❌ Došlo je do greške pri pretrazi. Pokušaj ponovo."

    await thinking.delete()
    await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


# ─── Command handleri

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


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin komanda: /stats — prikaži statistiku"""
    try:
        stats = db.get_stats()
        message = (
            "📊 *PriceBot Srbija — Statistika*\n\n"
            f"👥 *Korisnici:*\n"
            f" • Ukupno: {stats['total_users']}\n"
            f" • Free plan: {stats['free_users']}\n"
            f" • Premium plan: {stats['premium_users']}\n\n"
            f"📈 *Aktivnost danas:*\n"
            f" • Aktivnih korisnika: {stats['active_today']}\n"
            f" • Pretraga izvršeno: {stats['searches_today']}"
        )
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Greška pri učitavanju statistike: {e}")


# ─── Message handler

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    db.get_or_create_user(user.id, user.username or user.first_name)
    user_info = db.get_user(user.id)
    is_premium = user_info["plan"] == "premium"
    state = context.user_data.get("state")

    # ── Otkaži / Nazad
    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text(
            "↩️ Vraćen si na početak.", reply_markup=MAIN_KEYBOARD
        )
        return

    # ── Prati oglas
    if text == BTN_TRACK:
        context.user_data["state"] = "select_category"
        await update.message.reply_text(
            "📂 Izaberi kategoriju oglasa:",
            reply_markup=CATEGORY_KEYBOARD,
        )
        return

    # ── Kategorija izabrana
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
            "_Ako ne napišeš cijenu, praćenje je bez limita cijene._",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # ── Upit za oglas primljen
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

    # ── Pretraži cijenu
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
            "_Npr: CMF Buds Pro na kupujem prodajem, iPhone 15, Samsung TV 55..._",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # ── Upit za pretragu primljen
    if state == "await_search":
        context.user_data.clear()
        await do_search(update, user.id, text, is_premium)
        return

    # ── Premium info
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

    # ── Pomoć
    if text == BTN_HELP:
        context.user_data.clear()
        await update.message.reply_text(
            WELCOME_TEXT, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD
        )
        return

    # ── Slobodan tekst → search
    await do_search(update, user.id, text, is_premium)


# ─── Background job — provjera oglasa

async def check_ads_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔍 Pokrenuta provjera aktivnih oglasa...")
    logger.info("=" * 80)

    try:
        conn = db.get_conn()
        cursor = conn.cursor()
        all_ads_raw = cursor.execute("SELECT * FROM tracked_ads").fetchall()
        logger.info(f"📊 UKUPNO OGLASA U BAZI: {len(all_ads_raw)}")
        for row in all_ads_raw:
            logger.info(f" - ID:{row[0]} | User:{row[1]} | Termin:'{row[3]}' | Site:{row[5]} | Active:{row[8]} | Expires:{row[9]}")
        conn.close()
    except Exception as e:
        logger.error(f"❌ Greška pri čitanju baze: {e}")

    ads = db.get_all_active_ads()
    logger.info(f"📊 AKTIVNIH OGLASA: {len(ads)}")

    if not ads:
        logger.warning("⚠️ NEMA AKTIVNIH OGLASA ZA PROVJERU!")
        logger.info("=" * 80)
        return

    total_checked = 0
    total_new_found = 0

    for ad in ads:
        total_checked += 1
        logger.info(f"\n📌 PROVJERA OGLASA #{ad['id']} | {ad['search_term']} | {ad['site']}")

        try:
            if ad["expires_at"]:
                expires = datetime.fromisoformat(ad["expires_at"])
                if datetime.now() > expires:
                    db.deactivate_ad(ad["id"])
                    logger.warning(f"⏰ OGLAS ISTEKAO! Deaktiviram...")
                    try:
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
                    except Exception as send_err:
                        logger.error(f"❌ Greška pri slanju notifikacije o isteku: {send_err}")
                    continue

            results = scraper.scrape_site(ad["site"], ad["search_term"], ad["max_price"])
            known_urls = json.loads(ad.get("known_urls") or "[]")
            new_results = [r for r in results if r["url"] not in known_urls]

            logger.info(f" ✓ Pronađeno {len(results)} oglasa, {len(new_results)} novih")

            for result in new_results:
                price_str = result.get("price_text") or "Cijena nije navedena"
                total_new_found += 1
                try:
                    msg = (
                        f"🔔 *Novi oglas — {ad['search_term']}*\n\n"
                        f"📦 {result['title']}\n"
                        f"💰 {price_str}\n"
                        f"📍 {ad['site']}\n"
                        f"🔗 [Pogledaj oglas]({result['url']})"
                    )
                    await context.bot.send_message(
                        chat_id=ad["user_id"],
                        text=msg,
                        parse_mode="Markdown",
                    )
                    logger.info(f" ✅ Notifikacija poslana: {result['title'][:40]}...")
                except Exception as send_err:
                    logger.error(f" ❌ Greška pri slanju: {send_err}")

                known_urls.append(result["url"])

            db.update_ad_known_urls(ad["id"], known_urls)

        except Exception as e:
            logger.error(f"❌ Greška pri provjeri oglasa #{ad['id']}: {e}", exc_info=True)

    logger.info("=" * 80)
    logger.info(f"✅ ZAVRŠENO — Provjereno {total_checked}, pronađeno {total_new_found} novih")


# ─── Main

def main():
    db.init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setpremium", cmd_setpremium))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.job_queue.run_repeating(check_ads_job, interval=600, first=60)

    logger.info("🚀 PriceBot Srbija pokrenut!")
    app.run_polling()


if __name__ == "__main__":
    main()
