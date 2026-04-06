#!/usr/bin/env python3
"""
Full flow test - simulates what happens when user sends search requests
"""
import asyncio
import logging
from bot import do_search, parse_ad_query, extract_search_term, is_auto_search, is_kp_search, is_tech_search, is_real_estate_search
from scraper import scrape_kupujemprodajem, scrape_halooglasi, scrape_polovniautomobili

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

print("\n" + "="*70)
print("FULL FLOW TEST - Simulating bot requests")
print("="*70)

# Test cases: (input, expected_mode)
test_cases = [
    ("Samsung Galaxy A55 400€", "KP/Tech"),
    ("Golf 30000€", "Auto"),
    ("Apartman za prodaju 100000€", "RealEstate"),
    ("Laptop HP do 500€", "KP/Tech"),
    ("Bugatti do 1000000€", "Auto"),
    ("Samsung bez cijene", "KP/Tech"),
]

for test_input, expected_mode in test_cases:
    print(f"\n{'-'*70}")
    print(f"INPUT: '{test_input}'")
    print(f"EXPECTED MODE: {expected_mode}")
    print(f"{'-'*70}")

    # Parse the input
    product, price = parse_ad_query(test_input)
    print(f"Parsed: product='{product}', price={price}")

    # Detect mode
    kp = is_kp_search(test_input)
    tech = is_tech_search(test_input)
    auto = is_auto_search(test_input)
    real_estate = is_real_estate_search(test_input)

    print(f"Modes: KP={kp}, Tech={tech}, Auto={auto}, RealEstate={real_estate}")

    # Extract search term
    search_term = extract_search_term(test_input)
    print(f"Search term: '{search_term}'")

    # Simulate scraping
    if auto and not kp:
        print(f"[ACTION] Scraping PolvniAutomobili for '{search_term}'...")
        try:
            results = scrape_polovniautomobili(search_term)
            print(f"  Results: {len(results)} vehicles")
            if results:
                for i, r in enumerate(results[:3], 1):
                    print(f"    {i}. {r['title'][:50]}... ({r['price_text']})")
            else:
                print(f"  -> FALLBACK TO GEMINI (no results)")
        except Exception as e:
            print(f"  ERROR: {e}")

    elif (kp or tech) and not auto:
        print(f"[ACTION] Scraping KupujemProdajem for '{search_term}'...")
        try:
            results = scrape_kupujemprodajem(search_term)
            print(f"  Results: {len(results)} items")
            if results:
                for i, r in enumerate(results[:3], 1):
                    print(f"    {i}. {r['title'][:50]}... ({r['price_text']})")
            else:
                print(f"  -> FALLBACK TO GEMINI (no results)")
        except Exception as e:
            print(f"  ERROR: {e}")

    elif real_estate and not kp:
        print(f"[ACTION] Scraping HaloOglasi for '{search_term}'...")
        try:
            results = scrape_halooglasi(search_term)
            print(f"  Results: {len(results)} items")
            if results:
                for i, r in enumerate(results[:3], 1):
                    print(f"    {i}. {r['title'][:50]}... ({r['price_text']})")
            else:
                print(f"  -> FALLBACK TO GEMINI (no results)")
        except Exception as e:
            print(f"  ERROR: {e}")

    else:
        print(f"[ACTION] Using Gemini webshop search for '{test_input}'...")

print("\n" + "="*70)
print("FULL FLOW TEST COMPLETE")
print("="*70 + "\n")
