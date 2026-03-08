#!/usr/bin/env python3
"""
Integration tests for the Radio source service.

Usage:
    # Against local dev instance:
    python3 tests/integration/test-radio.py

    # Against a deployed device:
    python3 tests/integration/test-radio.py http://beosound5c-office.kirstenhome:8779
"""

import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8779"
PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  PASS  {name}")
        PASS += 1
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        FAIL += 1


def get(path, timeout=15):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def post(path, data, timeout=30):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_raw(path, timeout=15):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


# ── 1. Service health ──
def test_status():
    data = get("/status")
    assert data["source"] == "radio", f"Expected source=radio, got {data}"
test("1. GET /status returns source=radio", test_status)


# ── 2. Root browse returns 5 categories ──
def test_root_browse():
    data = get("/browse?path=")
    assert data["parent"] is None, "Root parent should be None"
    assert len(data["items"]) == 5, f"Expected 5 root items, got {len(data['items'])}"
    names = [i["name"] for i in data["items"]]
    for expected in ["Popular", "Countries", "Genres", "Languages", "Favourites"]:
        assert expected in names, f"Missing {expected}"
    for item in data["items"]:
        assert item["type"] == "category"
        assert "icon" in item
        assert "color" in item
test("2. Root browse: 5 categories with icons/colors", test_root_browse)


# ── 3. Popular stations ──
def test_popular():
    data = get("/browse?path=popular")
    assert data["name"] == "Popular"
    assert data["parent"] == ""
    assert len(data["items"]) > 0, "Popular should have stations"
    station = data["items"][0]
    assert station["type"] == "station"
    assert station.get("stationuuid"), "Station missing stationuuid"
    assert station.get("url_resolved"), "Station missing url_resolved"
    assert station.get("name"), "Station missing name"
test("3. Browse popular: returns stations with required fields", test_popular)


# ── 4. Countries list ──
def test_countries():
    data = get("/browse?path=countries")
    assert data["name"] == "Countries"
    assert len(data["items"]) > 10, f"Expected >10 countries, got {len(data['items'])}"
    for item in data["items"][:3]:
        assert item["type"] == "category"
        assert "count" in item
test("4. Browse countries: returns category list with counts", test_countries)


# ── 5. Drill into a country ──
def test_country_drill():
    data = get("/browse?path=countries/Germany")
    assert data["parent"] == "countries"
    assert len(data["items"]) > 0, "Germany should have stations"
    assert data["items"][0]["type"] == "station"
test("5. Browse countries/Germany: returns stations", test_country_drill)


# ── 6. Genres list ──
def test_genres():
    data = get("/browse?path=genres")
    assert len(data["items"]) > 5, f"Expected >5 genres, got {len(data['items'])}"
    for item in data["items"][:3]:
        assert item["type"] == "category"
test("6. Browse genres: returns category list", test_genres)


# ── 7. Drill into a genre ──
def test_genre_drill():
    data = get("/browse?path=genres/rock")
    assert len(data["items"]) > 0
    assert data["items"][0]["type"] == "station"
test("7. Browse genres/rock: returns stations", test_genre_drill)


# ── 8. Languages list ──
def test_languages():
    data = get("/browse?path=languages")
    assert len(data["items"]) > 5
test("8. Browse languages: returns list", test_languages)


# ── 9. Drill into a language ──
def test_language_drill():
    data = get("/browse?path=languages/english")
    assert len(data["items"]) > 0
    assert data["items"][0]["type"] == "station"
test("9. Browse languages/english: returns stations", test_language_drill)


# ── 10. Favourites structure ──
def test_favourites():
    data = get("/browse?path=favourites")
    assert data["name"] == "Favourites"
    assert isinstance(data["items"], list)
test("10. Browse favourites: valid response", test_favourites)


# ── 11. Station subtitle format ──
def test_station_subtitle():
    data = get("/browse?path=popular")
    for s in data["items"][:10]:
        assert "subtitle" in s, f"Station {s['name']} missing subtitle"
test("11. Station items have subtitle field", test_station_subtitle)


# ── 12. Play station command ──
def test_play_station():
    data = get("/browse?path=popular")
    station = data["items"][0]
    uuid = station["stationuuid"]
    # Longer timeout — player_play may timeout connecting to player service in dev
    result = post("/command", {"command": "play_station", "stationuuid": uuid}, timeout=30)
    assert result.get("status") == "ok", f"Play failed: {result}"
    status = get("/status")
    assert status["state"] == "playing", f"Expected playing, got {status['state']}"
    assert status["station"] is not None
test("12. Play station: sets state to playing", test_play_station)


# ── 13. Toggle pause/resume ──
def test_toggle():
    result = post("/command", {"command": "toggle"}, timeout=30)
    assert result.get("status") == "ok"
    status = get("/status")
    assert status["state"] == "paused", f"Expected paused, got {status['state']}"
    result = post("/command", {"command": "toggle"}, timeout=30)
    assert result.get("status") == "ok"
    status = get("/status")
    assert status["state"] == "playing", f"Expected playing, got {status['state']}"
test("13. Toggle: pause then resume", test_toggle)


# ── 14. Next/prev station cycling ──
def test_next_prev():
    status_before = get("/status")
    station_before = status_before["station"]
    result = post("/command", {"command": "next"}, timeout=30)
    assert result.get("status") == "ok"
    status_after = get("/status")
    assert status_after["state"] == "playing"
    result = post("/command", {"command": "prev"}, timeout=30)
    assert result.get("status") == "ok"
    status_back = get("/status")
    assert status_back["station"] == station_before, \
        f"Prev didn't return to original: {status_back['station']} vs {station_before}"
test("14. Next then prev: returns to original station", test_next_prev)


# ── 15. Toggle favourite (current station) ──
def test_toggle_favourite():
    status = get("/status")
    fav_count_before = status["favourites"]
    result = post("/command", {"command": "toggle_favourite"})
    assert result.get("status") == "ok"
    assert result.get("favourite") is True, f"Expected favourite=True, got {result}"
    status = get("/status")
    assert status["favourites"] == fav_count_before + 1
    favs = get("/browse?path=favourites")
    assert len(favs["items"]) == fav_count_before + 1
    # Remove it
    result = post("/command", {"command": "toggle_favourite"})
    assert result.get("status") == "ok"
    assert result.get("favourite") is False
    status = get("/status")
    assert status["favourites"] == fav_count_before
test("15. Toggle favourite: add then remove current station", test_toggle_favourite)


# ── 16. Play station not found ──
def test_play_not_found():
    result = post("/command", {"command": "play_station", "stationuuid": "nonexistent-uuid"})
    assert result.get("status") == "error"
test("16. Play nonexistent station: returns error", test_play_not_found)


# ── 17. Stop playback ──
def test_stop():
    result = post("/command", {"command": "stop"}, timeout=30)
    assert result.get("status") == "ok"
    status = get("/status")
    assert status["state"] == "stopped"
    assert status["station"] is None
test("17. Stop: state=stopped, station=None", test_stop)


# ── 18. Toggle with no station (stopped) ──
def test_toggle_stopped():
    result = post("/command", {"command": "toggle"})
    assert result.get("status") == "ok"
    status = get("/status")
    assert status["state"] == "stopped"
test("18. Toggle while stopped with no station: stays stopped", test_toggle_stopped)


# ── 19. Favicon proxy ──
def test_favicon_proxy():
    data = get("/browse?path=popular")
    favicon_url = None
    for s in data["items"]:
        if s.get("favicon"):
            favicon_url = s["favicon"]
            break
    if not favicon_url:
        raise Exception("No station with favicon found in popular")
    encoded = urllib.parse.quote(favicon_url, safe='')
    try:
        status, headers, body = get_raw(f"/favicon?url={encoded}")
        assert status == 200
        ct = headers.get("Content-Type", "")
        assert "image" in ct or "octet" in ct, f"Unexpected content-type: {ct}"
        assert len(body) > 0
    except urllib.error.HTTPError as e:
        if e.code == 404:
            pass  # upstream favicon may be dead
        else:
            raise
test("19. Favicon proxy: returns image data", test_favicon_proxy)


# ── 20. Favicon proxy blocks internal URLs ──
def test_favicon_ssrf():
    try:
        encoded = urllib.parse.quote("http://127.0.0.1:8779/status", safe='')
        get_raw(f"/favicon?url={encoded}")
        raise Exception("Should have been blocked")
    except urllib.error.HTTPError as e:
        assert e.code == 403, f"Expected 403, got {e.code}"
test("20. Favicon proxy: blocks internal URLs", test_favicon_ssrf)


# ── 21. Browse caching ──
def test_cache():
    get("/browse?path=popular")  # warm cache
    t0 = time.time()
    get("/browse?path=popular")
    t1 = time.time()
    assert (t1 - t0) < 0.5, f"Cached request too slow: {t1 - t0:.3f}s"
test("21. API cache: cached browse is fast (<0.5s)", test_cache)


# ── 22. Unknown browse path ──
def test_unknown_path():
    data = get("/browse?path=nonexistent")
    assert data["name"] == "Unknown"
    assert data["items"] == []
test("22. Unknown browse path: empty result, no error", test_unknown_path)


# ── 23. Concurrent browse requests ──
def test_concurrent():
    import concurrent.futures
    paths = ["popular", "countries", "genres", "languages", "favourites"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(get, f"/browse?path={p}") for p in paths]
        results = [f.result() for f in futures]
    assert len(results) == 5
    for r in results:
        assert "items" in r
test("23. Concurrent browse: 5 parallel requests succeed", test_concurrent)


# ── 24. Station list snapshot stability ──
def test_snapshot_stability():
    pop = get("/browse?path=popular")
    station = pop["items"][0]
    post("/command", {"command": "play_station", "stationuuid": station["stationuuid"]}, timeout=30)
    station_playing = get("/status")["station"]
    # Browse a different category — should NOT affect next/prev
    get("/browse?path=genres/jazz")
    post("/command", {"command": "next"}, timeout=30)
    post("/command", {"command": "prev"}, timeout=30)
    station_after = get("/status")["station"]
    assert station_after == station_playing, \
        f"Snapshot broken: was {station_playing}, now {station_after}"
    post("/command", {"command": "stop"}, timeout=30)
test("24. Next/prev uses snapshot, not latest browse", test_snapshot_stability)


# ── 25. Favourite toggle with no current station ──
def test_favourite_no_station():
    # Make sure stopped
    post("/command", {"command": "stop"}, timeout=30)
    result = post("/command", {"command": "toggle_favourite"})
    assert result.get("status") == "error"
test("25. Toggle favourite with no station: returns error", test_favourite_no_station)


# ── Summary ──
print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
print(f"{'='*50}")
sys.exit(1 if FAIL else 0)
