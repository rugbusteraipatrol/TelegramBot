#!/usr/bin/env python3
"""
Debug script to see what's happening in Polovni Automobili scraper
"""
import sys
import logging
from bs4 import BeautifulSoup
import requests
from scraper import _get, _parse_price, _matches_price

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Test Polovni Automobili scraping
search_term = "Golf"
max_price = 30000

url = "https://www.polovniautomobili.com/auto-oglasi/pretraga"
params = {
    "sort": "renewDate",
    "q": search_term,
    "price_to": int(max_price),
}

print(f"\nDEBUG: Fetching {url}")
print(f"DEBUG: Params: {params}\n")

soup = _get(url, params=params)
if not soup:
    print("ERROR: Couldn't get soup")
    sys.exit(1)

# Find all articles
items = soup.find_all("article")
print(f"DEBUG: Found {len(items)} articles total\n")

# Debug first 5 items
for i, item in enumerate(items[:5], 1):
    print(f"\n--- ITEM {i} ---")

    # Try to get title
    title_el = item.select_one("h2 a")
    if not title_el:
        title_el = item.select_one("h3 a, .entity-title a, .classified-title a")

    if title_el:
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        print(f"Title: {title}")
        print(f"Href: {href}")
    else:
        print("Title: [NOT FOUND]")
        # Show what's in the article
        print(f"Article HTML (first 500 chars):\n{str(item)[:500]}\n")

    # Try to get price
    price_el = item.select_one("div.price span")
    if not price_el:
        price_el = item.select_one(".price-box strong, .price-box .price, .entity-price")

    if price_el:
        price_text = price_el.get_text(strip=True)
        price = _parse_price(price_text)
        print(f"Price: {price_text} (parsed: {price})")
    else:
        print("Price: [NOT FOUND]")

    # Check filters
    if title_el:
        in_search = search_term.lower() in title.lower()
        print(f"Contains '{search_term}': {in_search} (title lower: '{title.lower()}')")
