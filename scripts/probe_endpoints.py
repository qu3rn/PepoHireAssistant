"""Probe script to find working collector API endpoints."""
import re
import sys
import json

import requests

H_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
}

def probe(label, url, method="GET", payload=None, timeout=10):
    try:
        if method == "POST":
            r = requests.post(url, json=payload, headers=H_BROWSER, timeout=timeout)
        else:
            r = requests.get(url, headers=H_BROWSER, timeout=timeout)
        ct = r.headers.get("content-type", "")
        is_json = "json" in ct
        body = r.text[:200].replace("\n", " ")
        if is_json and r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, list):
                    body = f"[list of {len(data)} items]"
                elif isinstance(data, dict):
                    body = f"{{dict keys: {list(data.keys())[:8]}}}"
            except Exception:
                pass
        print(f"  [{r.status_code}] {label}")
        print(f"         URL: {url}")
        print(f"         CT:  {ct[:60]}")
        print(f"         Body: {body[:180]}")
    except Exception as e:
        print(f"  [ERR] {label}: {e}")

print("\n=== JUSTJOIN ===")
probe("v2 user-panel offers", "https://api.justjoin.it/v2/user-panel/offers?page=1&perPage=5")
probe("v2 offers", "https://api.justjoin.it/v2/offers?page=1&perPage=5")
probe("v1 offers all", "https://justjoin.it/api/offers")
probe("v1 offers filtered", "https://justjoin.it/api/offers?categories[]=javascript")
probe("feed", "https://feed.justjoin.it/offers?page=1&perPage=5")

print("\n=== ROCKETJOBS ===")
probe("api offers", "https://api.rocketjobs.pl/api/offers?query=React&page=0&perPage=5")
probe("offers v3", "https://api.rocketjobs.pl/api/v3/offers?query=React")
probe("rocketjobs offers", "https://rocketjobs.pl/api/offers?query=React&page=0&perPage=5")
probe("offers listing", "https://api.rocketjobs.pl/offers?query=React&page=0")

print("\n=== NOFLUFFJOBS ===")
probe("GET /api/posting no keyword", "https://nofluffjobs.com/api/posting")
probe("GET /api/posting keyword react", "https://nofluffjobs.com/api/posting?keyword=react")
probe("GET /api/posting keyword react page", "https://nofluffjobs.com/api/posting?keyword=react&page=1&pageSize=10")

print("\n=== PRACUJ ===")
probe("massachusetts", "https://massachusetts.pracuj.pl/jobs?q=React&pn=1")
probe("api pracuj", "https://api.pracuj.pl/jobs?q=React&pn=1")
probe("www pracuj offer api", "https://www.pracuj.pl/praca/react;kw?pn=1")
