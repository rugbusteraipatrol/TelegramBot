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
    params = {
        "sort": "renewDate",
        "q": search_term,
        **({"price_to": int(max_price)} if max_price else {}),
    }
    logger.info(f"🔗 PA Scraping: {url} | q='{search_term}' | max_price={max_price}")

    soup = _get(url, params=params)
    if not soup:
        logger.error(f"❌ PA: Nisu mogli dobiti soup za '{search_term}'")
        return results

    # Pronađi sve article tagove (nova struktura)
    items = soup.find_all("article")
    logger.info(f"📍 PA: Pronađenih {len(items)} oglasa")

    for item in items[:12]:
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

            results.append({"title": title, "price": price, "price_text": price_text, "url": href})
            logger.debug(f"  ✓ PA: {title[:40]}... | {price_text}")
        except Exception as e:
            logger.debug(f"⚠️ PA: Greška pri parsiranju: {e}")
            continue

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


# ─── Dispatcher ───────────────────────────────────────────────────────────────

_SCRAPERS = {
    "polovniautomobili.com": scrape_polovniautomobili,
    "kupujemprodajem.com": scrape_kupujemprodajem,
    "halooglasi.com": scrape_halooglasi,
}


def scrape_site(site: str, search_term: str, max_price: float | None = None) -> list[dict]:
    scraper = _SCRAPERS.get(site)
    if scraper:
        return scraper(search_term, max_price)
    logger.warning(f"Nema scrapera za sajt: {site}")
    return []
