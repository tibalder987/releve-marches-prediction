#!/usr/bin/env python3
"""Vérifie que les API publiques répondent depuis cette machine (lecture seule)."""
import json
import time
import urllib.request

UA = {"User-Agent": "releve-lecture-seule/1.0", "Content-Type": "application/json", "Accept": "application/json"}
TESTS = [
    ("gamma", "https://gamma-api.polymarket.com/markets/keyset?closed=false&limit=1", None),
    ("clob_time", "https://clob.polymarket.com/time", None),
    ("clob_books", "https://clob.polymarket.com/books", []),
    ("data_api", "https://data-api.polymarket.com/v2/trades?limit=1", None),
    ("kalshi", "https://api.elections.kalshi.com/trade-api/v2/exchange/status", None),
    ("kalshi_markets", "https://api.elections.kalshi.com/trade-api/v2/markets?limit=1", None),
]
out = {"quand": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
for name, url, body in TESTS:
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA,
                                     method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=30) as r:
            out[name] = {"http": r.status, "debut": r.read(160).decode("utf-8", "replace")}
    except Exception as e:
        out[name] = {"erreur": (type(e).__name__ + " " + str(e))[:200]}
print(json.dumps(out, ensure_ascii=False, indent=1))
