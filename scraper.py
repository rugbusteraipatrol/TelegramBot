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

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sr-RS,sr;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 15


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.error(f"HTTP greška [{url}]: {e}")
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
    soup = _get(
        "https://www.polovniautomobili.com/auto-oglasi/pretraga",
        params={
            "sort": "renewDate",
            "q": search_term,
            **({"price_to": int(max_price)} if max_price else {}),
        },
    )
    if not soup:
        return results

    for item in soup.select("article.classified-item, div.entity-body")[:12]:
        try:
            title_el = item.select_one("h3 a, .entity-title a, .classified-title a")
            price_el = item.select_one(".price-box strong, .price-box .price, .entity-price")

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.polovniautomobili.com" + href

            price_text = price_el.get_text(strip=True) if price_el else ""
            price = _parse_price(price_text)

            if not _matches_price(price, max_price):
                continue

            results.append({"title": title, "price": price, "price_text": price_text, "url": href})
        except Exception:
            continue

    return results


def scrape_kupujemprodajem(search_term: str, max_price: float | None = None) -> list[dict]:
    results = []
    url = "https://www.kupujemprodajem.com/pretraga"
    params = {
        "keywords": search_term,
        "currency": "eur",
        **({"priceTo": int(max_price)} if max_price else {}),
    }
    logger.info(f"🔗 KP Scraping: {url} | keywords='{search_term}' | max_price={max_price}")

    soup = _get(url, params=params)
    if not soup:
        logger.error(f"❌ KP: Nisu mogli dobiti soup za '{search_term}'")
        return results

    items = soup.select("div.offer-item, article.offer-item, div.kp-ad-list-item, li.offer-list-item")
    logger.info(f"📍 KP: Pronađenih {len(items)} itemova sa selektora")

    # Debug: Ispiši sve dostupne klase i tagove ako nema rezultata
    if not items:
        logger.warning("⚠️ KP: NEMA ITEMOVA! Analiziram HTML strukturu...")
        # Pronađi sve div-ove sa class atributima
        all_divs = soup.find_all("div", class_=True)
        all_articles = soup.find_all("article", class_=True)
        logger.info(f"  Ukupno div-ova sa klasama: {len(all_divs)}")
        logger.info(f"  Ukupno article-a sa klasama: {len(all_articles)}")
        # Ispiši prvih 10 klasa
        if all_divs:
            logger.info(f"  Primjer klasa iz div-ova:")
            for i, div in enumerate(all_divs[:10]):
                classes = div.get("class", [])
                logger.info(f"    {i+1}. {' '.join(classes)}")
        if all_articles:
            logger.info(f"  Primjer klasa iz article-a:")
            for i, art in enumerate(all_articles[:5]):
                classes = art.get("class", [])
                logger.info(f"    {i+1}. {' '.join(classes)}")

    for item in items[:12]:
        try:
            title_el = item.select_one(
                "h3 a, .offer-title a, .kp-ad-name a, a.offer-title"
            )
            price_el = item.select_one(
                ".price-box, .offer-price, .kp-ad-price, span.price"
            )

            if not title_el:
                logger.debug("⚠️ KP: Title element nije pronađen, skipam item")
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.kupujemprodajem.com" + href

            price_text = price_el.get_text(strip=True) if price_el else ""
            price = _parse_price(price_text)

            if not _matches_price(price, max_price):
                logger.debug(f"⚠️ KP: Cijena {price_text} iznad limita {max_price}, skipam")
                continue

            result = {"title": title, "price": price, "price_text": price_text, "url": href}
            results.append(result)
            logger.debug(f"  ✓ KP: {title[:40]}... | {price_text} | {href[:50]}...")
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
