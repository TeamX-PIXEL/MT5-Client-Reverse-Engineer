"""Broker Search API — SearchMQ + Search endpoints."""
import hashlib
import json
import time

import requests


HMAC_KEY = bytes([61, 123, 21, 22, 214, 234, 187, 52, 217, 214,
                  99, 227, 98, 62, 27, 215, 251, 220, 174, 244,
                  87, 59, 223, 53, 127, 168, 207, 11, 190, 173,
                  146, 127])

SEARCH_URL = "http://search.mtapi.io/Search?company={company}&mt5=true"
SEARCHMQ_URL = "https://updates.metaquotes.net/public/mt5/network"
USER_AGENT = "MetaTrader 5 Terminal/5.5830 (Windows NT 10.0.22621; x64)"


def _compute_signature(body: str) -> str:
    body_hash = hashlib.md5(body.encode('latin-1')).digest()
    return hashlib.md5(body_hash + HMAC_KEY).hexdigest()


def _generate_cookie() -> str:
    timestamp = int(time.time())
    ms = int(time.monotonic_ns() // 1_000_000) & 0x1FFFFFF
    val = ((timestamp - 1420070400) | (ms << 32)) | 0x4200000000000000

    seed = int(time.time_ns() & 0xFFFFFFFF)
    rand_bytes = bytearray(256)
    for i in range(256):
        seed = (seed * 214013 + 2531011) & 0xFFFFFFFF
        rand_bytes[i] = (seed >> 16) & 0xFF

    md5 = bytearray(hashlib.md5(bytes(rand_bytes)).digest())
    md5[0] = 0
    for j in range(1, 16):
        md5[0] = (md5[0] + md5[j]) & 0xFF
    tid = md5[:8].hex().upper()[:17]

    age = timestamp - 86400
    return f"_fz_uniq={val};uniq={val};age={age};tid={tid}"


def search(company: str) -> list:
    """Broker.Search() — HTTP GET, no auth."""
    url = SEARCH_URL.format(company=company)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", [])


def search_mq(company: str) -> list:
    """Broker.SearchMQ() — HTTP POST with HMAC."""
    body = f"company={company}&code=mt5"
    sig = _compute_signature(body)
    full_body = f"{body}&signature={sig}&ver=2"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en",
        "User-Agent": USER_AGENT,
        "Cookie": _generate_cookie(),
    }
    resp = requests.post(SEARCHMQ_URL, data=full_body, headers=headers, timeout=10)
    resp.raise_for_status()
    text = resp.text
    json_start = text.find("{")
    if json_start < 0:
        raise ValueError(f"No JSON in response: {text[:200]}")
    return json.loads(text[json_start:]).get("result", [])


def find_server(server_name: str) -> dict | None:
    """Find a specific server by name. Tries SearchMQ first, falls back to Search."""
    for api_fn in [search_mq, search]:
        try:
            for company in api_fn(server_name):
                for result in company.get("results", []):
                    if result["name"].lower() == server_name.lower():
                        return {
                            "company": company.get("company"),
                            "name": result["name"],
                            "site": result.get("site"),
                            "access": result.get("access", []),
                            "is_demo": result.get("is_demo", 0),
                        }
        except Exception:
            continue
    return None


def find_server_ips(server_name: str) -> list[str]:
    """Find server IP addresses by name. Returns list of 'ip:port' strings."""
    info = find_server(server_name)
    return info["access"] if info else []
