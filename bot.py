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

PRIORITETNI SAJTOVI PO KATEGORIJI:
📱 Tehnika, računari, mobilni, elektronika → KupujemProdajem.com (prvi izbor)
👕 Odjeća, obuća, moda → KupujemProdajem.com (prvi izbor)
🛍️ Ostalo (nije hrana) → KupujemProdajem.com (prvi izbor)
🏪 Hrana, kozmetika, domaće → Cenoteka.rs (prvi izbor)

FORMAT ODGOVORA - STROGO SLIJEDI:
🛒 Naziv proizvoda • Cijena: XXX RSD • KP: https://www.kupujemprodajem.com/pretraga?keywords=NAZIV+PROIZVODA

GDJE:
- Zamijeni NAZIV sa nazivom proizvoda
- Zamijeni XXX sa cijenom
- Zamijeni razmake sa + u URL-u
- KP = KupujemProdajem (za tehniku, odjeću, ostalo)
- KE = Cenoteka (samo ako nema KP)

PRIMJERI TOČNOG FORMATA:
✅ 🛒 iPhone 15 Pro • Cijena: 1200 RSD • KP: https://www.kupujemprodajem.com/pretraga?keywords=iPhone+15+Pro
✅ 🛒 Samsung TV 55" • Cijena: 450 RSD • KP: https://www.kupujemprodajem.com/pretraga?keywords=Samsung+TV+55
✅ 🛒 Kruh • Cijena: 150 RSD • KE: https://www.cenoteka.rs/pretraga?q=Kruh

PRAVILA:
- Prikaži SAMO 3-5 najjeftinijih opcija
- Sortiraj od najjeftinije ka najskupljoj
- Ako proizvod nije dostupan, predloži sličan proizvod (npr iPhone 15 ako nema iPhone 17)
- Uključi [KP] ili [KE] u formatu TOČNO kako je navedeno
- Odgovori na srpskom jeziku"""

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
    """Parsira 'Samsung Galaxy A55 400€' → ('Samsung Galaxy A55', 400.0)."""
    # Pronađi cijenu sa € symbol ili eur/euro
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:€|EUR?|EURO|eur|euro)", text, re.IGNORECASE)

    if match:
        try:
            # Parsira broj (zamijeni zarez sa točkom ako je decimalni separator)
            price_text = match.group(1).replace(",", ".")
            price = float(price_text)
            # Izvuče termin (sve prije cijene)
            term = text[:match.start()].strip()
            return (term or text), price
        except ValueError:
            return text, None

    return text, None


def convert_to_smart_links(response: str) -> str:
    """Konvertuj linkove u Cenoteka/KupujemProdajem search linkove ovisno o tipu."""
    lines = response.split("\n")
    result = []

    for line in lines:
        # Pronađi proizvod — različiti formati
        # Format 1: 🛒 Proizvod • Cijena: ...
        # Format 2: 🛒 Proizvod • KP: https://...
        # Format 3: * 🛒 Proizvod • KP: https://...
        match = re.search(r"🛒\s*([^•\n]+?)\s*•", line)
        if match:
            product_name = match.group(1).strip()
            search_query = product_name.replace(" ", "+")

            # Detektuj tip proizvoda iz naziva
            product_lower = product_name.lower()
            is_tech = any(word in product_lower for word in [
                "iphone", "samsung", "telefon", "mobilni", "laptop", "računar",
                "monitor", "kamera", "tablet", "apple", "huawei", "redmi",
                "poco", "oneplus", "elektronika", "usb", "adapter", "slušalice",
                "tv", "tv)", "proc", "gpu", "ssd", "ram", "gaming"
            ])
            is_clothing = any(word in product_lower for word in [
                "majica", "trousers", "pants", "odjeća", "obuća", "cipele",
                "patike", "jakna", "košulja", "haljina", "razuva", "glava", "ženske", "muške"
            ])

            # Odaberi prioritetni link ovisno o tipu
            if is_tech or is_clothing:
                # Za tehniku i odjeću preferira KupujemProdajem
                link_text = f"🔗 KP: https://www.kupujemprodajem.com/pretraga?keywords={search_query}"
            else:
                # Za ostalo (hrana, kozmetika, itd) preferira Cenoteka
                link_text = f"🔗 KE: https://www.cenoteka.rs/pretraga?q={search_query}"

            # Zamijeni sve vrste linkova sa novim (KE, KP, 🔗, ili bare https://)
            new_line = re.sub(
                r"(•\s*)?(?:KE|KP)?\s*:\s*https?://[^\s\n]+(?:\s*\|\s*(?:KE|KP)?\s*:\s*https?://[^\s\n]+)*",
                link_text,
                line
            )
            # Ako nema linkova, dodaj nakon proizvoda
            if "http" not in new_line:
                new_line = re.sub(
                    r"(🛒\s*[^•]+\s*•)",
                    f"\\1 {link_text}",
                    new_line
                )
            result.append(new_line)
        else:
            result.append(line)

    return "\n".join(result)


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
            response_text = "\n".join(texts) or "Nisam pronašao rezultate."

            # Konvertuj linkove sa inteligentnom detekcijom Cenoteka/KupujemProdajem
            response_text = convert_to_smart_links(response_text)
            return response_text

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


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin komanda: /stats — prikaži statistiku"""
    try:
        stats = db.get_stats()
        message = (
            "📊 *PriceBot Srbija — Statistika*\n\n"
            f"👥 *Korisnici:*\n"
            f"  • Ukupno: {stats['total_users']}\n"
            f"  • Free plan: {stats['free_users']}\n"
            f"  • Premium plan: {stats['premium_users']}\n\n"
            f"📈 *Aktivnost danas:*\n"
            f"  • Aktivnih korisnika: {stats['active_today']}\n"
            f"  • Pretraga izvršeno: {stats['searches_today']}"
        )
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Greška pri učitavanju statistike: {e}")


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


# ─── Background job — provjera oglasa svakih 1h ───────────────────────────────

async def check_ads_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔍 Pokrenuta provjera aktivnih oglasa...")
    logger.info("=" * 80)

    # Debug: Provjeri sve oglase u bazi (ne samo aktivne)
    try:
        conn = db.get_conn()
        cursor = conn.cursor()
        all_ads_raw = cursor.execute("SELECT * FROM tracked_ads").fetchall()
        logger.info(f"📊 UKUPNO OGLASA U BAZI: {len(all_ads_raw)}")
        for row in all_ads_raw:
            logger.info(f"  - ID:{row[0]} | User:{row[1]} | Termin:'{row[3]}' | Site:{row[5]} | Active:{row[8]} | Expires:{row[9]}")
        conn.close()
    except Exception as e:
        logger.error(f"❌ Greška pri čitanju baze: {e}")

    # Dobij samo aktivne oglase
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
        logger.info(f"\n📌 PROVJERA OGLASA #{ad['id']}")
        logger.info(f"  Korisnik: {ad['user_id']}")
        logger.info(f"  Termin: {ad['search_term']}")
        logger.info(f"  Sajt: {ad['site']}")
        logger.info(f"  Max cijena: {ad.get('max_price')}")
        logger.info(f"  Istekao: {ad['expires_at']}")

        try:
            # Provjeri je li oglasu isteklo vrijeme praćenja
            if ad["expires_at"]:
                expires = datetime.fromisoformat(ad["expires_at"])
                if datetime.now() > expires:
                    db.deactivate_ad(ad["id"])
                    logger.warning(f"⏰ OGLAS ISTEKAO! Deaktiviram i šaljem notifikaciju...")
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
                        logger.info("✅ Notifikacija o isteku poslana!")
                    except Exception as send_err:
                        logger.error(f"❌ Greška pri slanju notifikacije o isteku: {send_err}")
                    continue

            # Pretraga oglasa na sajtu
            logger.info(f"🔍 Skrapiram '{ad['search_term']}' sa {ad['site']}...")
            results = scraper.scrape_site(ad["site"], ad["search_term"], ad["max_price"])
            logger.info(f"  ✓ Pronađeno {len(results)} oglasa sa scrapinga")

            known_urls = json.loads(ad.get("known_urls") or "[]")
            new_results = [r for r in results if r["url"] not in known_urls]
            logger.info(f"  ✓ Od toga {len(new_results)} su NOVI (nepoznati)")

            # Logiranje rezultata pretrage
            max_price_info = f" (max {ad['max_price']:.0f}€)" if ad["max_price"] else ""
            logger.info(
                f"📊 REZULTAT: '{ad['search_term']}{max_price_info}' na {ad['site']} — "
                f"Pronađeno {len(results)} oglasa, {len(new_results)} novih"
            )

            # Logiranje pojedinih oglasa
            for i, result in enumerate(results, 1):
                price = result.get("price")
                price_str = result.get("price_text", "Cijena nije navedena")
                is_new = result["url"] not in known_urls
                is_under_limit = ad["max_price"] is None or price is None or price <= ad["max_price"]

                status = "✅ NOVO" if is_new else "📌 Staro"
                price_status = "✓ U limitu" if is_under_limit else "✗ Iznad limita"

                logger.info(
                    f"  {i}. {status} | {result['title'][:50]}... | "
                    f"{price_str} | {price_status}"
                )

            # Slanje notifikacija za nove oglase
            logger.info(f"📨 Slanje {len(new_results)} notifikacija...")
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
                    logger.info(f"  ✅ Notifikacija poslana za: {result['title'][:40]}...")
                except Exception as send_err:
                    logger.error(f"  ❌ Greška pri slanju notifikacije: {send_err}")

                known_urls.append(result["url"])

            db.update_ad_known_urls(ad["id"], known_urls)
            logger.info(f"✅ Ažurirao known_urls ({len(known_urls)} url-ova)")

        except Exception as e:
            logger.error(f"❌ Greška pri provjeri oglasa #{ad['id']}: {e}", exc_info=True)

    logger.info("=" * 80)
    logger.info(
        f"✅ PROVJERA ZAVRŠENA — Provjereno {total_checked} oglasa, "
        f"pronađeno {total_new_found} novih oglasa."
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setpremium", cmd_setpremium))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.job_queue.run_repeating(check_ads_job, interval=600, first=60)  # 10 minuta za testing

    logger.info("🚀 PriceBot Srbija pokrenut!")
    app.run_polling()


if __name__ == "__main__":
    main()
