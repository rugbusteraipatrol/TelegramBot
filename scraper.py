"""
Web scraper za praćenje oglasa na srpskim sajtovima.

Podržani sajtovi:
  - polovniautomobili.com  (automobili)
  - kupujemprodajem.com    (tehnika, ostalo)
  - halooglasi.com         (nekretnine)

NAPOMENA: CSS selektori se mogu promijeniti ako sajt ažurira dizajn.
Ako scraping prestane raditi, ažurirajte selektore u odgovarajućoj funkciji.
"""

import re
import logging
import requests
from bs4 import BeautifulSoup
import time
import random

logger = logging.getLogger(__name__)

# Diversos User-Agents da izbjegnemo bot detektovanje
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def get_headers():
    """Vraća random User-Agent da izbjegnemo blokade."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "sr-RS,sr;q=0.9,en-US;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }

TIMEOUT = 20  # Povećan timeout


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None) -> BeautifulSoup | None:
    """Fetch URL sa retry logikom i random delay."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Dodaj random delay (0.5-2s) da izbjegnemo throttling
            time.sleep(random.uniform(0.5, 2.0))

            resp = requests.get(url, params=params, headers=get_headers(), timeout=TIMEOUT)
            resp.raise_for_status()

            if resp.text and len(resp.text) > 100:  # Provjeri da li je odgovor validan
                return BeautifulSoup(resp.text, "html.parser")
            else:
                logger.warning(f"⚠️ HTTP: Prazan ili minimalan odgovor od {url}")
                if attempt < max_retries - 1:
                    time.sleep(2)  # Čekaj prije retry
                    continue
                return None

        except requests.exceptions.Timeout:
            logger.warning(f"⏱️ Timeout [{url}] pokušaj {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                time.sleep(3)
        except requests.exceptions.ConnectionError:
            logger.warning(f"🔌 Greška pri konekciji [{url}] pokušaj {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                time.sleep(3)
        except Exception as e:
            logger.error(f"❌ HTTP greška [{url}]: {e}")
            return None

    return None


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    # Ukloni sve osim cifara, zareza i tačaka
    clean = re.sub(r"[^\d,.]", "", text.strip())
    # Normalizuj separator (1.234,56 → 1234.56)
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def _matches_price(price: float | None, max_price: float | None) -> bool:
    if max_price is None:
        return True
    if price is None:
        return True  # Nema cijene → uključi u rezultate
    return price <= max_price


# ─── Scrapers ─────────────────────────────────────────────────────────────────

def scrape_polovniautomobili(search_term: str, max_price: float | None = None) -> list[dict]:
    results = []
    url = "https://www.polovniautomobili.com/auto-oglasi/pretraga"

    # NOTE: Server search is not reliable - it returns random vehicles
    # We'll fetch results and do basic filtering
    params = {
        "sort": "renewDate",
        **({"price_to": int(max_price)} if max_price else {}),
    }
    logger.info(f"🔗 PA Scraping: {url} | search='{search_term}' | max_price={max_price}")

    soup = _get(url, params=params)
    if not soup:
        logger.error(f"❌ PA: Nisu mogli dobiti soup za '{search_term}'")
        return results

    # Pronađi sve article tagove (nova struktura)
    items = soup.find_all("article")
    logger.info(f"📍 PA: Pronađenih {len(items)} oglasa")

    for item in items[:20]:  # Get up to 20 results
        try:
            # Nova struktura: h2 > a za naslov
            title_el = item.select_one("h2 a")
            if not title_el:
                title_el = item.select_one("h3 a, .entity-title a, .classified-title a")

            # Nova struktura: div.price > span za cijenu
            price_el = item.select_one("div.price span")
            if not price_el:
                price_el = item.select_one(".price-box strong, .price-box .price, .entity-price")

            if not title_el or not price_el:
                logger.debug("⚠️ PA: Title ili price element nije pronađen, skipam")
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.polovniautomobili.com" + href

            price_text = price_el.get_text(strip=True)
            price = _parse_price(price_text)

            if not price:
                logger.debug(f"⚠️ PA: Nije moguće parsirati cijenu '{price_text}', skipam")
                continue

            if not _matches_price(price, max_price):
                logger.debug(f"⚠️ PA: Cijena {price} iznad limita {max_price}, skipam")
                continue

            # SOFT FILTERING: Prefer results that contain search term, but don't exclude others
            title_lower = title.lower()
            contains_term = search_term.lower() in title_lower

            if contains_term:
                logger.debug(f"  ✓ PA (match): {title[:40]}... | {price_text}")
            else:
                logger.debug(f"  ~ PA (fallback): {title[:40]}... | {price_text}")

            results.append({"title": title, "price": price, "price_text": price_text, "url": href})

        except Exception as e:
            logger.debug(f"⚠️ PA: Greška pri parsiranju: {e}")
            continue

    logger.info(f"✅ PA: Pronađeno {len(results)} vozila")
    return results


def scrape_kupujemprodajem(search_term: str, max_price: float | None = None) -> list[dict]:
    results = []
    url = "https://www.kupujemprodajem.com/pretraga"

    # Za mobilne telefone: postavi minimum cijenu (izbegni dijelove i dodatnu opremu)
    # 80€ je dovoljno da filtrira dijelove (20-40€) a da uključi rabljene telefone (50-100€)
    min_price = 80 if "iphone" in search_term.lower() or "telefon" in search_term.lower() or "samsung" in search_term.lower() or "galaxy" in search_term.lower() else 10

    params = {
        "keywords": search_term,
        "currency": "eur",
        **({"priceTo": int(max_price)} if max_price else {}),
    }
    logger.info(f"🔗 KP Scraping: {url} | keywords='{search_term}' | max_price={max_price} | min_price={min_price}")

    soup = _get(url, params=params)
    if not soup:
        logger.error(f"❌ KP: Nisu mogli dobiti soup za '{search_term}'")
        return results

    # Prvo pokušaj sa article tagom (nova struktura KupujemProdajem)
    items = soup.find_all("article")
    if not items:
        # Fallback na stare selektore ako article ne radi
        items = soup.select("div.offer-item, article.offer-item, div.kp-ad-list-item, li.offer-list-item")

    logger.info(f"📍 KP: Pronađenih {len(items)} itemova")

    for item in items[:12]:
        try:
            # Nova KP struktura: AdItem_adHolder sa AdItem_price__VZ_at
            title_el = None
            price_el = None

            # Pronađi prvi link koji ima dovolno dugačak tekst (naslov)
            links = item.find_all("a", href=True)
            for link in links:
                text = link.get_text(strip=True)
                if len(text) > 10:  # Naslov mora biti duži od 10 karaktera
                    title_el = link
                    break

            # Pronađi cijenu sa novim selectorom
            price_el = item.select_one("div.AdItem_price__VZ_at, div.AdItem_priceHolder__yVMOe")

            # Fallback na stare selektore ako novi ne rade
            if not title_el:
                title_el = item.find("a", href=True) or item.select_one(
                    "h3 a, h2 a, .offer-title a, .kp-ad-name a"
                )
            if not price_el:
                price_el = item.select_one(
                    "span.price, .price-box, .offer-price, .kp-ad-price"
                )

            if not title_el or not price_el:
                logger.debug("⚠️ KP: Title ili price element nije pronađen, skipam item")
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.kupujemprodajem.com" + href

            price_text = price_el.get_text(strip=True)
            price = _parse_price(price_text)

            if not price:  # Ako nema cijene, skipuj
                logger.debug(f"⚠️ KP: Nije moguće parsirati cijenu '{price_text}', skipam")
                continue

            # Filtriera po min i max cijeni
            if price < min_price:
                logger.debug(f"⚠️ KP: Cijena {price} ispod minimuma {min_price}, skipam")
                continue

            if not _matches_price(price, max_price):
                logger.debug(f"⚠️ KP: Cijena {price} iznad limita {max_price}, skipam")
                continue

            # Filtriera dijelove i dodatnu opremu - isključi ako naslov sadrži ključne riječi
            excluded_keywords = [
                "case", "maska", "zaštita", "punjač", "kabel", "film", "zaštitni",
                "dijelovi", "delovi", "dijelove", "akcesori", "oprema", "dodaci",
                "screen protector", "tempered glass", "adapter", "zamjena", "ekrana",
                "cover", "silicone", "clear", "wallet", "folio", "flip", "tempered"
            ]
            title_lower = title.lower()
            if any(keyword in title_lower for keyword in excluded_keywords):
                logger.debug(f"⚠️ KP: '{title}' je dijelovi/oprema, skipam")
                continue

            result = {"title": title, "price": price, "price_text": price_text, "url": href}
            results.append(result)
            logger.debug(f"  ✓ KP: {title[:40]}... | {price_text}")
        except Exception as e:
            logger.debug(f"⚠️ KP: Greška pri parsiranju: {e}")
            continue

    logger.info(f"✅ KP: Završio scraping sa {len(results)} rezultata")
    return results


def scrape_halooglasi(search_term: str, max_price: float | None = None) -> list[dict]:
    results = []
    soup = _get(
        "https://www.halooglasi.com/pretraga",
        params={
            "what": search_term,
            "sort": "ValidFromField desc",
            **({"cena_d_to": int(max_price)} if max_price else {}),
        },
    )
    if not soup:
        return results

    for item in soup.select(
        "div.product-item, div.classified-item, li.product-item"
    )[:12]:
        try:
            title_el = item.select_one(
                "h3 a, .product-title a, .classified-title a"
            )
            price_el = item.select_one(
                ".price-box, .cena, .product-price, .classified-price"
            )

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.halooglasi.com" + href

            price_text = price_el.get_text(strip=True) if price_el else ""
            price = _parse_price(price_text)

            if not _matches_price(price, max_price):
                continue

            results.append({"title": title, "price": price, "price_text": price_text, "url": href})
        except Exception:
            continue

    return results


# ─── Webshop Scrapers ─────────────────────────────────────────────────────────

# Source emoji mapa
SOURCE_EMOJI = {
    "Gigatron": "🔵",
    "WinWin": "🟢",
    "Tehnomanija": "🟠",
    "Eponuda": "🟣",
    "Google": "🔍",
}


def _extract_price_from_text(text: str) -> float | None:
    """Pokušava da izvuče cijenu iz teksta (snippet, naslov itd.)."""
    if not text:
        return None
    # Npr: "14.999,00 din", "9.990 RSD", "€149", "149 EUR"
    patterns = [
        r"(\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{1,2})?)\s*(?:din|rsd|dinara)",
        r"(\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{1,2})?)\s*(?:€|eur|euro)",
        r"(?:od|cena|cijena|price)[:\s]+(\d[\d.,\s]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            price = _parse_price(m.group(1))
            if price and price > 100:  # ignoriši sitne vrijednosti
                return price
    return None


def scrape_eponuda(search_term: str, max_price: float | None = None) -> list[dict]:
    """
    Scrapa Eponuda.rs koristeći cloudscraper (Cloudflare bypass).
    URL: https://www.eponuda.com/uporedicene?ep=UPIT

    Potvrđeni CSS selektori (2024):
      Container : div.b-paging-product--vertical
      Title     : h3.l3-product-title
      Link      : a[href] (prvi u kontejneru koji vodi na product stranicu)
      Price     : span.b-paging-product__price  (attr event-viewitem-price)
    """
    try:
        import cloudscraper as cs_lib
    except ImportError:
        logger.warning("[EPONUDA] cloudscraper nije instaliran — pip install cloudscraper")
        return []

    results = []
    logger.info(f"[EPONUDA] Scraping: '{search_term}'")

    try:
        cs = cs_lib.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        # Warm-up session (get cookies)
        cs.get("https://www.eponuda.com", timeout=15)
        time.sleep(random.uniform(0.5, 1.2))

        url = "https://www.eponuda.com/uporedicene"
        resp = cs.get(url, params={"ep": search_term}, timeout=25)
        if resp.status_code != 200:
            logger.warning(f"[EPONUDA] Status {resp.status_code}")
            return results

        soup = BeautifulSoup(resp.text, "html.parser")

        # Potvrđeni selektor za product kontejnere
        items = soup.select("div.b-paging-product--vertical")
        logger.info(f"[EPONUDA] Pronađeno {len(items)} product kontejnera")

        seen_urls = set()
        brand = search_term.split()[0].lower() if search_term else ""

        for item in items[:20]:
            try:
                # ── Naslov: h3.l3-product-title
                title_el = item.select_one("h3.l3-product-title")
                if not title_el:
                    continue
                title = title_el.get_text(" ", strip=True)
                if not title or len(title) < 5:
                    continue

                # Brand filter
                if brand and brand not in title.lower():
                    logger.debug(f"[EPONUDA] Skip (brand filter): '{title[:40]}'")
                    continue

                # ── URL: prvi link koji vodi na product stranicu (ne sliku)
                href = ""
                for a in item.select("a[href]"):
                    h = a.get("href", "")
                    if h and not h.startswith(("http", "#", "javascript")):
                        href = "https://www.eponuda.com" + h
                        break
                    elif h and "eponuda.com" in h:
                        href = h
                        break

                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # ── Cijena: span.b-paging-product__price
                # Koristimo data atribut za čistu numeričku vrijednost
                price_el = item.select_one("span.b-paging-product__price")
                price = None
                price_text = "Vidi na Eponuda"
                if price_el:
                    # event-viewitem-price="14370.00" — najčistiji izvor cijene
                    raw_attr = price_el.get("event-viewitem-price", "")
                    if raw_attr:
                        try:
                            price = float(raw_attr)
                            price_text = f"{price:,.0f} din"
                        except ValueError:
                            pass

                    if not price:
                        # Fallback: tekst unutar <b> taga (npr. "14.370,00 din")
                        b_el = price_el.select_one("b")
                        raw_text = b_el.get_text(strip=True) if b_el else price_el.get_text(strip=True)
                        price = _parse_price(raw_text)
                        price_text = raw_text if raw_text else price_text

                # ── Filter po max_price (Eponuda = RSD, ~117 RSD = 1 EUR)
                if max_price and price and price > max_price * 120:
                    logger.debug(f"[EPONUDA] Skip (cijena {price:.0f} > {max_price*120:.0f}): '{title[:30]}'")
                    continue

                results.append({
                    "title": title,
                    "price": price,
                    "price_text": price_text,
                    "url": href,
                    "source": "Eponuda",
                })
                logger.debug(f"[EPONUDA] ✓ {title[:45]} | {price_text}")

            except Exception as e:
                logger.debug(f"[EPONUDA] Parse error: {e}")

    except Exception as e:
        logger.error(f"[EPONUDA] Greška: {e}")

    results.sort(key=lambda r: (r["price"] is None, r["price"] or 0))
    logger.info(f"[EPONUDA] {len(results)} rezultata za '{search_term}'")
    return results


def google_search_shops(search_term: str, max_price: float | None = None) -> list[dict]:
    """
    Google Custom Search JSON API — pretražuje srpske webshopove.
    Zahtijeva GOOGLE_API_KEY i GOOGLE_CSE_ID u .env fajlu.

    Setup:
      1. Idi na https://programmablesearchengine.google.com/
      2. Kreiraj novi search engine (Pretraži cijeli web)
      3. Kopiraj Search engine ID u GOOGLE_CSE_ID
    """
    import os

    api_key = os.getenv("GOOGLE_API_KEY")
    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()

    if not api_key or not cse_id:
        logger.warning("[GOOGLE_CSE] GOOGLE_CSE_ID nije postavljen u .env")
        return []

    # Ograniči pretragu na poznate srpske shopove
    sites = (
        "site:gigatron.rs OR site:winwin.rs OR "
        "site:tehnomanija.rs OR site:eponuda.com"
    )
    query = f"{search_term} {sites}"

    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": 10,
        "gl": "rs",         # Geografija: Srbija
        "hl": "sr",         # Jezik: srpski
    }

    logger.info(f"[GOOGLE_CSE] Pretraga: '{search_term}'")

    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=15,
        )

        if resp.status_code == 429:
            logger.error("[GOOGLE_CSE] Dnevni limit prekoračen (100 besplatnih/dan)")
            return []
        if resp.status_code == 400:
            logger.error(f"[GOOGLE_CSE] Bad request — provjeri GOOGLE_CSE_ID: {resp.text[:200]}")
            return []
        resp.raise_for_status()
        data = resp.json()

    except Exception as e:
        logger.error(f"[GOOGLE_CSE] Greška pri zahtjevu: {e}")
        return []

    results = []
    for item in data.get("items", []):
        title = item.get("title", "").strip()
        link  = item.get("link", "").strip()
        snippet = item.get("snippet", "").replace("\n", " ").strip()
        pagemap = item.get("pagemap", {})

        # ── Cijena iz strukturiranih podataka (pagemap)
        price = None
        price_text = ""
        for offer_key in ("offer", "product"):
            for offer in pagemap.get(offer_key, [])[:1]:
                raw = offer.get("price") or offer.get("lowprice") or ""
                if raw:
                    price = _parse_price(str(raw))
                    price_text = str(raw)
                    break
            if price:
                break

        # ── Fallback: izvuci iz snippet-a / naslova
        if not price:
            price = _extract_price_from_text(snippet + " " + title)
            if price:
                price_text = f"{price:,.0f} RSD"

        if not price_text:
            # Nema cijene — prikaži snippet kao opis
            price_text = snippet[:90] + "…" if len(snippet) > 90 else snippet

        # ── Filtriraj po max_price (RSD; gruba konverzija 120 RSD = 1 EUR)
        if max_price and price and price > max_price * 120:
            logger.debug(f"[GOOGLE_CSE] Cijena {price} RSD iznad limita, skip")
            continue

        # ── Odredi izvor
        source = "Webshop"
        for shop in ("gigatron.rs", "winwin.rs", "tehnomanija.rs", "eponuda.com"):
            if shop in link:
                source = shop.split(".")[0].capitalize()
                break

        # ── Brand filter: prva riječ upita mora biti u naslovu
        brand = search_term.split()[0].lower() if search_term else ""
        if brand and brand not in title.lower() and brand not in snippet.lower():
            logger.debug(f"[GOOGLE_CSE] Brand filter skip: '{title[:50]}'")
            continue

        results.append({
            "title": title,
            "price": price,
            "price_text": price_text,
            "url": link,
            "source": source,
        })

    # Ukloni duplikate i sortiraj
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    unique.sort(key=lambda r: (r["price"] is None, r["price"] or 0))
    logger.info(f"[GOOGLE_CSE] {len(unique)} rezultata za '{search_term}'")
    return unique


def scrape_webshops(search_term: str, max_price: float | None = None) -> list[dict]:
    """
    Agregira webshop rezultate:
      1. Google Custom Search (gigatron, winwin, tehnomanija, eponuda) — PRIMARY
      2. Eponuda direct scrape (cloudscraper) — FALLBACK ako Google CSE nije konfigurisan
    """
    import os

    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()

    if cse_id:
        # Google CSE je konfigurisan → koristi ga
        results = google_search_shops(search_term, max_price)
        if results:
            return results
        logger.info("[WEBSHOP] Google CSE vratio 0 — fallback na Eponuda direct")

    # Fallback: direktan Eponuda scrape
    return scrape_eponuda(search_term, max_price)


# ─── Dispatcher ───────────────────────────────────────────────────────────────

_SCRAPERS = {
    "polovniautomobili.com": scrape_polovniautomobili,
    "kupujemprodajem.com": scrape_kupujemprodajem,
    "halooglasi.com": scrape_halooglasi,
    "eponuda.com": scrape_eponuda,
}


def scrape_site(site: str, search_term: str, max_price: float | None = None) -> list[dict]:
    fn = _SCRAPERS.get(site)
    if fn:
        return fn(search_term, max_price)
    logger.warning(f"Nema scrapera za sajt: {site}")
    return []
