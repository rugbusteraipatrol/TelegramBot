#!/usr/bin/env python3
"""
Comprehensive test suite for PriceBot - tests all parsing and scraping functions
"""
import sys
import logging
from bot import parse_ad_query
from scraper import scrape_kupujemprodajem, scrape_polovniautomobili, scrape_halooglasi

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

print("\n" + "="*70)
print("[TEST] PRICEBOT COMPREHENSIVE TEST SUITE")
print("="*70)

# Test 1: Price Parsing
print("\n" + "-"*70)
print("[TEST 1] PARSE_AD_QUERY - Cijena i proizvod")
print("-"*70)

test_cases = [
    ("Samsung Galaxy A55 400€", ("Samsung Galaxy A55", 400.0)),
    ("iPhone 15 Pro 1500€", ("iPhone 15 Pro", 1500.0)),
    ("Golf 30000€", ("Golf", 30000.0)),
    ("PS5 400 EUR", ("PS5", 400.0)),
    ("Laptop 1200,50 EUR", ("Laptop", 1200.5)),
    ("Telefon pod 150€", ("Telefon pod", 150.0)),
    ("Samsung bez cijene", (None, None)),
]

for test_input, expected in test_cases:
    product, price = parse_ad_query(test_input)
    status = "OK" if (product, price) == expected else "FAIL"
    print(f"[{status}] '{test_input}' -> product='{product}', price={price}")
    if (product, price) != expected:
        print(f"     Expected: {expected}")

# Test 2: KupujemProdajem Scraper
print("\n" + "-"*70)
print("[TEST 2] SCRAPER - KupujemProdajem (telefoni)")
print("-"*70)

results = scrape_kupujemprodajem("Samsung", max_price=400)
print(f"Pretraga: 'Samsung' do 400 EUR")
print(f"Rezultati: {len(results)} stavki")
if results:
    for i, item in enumerate(results[:3], 1):
        print(f"  {i}. {item['title'][:50]}... | {item['price_text']}")
    if len(results) > 3:
        print(f"  ... i jos {len(results) - 3}")
else:
    print("  Nema rezultata")

# Test 3: Golf Search (the problematic one)
print("\n" + "-"*70)
print("[TEST 3] SCRAPER - Polovni Automobili (GOLF!)")
print("-"*70)

results = scrape_polovniautomobili("Golf", max_price=30000)
print(f"Pretraga: 'Golf' do 30000 EUR")
print(f"Rezultati: {len(results)} vozila")

if results:
    print("\nRezultati:")
    all_contain_golf = True
    for i, item in enumerate(results[:10], 1):
        contains_golf = "golf" in item['title'].lower()
        status = "OK" if contains_golf else "FAIL"
        print(f"  [{status}] {i}. {item['title'][:50]}... | {item['price_text']}")
        if not contains_golf:
            all_contain_golf = False

    if len(results) > 10:
        print(f"  ... i jos {len(results) - 10} rezultata")

    print(f"\nFiltriranje po 'Golf': {'OK - DOBRO' if all_contain_golf else 'FAIL - jos ima vozila bez Golf-a!'}")
else:
    print("  Nema rezultata")

# Test 4: General Car Search
print("\n" + "-"*70)
print("[TEST 4] SCRAPER - Polovni Automobili (Skoda)")
print("-"*70)

results = scrape_polovniautomobili("Škoda", max_price=15000)
print(f"Pretraga: 'Skoda' do 15000 EUR")
print(f"Rezultati: {len(results)} vozila")

if results:
    print("\nRezultati:")
    for i, item in enumerate(results[:5], 1):
        print(f"  {i}. {item['title'][:50]}... | {item['price_text']}")
else:
    print("  Nema rezultata")

# Test 5: HaloOglasi Scraper
print("\n" + "-"*70)
print("[TEST 5] SCRAPER - HaloOglasi")
print("-"*70)

try:
    results = scrape_halooglasi("Samsung", max_price=400)
    print(f"Pretraga: 'Samsung' do 400 EUR na HaloOglasima")
    print(f"Rezultati: {len(results)} stavki")
    if results:
        for i, item in enumerate(results[:3], 1):
            print(f"  {i}. {item['title'][:50]}... | {item['price_text']}")
    else:
        print("  Nema rezultata")
except Exception as e:
    print(f"  Greska: {str(e)[:100]}")

print("\n" + "="*70)
print("[DONE] TESTIRANJE ZAVRSENO")
print("="*70 + "\n")
