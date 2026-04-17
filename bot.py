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

# ─── Auto ključne riječi → PolvniAutomobili
AUTO_KEYWORDS = [
    "golf", "passat", "audi", "bmw", "mercedes", "benz", "opel", "ford",
    "renault", "peugeot", "citroen", "fiat", "toyota", "honda", "mazda",
    "hyundai", "kia", "skoda", "seat", "volvo", "nissan", "mitsubishi",
    "suzuki", "dacia", "alfa", "romeo", "jeep", "land rover", "porsche",
    "volkswagen", "vw", "automobil", "auto prodaja", "godište", "dizel",
    "benzin", "karavan", "kabriolet", "limuzina", "džip", "suv", "motor",
    "motocikl", "skuteri", "kombi", "kamion"
]

# ─── Nekretnine ključne riječi → Halooglasi
REAL_ESTATE_KEYWORDS = [
    "stan", "stanovi", "kuća", "kuca", "garsonjera", "apartman", "lokal",
    "poslovni prostor", "nekretnina", "nekretnine", "iznajmljivanje",
    "izdavanje", "prodaja stana", "kvadrat", "m2", "soba", "podstanar",
    "najam", "kirija", "zemlja", "plac", "vikendica", "garaža", "garaza"
]

SYSTEM_PROMPT_WEBSHOP = """Ti si PriceBot Srbija — asistent za pronalaženje najjeftinijih cijena u NOVIM webshopovima u Srbiji.

TRAŽIŠ SAMO cijene u regularnim webshopovima (Gigatron, Tehnomanija, WinWin, Shoppster, Ananas, Eponuda itd.)
NE tražiš oglase na KupujemProdajem — to je zasebna funkcija.

FORMAT ODGOVORA — STROGO SLIJEDI:

naziv proizvoda
💰 cijena — NazivShopa

(ponovi za svaki rezultat, sortirano od najjeftinije)

ZABRANE — nikad ne krši:
- ZABRANJENO pisati bilo kakve URL-ove, linkove ili href-ove
- ZABRANJENO pisati naziv shopa kao link — ISKLJUČIVO kao obični tekst
- ZABRANJENO izmišljati cijene — koristi SAMO stvarne rezultate iz web search-a
- Ako nisi pronašao rezultate, napiši: Nisam pronašao rezultate u webshopovima.
- Bez uvoda, objašnjenja ili dodatnog teksta — samo lista, ništa više"""


# ─── Labele dugmadi
BTN_TRACK = "🔔 Prati oglas"
BTN_MY_ADS = "⭐ Moji oglasi"
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
        [KeyboardButton(BTN_TRACK), KeyboardButton(BTN_MY_ADS)],
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
    # Use word boundaries for longer keywords, substring for short ones
    for kw in KP_KEYWORDS:
        if len(kw) > 3:
            if re.search(rf'\b{re.escape(kw)}\b', text_lower):
                return True
        else:
            if kw in text_lower:
                return True
    return False


def is_tech_search(text: str) -> bool:
    """Detektuje da li korisnik traži tehničke proizvode."""
    text_lower = text.lower()
    # Use word boundaries for all keywords
    for kw in TECH_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', text_lower):
            return True
    return False


def is_auto_search(text: str) -> bool:
    """Detektuje da li korisnik traži automobil."""
    text_lower = text.lower()
    # Use word boundaries for all keywords to avoid "Motorola" matching "motor"
    for kw in AUTO_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', text_lower):
            return True
    return False


def is_real_estate_search(text: str) -> bool:
    """Detektuje da li korisnik traži nekretninu."""
    text_lower = text.lower()
    # Use word boundaries for all keywords
    for kw in REAL_ESTATE_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', text_lower):
            return True
    return False


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


def format_auto_results(results: list[dict], search_term: str) -> str:
    """Formatuje PolvniAutomobili rezultate."""
    if not results:
        pa_url = f"https://www.polovniautomobili.com/auto-oglasi/pretraga?q={search_term.replace(' ', '+')}"
        return (
            f"❌ Nisam pronašao oglase za *{search_term}* na PolvniAutomobili.\n\n"
            f"🔗 Pretražite ručno: [PolvniAutomobili]({pa_url})"
        )
    lines = [f"🚗 *Rezultati za: {search_term}* (PolvniAutomobili)\n"]
    for i, r in enumerate(results[:5], 1):
        title = r.get("title", "Nepoznat naziv")[:60]
        price_text = r.get("price_text", "Cijena nije navedena")
        url = r.get("url", "")
        if url and not url.startswith("http"):
            url = "https://www.polovniautomobili.com" + url
        if url:
            lines.append(f"{i}. 🚗 [{title}]({url})\n   💰 {price_text}")
        else:
            lines.append(f"{i}. 🚗 {title}\n   💰 {price_text}")
    pa_url = f"https://www.polovniautomobili.com/auto-oglasi/pretraga?q={search_term.replace(' ', '+')}"
    lines.append(f"\n🔍 [Prikaži sve oglase na PolvniAutomobili]({pa_url})")
    return "\n".join(lines)


def format_halooglasi_results(results: list[dict], search_term: str) -> str:
    """Formatuje Halooglasi rezultate."""
    if not results:
        ha_url = f"https://www.halooglasi.com/pretraga?what={search_term.replace(' ', '+')}"
        return (
            f"❌ Nisam pronašao oglase za *{search_term}* na Halooglasi.\n\n"
            f"🔗 Pretražite ručno: [Halooglasi]({ha_url})"
        )
    lines = [f"🏠 *Rezultati za: {search_term}* (Halooglasi)\n"]
    for i, r in enumerate(results[:5], 1):
        title = r.get("title", "Nepoznat naziv")[:60]
        price_text = r.get("price_text", "Cijena nije navedena")
        url = r.get("url", "")
        if url and not url.startswith("http"):
            url = "https://www.halooglasi.com" + url
        if url:
            lines.append(f"{i}. 🏠 [{title}]({url})\n   💰 {price_text}")
        else:
            lines.append(f"{i}. 🏠 {title}\n   💰 {price_text}")
    ha_url = f"https://www.halooglasi.com/pretraga?what={search_term.replace(' ', '+')}"
    lines.append(f"\n🔍 [Prikaži sve oglase na Halooglasi]({ha_url})")
    return "\n".join(lines)


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


def format_combined_results(webshop_results: list[dict], kp_results: list[dict], search_term: str) -> str:
    """
    Formatuje rezultate u dva dijela:
      🏪 Webshop cijene (Google CSE / Eponuda direct)
      🔄 Polovni oglasi (KupujemProdajem)
    """
    q = search_term.replace(' ', '+')
    lines = []

    source_emoji = {
        "Gigatron":   "🔵",
        "Winwin":     "🟢",
        "Tehnomanija":"🟠",
        "Eponuda":    "🟣",
        "Google":     "🔍",
        "Webshop":    "🏪",
    }

    # ── Sekcija 1: Webshop cijene
    lines.append(f"🏪 *Webshop cijene za: {search_term}*\n")
    if webshop_results:
        for i, r in enumerate(webshop_results[:6], 1):
            title = r.get("title", "")[:55]
            price_text = r.get("price_text", "N/A")
            url = r.get("url", "")
            source = r.get("source", "Webshop")
            emoji = source_emoji.get(source, "🏪")
            if url:
                lines.append(f"{i}. {emoji} [{title}]({url})\n   💰 {price_text} — _{source}_")
            else:
                lines.append(f"{i}. {emoji} {title}\n   💰 {price_text} — _{source}_")
    else:
        lines.append("_Nije pronađeno u webshopovima._")

    # Ručni linkovi na Eponuda i Gigatron
    eponuda_url = f"https://www.eponuda.com/uporedicene?ep={q}"
    gigatron_url = f"https://www.gigatron.rs/pretraga?q={q}"
    lines.append(f"\n🔍 Pretraži i na: [Eponuda]({eponuda_url}) • [Gigatron]({gigatron_url})")

    # ── Sekcija 2: Polovni oglasi (KP)
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🔄 *Polovni oglasi (KupujemProdajem):*\n")
    if kp_results:
        for i, r in enumerate(kp_results[:5], 1):
            title = r.get("title", "")[:55]
            price_text = r.get("price_text", "N/A")
            url = r.get("url", "")
            if url and not url.startswith("http"):
                url = "https://www.kupujemprodajem.com" + url
            if url:
                lines.append(f"{i}. 🛒 [{title}]({url})\n   💰 {price_text}")
            else:
                lines.append(f"{i}. 🛒 {title}\n   💰 {price_text}")
        kp_url = f"https://www.kupujemprodajem.com/pretraga?keywords={q}"
        lines.append(f"\n🔍 [Svi oglasi na KP]({kp_url})")
    else:
        kp_url = f"https://www.kupujemprodajem.com/pretraga?keywords={q}"
        lines.append(f"_Nije pronađeno na KP._\n🔍 [Pretraži ručno na KP]({kp_url})")

    return "\n".join(lines)


def format_webshop_results(results: list[dict], search_term: str) -> str:
    """Formatuje samo webshop rezultate (bez KP sekcije) — za fallback."""
    q = search_term.replace(' ', '+')

    if not results:
        return (
            f"❌ Nisam pronašao *{search_term}* u webshopovima.\n\n"
            f"Pretraži ručno:\n"
            f"• [Eponuda](https://www.eponuda.com/uporedicene?ep={q})\n"
            f"• [Gigatron](https://www.gigatron.rs/pretraga?q={q})\n"
            f"• [WinWin](https://www.winwin.rs/catalogsearch/result/?q={q})"
        )

    lines = [f"🏪 *Webshop cijene za: {search_term}*\n"]
    for i, r in enumerate(results[:6], 1):
        title = r.get("title", "")[:55]
        price_text = r.get("price_text", "N/A")
        url = r.get("url", "")
        source = r.get("source", "")
        if url:
            lines.append(f"{i}. 🟢 [{title}]({url})\n   💰 {price_text} — _{source}_")
        else:
            lines.append(f"{i}. 🟢 {title}\n   💰 {price_text} — _{source}_")

    lines.append(
        f"\n🔍 Pretraži i na: "
        f"[Eponuda](https://www.eponuda.com/search/?q={q}) • "
        f"[Gigatron](https://www.gigatron.rs/pretraga?q={q})"
    )
    return "\n".join(lines)


async def _call_gemini_with_search(prompt: str) -> str:
    """Gemini API poziv sa Google Search alatom — bez system prompta."""
    if not GOOGLE_API_KEY:
        return ""
    try:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
        if "candidates" in data and data["candidates"]:
            parts = data["candidates"][0].get("content", {}).get("parts", [])
            return "\n".join(p.get("text", "") for p in parts if "text" in p)
        return ""
    except Exception as e:
        logger.error(f"[GEMINI] _call_gemini_with_search greška: {e}")
        return ""


async def fetch_pa_via_gemini(search_term: str, max_price: float | None = None) -> list[dict]:
    """
    Koristi Gemini + Google Search da pronađe PA oglase.
    Zamjena za scrape_polovniautomobili koji dobiva 403 sa cloud IP-a.
    """
    prompt = (
        f"Pronađi 5 najnovijih oglasa za '{search_term}' na "
        f"polovniautomobili.com. Vrati SAMO oglase koje si "
        f"stvarno vidio u search rezultatima u formatu:\n"
        f"naziv | cijena | URL\n\n"
        f"Ako nisi pronašao oglas na polovniautomobili.com "
        f"— ne izmišljaj, napiši NEMA."
    )
    if max_price:
        prompt += f"\nMaksimalna cijena: {max_price:.0f}€"

    raw = await _call_gemini_with_search(prompt)
    logger.info(f"[PA-GEMINI] Raw odgovor za '{search_term}':\n{raw[:400]}")

    results = []
    for line in raw.strip().splitlines():
        line = line.strip(" -•*")
        if not line or "NEMA" in line.upper():
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        title, price_text, url = parts[0], parts[1], parts[2]
        # Prihvati samo stvarne PA URL-ove
        if "polovniautomobili.com" not in url:
            logger.debug(f"[PA-GEMINI] Skip (nije PA URL): {url}")
            continue
        price = scraper._parse_price(price_text)
        # Filtriraj po max_price ako je zadana
        if max_price and price and price > max_price:
            logger.debug(f"[PA-GEMINI] Skip (cijena {price} > {max_price}): {title}")
            continue
        results.append({"title": title, "price": price, "price_text": price_text, "url": url})

    logger.info(f"[PA-GEMINI] {len(results)} oglasa za '{search_term}'")
    return results


async def ask_gemini_webshop(user_message: str, retry_count: int = 0, max_retries: int = 2) -> str:
    """Šalje upit Gemini-u za webshop cijene (Gigatron, Tehnomanija itd.) sa retry logikom."""
    if not GOOGLE_API_KEY:
        logger.error("❌ GOOGLE_API_KEY nije postavljen!")
        return "❌ Greška u konfiguraciji: GOOGLE_API_KEY nije pronađen.\n\nKontaktiraj admina."

    try:
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": user_message}]
            }],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT_WEBSHOP}]},
            "tools": [{"googleSearch": {}}]
        }

        logger.debug(f"[GEMINI] Slanje zahtjeva na {GEMINI_API_URL} (attempt {retry_count + 1}/{max_retries + 1})")
        async with httpx.AsyncClient() as httpx_client:
            response = await httpx_client.post(
                f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                json=payload,
                timeout=30.0
            )

            # Detaljniji error logging
            if response.status_code == 401:
                logger.error(f"❌ GEMINI 401: Nevaljani API ključ")
                return "❌ Greška: Nevaljani Google API ključ. Kontaktiraj admina."

            elif response.status_code == 429:
                logger.error(f"❌ GEMINI 429: Rate limit (attempt {retry_count + 1})")
                if retry_count < max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** retry_count)  # exponential backoff: 1s, 2s, 4s
                    return await ask_gemini_webshop(user_message, retry_count + 1, max_retries)
                return "⚠️ Previše zahteva. Pokušaj ponovo za nekoliko sekundi."

            elif response.status_code == 503:
                logger.error(f"❌ GEMINI 503: Service Unavailable (attempt {retry_count + 1})")
                if retry_count < max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** retry_count)  # exponential backoff
                    return await ask_gemini_webshop(user_message, retry_count + 1, max_retries)
                return "⚠️ Google Gemini servis je trenutno nedostupan. Pokušaj ponovo za nekoliko minuta."

            elif response.status_code in [500, 502, 504]:
                logger.error(f"❌ GEMINI {response.status_code}: Server error (attempt {retry_count + 1})")
                if retry_count < max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** retry_count)
                    return await ask_gemini_webshop(user_message, retry_count + 1, max_retries)
                return f"⚠️ Google server greška ({response.status_code}). Pokušaj ponovo."

            response.raise_for_status()
            data = response.json()

        if "candidates" in data and data["candidates"]:
            content = data["candidates"][0].get("content", {})
            parts = content.get("parts", [])
            texts = [p.get("text", "") for p in parts if "text" in p]
            result = "\n".join(texts) or "Nisam pronašao rezultate."
            logger.info(f"[GEMINI] ✅ Pronađeni rezultati ({len(texts)} dijelova)")
            return result

        logger.warning(f"[GEMINI] Nema candidates u odgovoru: {data}")
        return "Nisam pronašao rezultate."

    except httpx.TimeoutException:
        logger.error(f"❌ GEMINI Timeout (30s) - attempt {retry_count + 1}")
        if retry_count < max_retries:
            import asyncio
            await asyncio.sleep(2 ** retry_count)
            return await ask_gemini_webshop(user_message, retry_count + 1, max_retries)
        return "⚠️ Zahtjev je trajao previše dugo. Pokušaj ponovo."

    except httpx.HTTPError as e:
        logger.error(f"❌ GEMINI HTTP error: {e}")
        return f"⚠️ Mrežna greška: {str(e)[:100]}"

    except Exception as e:
        logger.error(f"❌ GEMINI Nepoznata greška: {type(e).__name__}: {e}", exc_info=True)
        return f"❌ Greška pri pretrazi: {str(e)[:80]}"


async def do_search(update: Update, user_id: int, text: str, is_premium: bool):
    """
    Glavna search logika:
    - Auto → PolvniAutomobili
    - Nekretnine → Halooglasi
    - Sve ostalo → Kombinirano: WinWin (webshop) + KupujemProdajem (polovno) paralelno
    """
    logger.info(f"[SEARCH] Korisnik {user_id}: '{text}'")

    thinking = await update.message.reply_text("🔍 Pretražujem, molim sačekaj...")

    try:
        if not is_premium:
            db.increment_search(user_id)

        kp_mode = is_kp_search(text)
        tech_mode = is_tech_search(text)
        auto_mode = is_auto_search(text)
        real_estate_mode = is_real_estate_search(text)

        if auto_mode and not kp_mode:
            # ── PolvniAutomobili scraping
            product_name, max_price = parse_ad_query(text)
            search_term = extract_search_term(product_name or text)
            logger.info(f"[SEARCH] AUTO mod | term: '{search_term}' | max_price: {max_price}")
            await thinking.edit_text(f"🚗 Tražim *{search_term}* na PolvniAutomobili...")
            import asyncio
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: scraper.scrape_polovniautomobili(search_term)
            )

            # Ako PolvniAutomobili ne vrati relevantne rezultate, fallback na Gemini
            # (website search je često iritantan i vraća nasumične automobile)
            if not results:
                logger.info("[SEARCH] PolvniAutomobili vratio 0 rezultata, fallback na Gemini")
                await thinking.edit_text("🔍 Pretražujem dostupne oglase...")
                reply = await ask_gemini_webshop(text)
            else:
                reply = format_auto_results(results, search_term)

        elif real_estate_mode and not kp_mode:
            # ── Halooglasi scraping
            product_name, max_price = parse_ad_query(text)
            search_term = extract_search_term(product_name or text)
            logger.info(f"[SEARCH] NEKRETNINE mod | term: '{search_term}' | max_price: {max_price}")
            await thinking.edit_text(f"🏠 Tražim *{search_term}* na Halooglasi...")
            import asyncio
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: scraper.scrape_halooglasi(search_term)
            )

            # Ako HaloOglasi ne vrati relevantne rezultate, fallback na Gemini
            if not results:
                logger.info("[SEARCH] HaloOglasi vratio 0 rezultata, fallback na Gemini")
                await thinking.edit_text("🔍 Pretražujem dostupne oglase...")
                reply = await ask_gemini_webshop(text)
            else:
                reply = format_halooglasi_results(results, search_term)

        else:
            # ── Kombinirano: webshop cijene (WinWin) + polovni oglasi (KP)
            # Vrijedi za: kp_mode, tech_mode i sve ostalo (bez auto/nekretnine)
            product_name, max_price = parse_ad_query(text)
            search_term = extract_search_term(product_name or text)
            logger.info(
                f"[SEARCH] KOMBINIRANO mod | term: '{search_term}' | max_price: {max_price} "
                f"| kp={kp_mode} | tech={tech_mode}"
            )

            await thinking.edit_text(f"🔍 Pretražujem webshopove i KP za *{search_term}*...")

            import asyncio
            # Pokretanje oba scrapers paralelno
            webshop_task = asyncio.get_event_loop().run_in_executor(
                None,
                lambda: scraper.scrape_webshops(search_term, max_price)
            )
            kp_task = asyncio.get_event_loop().run_in_executor(
                None,
                lambda: scraper.scrape_kupujemprodajem(search_term, max_price)
            )
            webshop_results, kp_results = await asyncio.gather(webshop_task, kp_task)

            logger.info(
                f"[SEARCH] Webshop: {len(webshop_results)} | KP: {len(kp_results)}"
            )

            if not webshop_results and not kp_results:
                # Svi scrapers su zakazali (Railway IP blokiran ili CSE greška)
                # → Fallback na Gemini AI koji radi direktno kroz API bez web scrapinga
                logger.info("[SEARCH] Webshop+KP = 0 → Gemini AI fallback")
                await thinking.edit_text(f"🔍 Pretražujem AI za *{search_term}*...")
                gemini_prompt = (
                    f"Pronađi cijene za '{search_term}' u Srbiji.\n\n"
                    f"VAŽNO: Koristi Google Search i navedi SAMO stvarne rezultate koje si našao.\n"
                    f"NIKADA ne izmišljaj linkove — uključi SAMO URL koji si vidio u search rezultatima.\n\n"
                    f"1. Webshop cijene — Gigatron, WinWin, Tehnomanija, Eponuda, Shoppster (novi)\n"
                    f"2. Polovni oglasi — KupujemProdajem (rabljeni)\n\n"
                    f"Format: naziv • cijena u RSD ili EUR • direktan link\n"
                    f"Sortiraj od najjeftinije. Odgovori na srpskom."
                )
                if max_price:
                    gemini_prompt += f"\nMaksimalna cijena: {max_price}€"
                gemini_reply = await ask_gemini_webshop(gemini_prompt)

                import urllib.parse
                q_enc = urllib.parse.quote_plus(search_term)
                cenoteka = f"\n\n🔍 [Pronađi i kupi na Cenoteka](https://www.cenoteka.rs/search?q={q_enc})"
                reply = gemini_reply + cenoteka
            else:
                reply = format_combined_results(webshop_results, kp_results, search_term)

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
            f"🔄 Provjera svakih 10 minuta{limit_text}\n\n"
            f"Obavijestit ću te čim nađem novi oglas! 🔔",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # ── Moji oglasi
    if text == BTN_MY_ADS:
        try:
            # Pokušaj db.get_user_active_ads ako postoji
            if hasattr(db, 'get_user_active_ads'):
                ads = db.get_user_active_ads(user.id)
            else:
                # Fallback — direktan upit u bazu
                conn = db.get_conn()
                cursor = conn.cursor()
                rows = cursor.execute(
                    "SELECT * FROM tracked_ads WHERE user_id=? AND is_active=1",
                    (user.id,)
                ).fetchall()
                conn.close()
                # Konvertuj u dict
                cols = ["id", "user_id", "category", "search_term", "max_price",
                        "site", "is_premium", "known_urls", "is_active", "expires_at", "created_at"]
                ads = [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.error(f"Greška pri dohvatu oglasa: {e}")
            ads = []

        if not ads:
            await update.message.reply_text(
                "⭐ *Moji oglasi*\n\nNemaš aktivnih praćenja.\n\nKlikni *🔔 Prati oglas* da dodaš!",
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD,
            )
            return

        lines = ["⭐ *Tvoja aktivna praćenja:*\n"]
        for i, ad in enumerate(ads, 1):
            price_text = f" do {ad['max_price']:.0f}€" if ad.get("max_price") else ""
            expires = f"\n   ⏰ Ističe: {str(ad.get('expires_at', ''))[:10]}" if ad.get("expires_at") else ""
            lines.append(f"{i}. 📦 *{ad['search_term']}*{price_text}\n   📍 {ad['site']}{expires}")

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
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

            # PA: Gemini + Google Search umjesto scrapera (scraper dobiva 403 sa cloud IP-a)
            if ad["site"] == "polovniautomobili.com":
                results = await fetch_pa_via_gemini(ad["search_term"], ad["max_price"])
            else:
                results = scraper.scrape_site(ad["site"], ad["search_term"], ad["max_price"])

            # CLIENT-SIDE FILTERING: Filter by search term (website search is often unreliable)
            search_term_lower = ad["search_term"].lower()
            search_words = [w.lower() for w in ad["search_term"].split() if len(w) > 2]
            filtered_results = []

            for r in results:
                title_lower = r.get("title", "").lower()

                # Strategy 1: Check if ANY significant word matches
                word_match = all(word in title_lower for word in search_words)
                if not word_match:
                    logger.debug(f"  ⚠️ Filtriran (no words): '{r.get('title', '')}'")
                    continue

                # Strategy 2: If search term contains a MODEL number (e.g., "Golf 5"), check for exact match
                # Skip large numbers (> 9999) — they're likely prices/mileage, not model identifiers
                # e.g. "opel mokka 16000" → ignore 16000; "Golf 5" → require "5" in title
                import re
                all_numbers_in_search = re.findall(r'\d+', search_term_lower)
                model_numbers = [n for n in all_numbers_in_search if int(n) <= 9999]
                if model_numbers:
                    all_numbers_found = True
                    for num in model_numbers:
                        if not re.search(rf'\b{num}\b', title_lower):
                            logger.debug(f"  ⚠️ Filtriran (version): '{r.get('title', '')}' nema '{num}'")
                            all_numbers_found = False
                            break

                    if not all_numbers_found:
                        continue

                # All checks passed
                filtered_results.append(r)

            results = filtered_results

            # Load known URLs with proper error handling
            try:
                known_urls_str = ad.get("known_urls") or "[]"
                known_urls = json.loads(known_urls_str)
                logger.debug(f" 📋 Known URLs: {len(known_urls)} (raw: {known_urls_str[:50]}...)")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f" ⚠️ Greška pri parsiranju known_urls: {e} | Raw: {ad.get('known_urls')}")
                known_urls = []

            new_results = [r for r in results if r["url"] not in known_urls]

            logger.info(f" ✓ Pronađeno {len(results)} oglasa (nakon filtriranja), {len(new_results)} novih")

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

            # Save updated known URLs to database
            try:
                logger.debug(f" 💾 Saving {len(known_urls)} known URLs for ad {ad['id']}")
                db.update_ad_known_urls(ad["id"], known_urls)
                logger.info(f" ✅ Known URLs saved: {len(known_urls)} URLs")
            except Exception as save_err:
                logger.error(f" ❌ Greška pri čuvanju known_urls: {save_err}", exc_info=True)

        except Exception as e:
            logger.error(f"❌ Greška pri provjeri oglasa #{ad['id']}: {e}", exc_info=True)

    logger.info("=" * 80)
    logger.info(f"✅ ZAVRŠENO — Provjereno {total_checked}, pronađeno {total_new_found} novih")


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin debug komanda — testira scrapers direktno sa Railway servera."""
    import asyncio, os, time
    import requests as _req

    await update.message.reply_text("🔧 Testiram scrapers (može trajati ~30s)...")

    lines = ["🔧 *Debug Report*\n"]

    # Env vars
    cse_id  = os.getenv("GOOGLE_CSE_ID", "")
    cse_key = os.getenv("GOOGLE_CSE_API_KEY", "")
    g_key   = os.getenv("GOOGLE_API_KEY", "")
    lines.append(f"*ENV:*")
    lines.append(f"  CSE\\_ID: `{'✅ ' + cse_id[:12] + '…' if cse_id else '❌ nije postavljen'}`")
    lines.append(f"  CSE\\_KEY: `{'✅ ' + cse_key[-8:] if cse_key else '❌ nije postavljen'}`")
    lines.append(f"  GOOGLE\\_KEY: `{'✅ …' + g_key[-8:] if g_key else '❌ nije postavljen'}`\n")

    # ── Test Google CSE (direktno, bez wrapera — da vidimo tačan HTTP status)
    lines.append("*Google CSE (direktan test):*")
    for key_label, key_val in [("CSE\\_KEY", cse_key), ("GOOGLE\\_KEY", g_key)]:
        if not key_val or not cse_id:
            continue
        try:
            t0 = time.time()
            r = _req.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": key_val, "cx": cse_id,
                        "q": "Samsung Galaxy site:gigatron.rs", "num": 1},
                timeout=12,
            )
            elapsed = time.time() - t0
            if r.status_code == 200:
                n = len(r.json().get("items", []))
                lines.append(f"  {key_label}: ✅ HTTP 200, {n} rezultata ({elapsed:.1f}s)")
            else:
                try:
                    err = r.json().get("error", {}).get("message", r.text[:120])
                except Exception:
                    err = r.text[:120]
                lines.append(f"  {key_label}: ❌ HTTP {r.status_code} — `{err[:100]}`")
        except Exception as e:
            lines.append(f"  {key_label}: ❌ `{str(e)[:80]}`")

    # ── Test KP (provjeri HTTP status direktno)
    lines.append("\n*KP direktan HTTP test:*")
    try:
        t0 = time.time()
        r = _req.get(
            "https://www.kupujemprodajem.com/pretraga",
            params={"keywords": "Samsung Galaxy"},
            headers=scraper.get_headers(),
            timeout=15,
            allow_redirects=True,
        )
        elapsed = time.time() - t0
        is_cf = "cloudflare" in r.text.lower() or "cf-ray" in str(r.headers).lower()
        has_articles = "<article" in r.text
        lines.append(
            f"  HTTP {r.status_code} ({elapsed:.1f}s) | "
            f"{'⚠️ Cloudflare' if is_cf else '✅ Nema CF'} | "
            f"{'✅ article tagovi' if has_articles else '❌ nema article tagova'}"
        )
    except Exception as e:
        lines.append(f"  ❌ `{str(e)[:80]}`")

    # ── Test KP scraper
    t0 = time.time()
    try:
        kp_r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: scraper.scrape_kupujemprodajem("Samsung Galaxy A55")
        )
        lines.append(f"*KP scraper:* {'✅ ' + str(len(kp_r)) + ' rezultata' if kp_r else '❌ 0 rezultata'} ({time.time()-t0:.1f}s)")
    except Exception as e:
        lines.append(f"*KP scraper:* ❌ `{str(e)[:60]}`")

    # ── Test WinWin
    t0 = time.time()
    try:
        ww_r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: scraper.scrape_winwin("Samsung Galaxy")
        )
        lines.append(f"*WinWin:* {'✅ ' + str(len(ww_r)) + ' rezultata' if ww_r else '❌ 0 (403?)'} ({time.time()-t0:.1f}s)")
    except Exception as e:
        lines.append(f"*WinWin:* ❌ `{str(e)[:60]}`")

    # ── Test Google CSE (via scraper wrapper)
    if cse_id and (cse_key or g_key):
        t0 = time.time()
        try:
            cse_r = await asyncio.get_event_loop().run_in_executor(
                None, lambda: scraper.google_search_shops("Samsung Galaxy A55")
            )
            lines.append(f"*Google CSE scraper:* {'✅ ' + str(len(cse_r)) + ' rezultata' if cse_r else '❌ 0'} ({time.time()-t0:.1f}s)")
        except Exception as e:
            lines.append(f"*Google CSE scraper:* ❌ `{str(e)[:60]}`")
    else:
        lines.append("*Google CSE scraper:* ⏭ Nije konfigurisan")

    # ── Test Eponuda
    t0 = time.time()
    try:
        ep_r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: scraper.scrape_eponuda("Samsung Galaxy")
        )
        lines.append(f"*Eponuda:* {'✅ ' + str(len(ep_r)) + ' rezultata' if ep_r else '❌ 0 (Cloudflare?)'} ({time.time()-t0:.1f}s)")
    except Exception as e:
        lines.append(f"*Eponuda:* ❌ `{str(e)[:60]}`")

    # ── Test Gemini
    lines.append("\n*Gemini API test:*")
    try:
        t0 = time.time()
        gemini_r = await ask_gemini_webshop("Koliko košta Samsung Galaxy A55 u Srbiji? Daj jednu cijenu.")
        is_ok = len(gemini_r) > 20 and "greška" not in gemini_r.lower()
        lines.append(f"  {'✅' if is_ok else '❌'} ({time.time()-t0:.1f}s): `{gemini_r[:80]}`")
    except Exception as e:
        lines.append(f"  ❌ `{str(e)[:80]}`")

    try:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception:
        # Fallback bez Markdown ako ima special charova
        await update.message.reply_text("\n".join(lines).replace("*", "").replace("`", "").replace("_", ""))


async def cmd_resetcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin komanda: /resetcache — briše known_urls za sve aktivne oglase."""
    try:
        conn = db.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE tracked_ads SET known_urls = '[]'")
        conn.commit()
        rows = cursor.rowcount
        conn.close()
        await update.message.reply_text(f"✅ Cache obrisan za {rows} oglasa.")
    except Exception as e:
        await update.message.reply_text(f"❌ Greška: {e}")


# ─── Main

def main():
    db.init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setpremium", cmd_setpremium))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("resetcache", cmd_resetcache))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.job_queue.run_repeating(check_ads_job, interval=600, first=60)

    logger.info("🚀 PriceBot Srbija pokrenut!")
    app.run_polling()


if __name__ == "__main__":
    main()
