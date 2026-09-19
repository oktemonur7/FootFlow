import http.server
import socketserver
import urllib.request
import json
import re
import os
import sys
import time

PORT = 8080
TARGET_URL = "https://www.sahadan.com/lig/premier-lig/2kwbbcootiqqgmrzs6o5inle5?round_id=94794"
STREAM_PLAYER_CACHE = {}
MATCH_GOALS_CACHE = {}

# Preload persisted match goals cache if available
try:
    _cache_file = os.path.join(os.path.dirname(__file__), "all_goals_cache.json")
    if os.path.exists(_cache_file):
        with open(_cache_file, "r", encoding="utf-8") as _f:
            _loaded = json.load(_f)
            for _u, _g in _loaded.items():
                MATCH_GOALS_CACHE[_u] = {
                    "goals": _g,
                    "cards": [],
                    "time": time.time(),
                    "is_ft": True
                }
        print(f"Loaded {len(MATCH_GOALS_CACHE)} matches into MATCH_GOALS_CACHE.")
except Exception as _e:
    print("Could not preload all_goals_cache.json:", _e)

try:
    _cards_cache_file = os.path.join(os.path.dirname(__file__), "all_cards_cache.json")
    if os.path.exists(_cards_cache_file):
        with open(_cards_cache_file, "r", encoding="utf-8") as _cf:
            _loaded_cards = json.load(_cf)
            for _u, _c in _loaded_cards.items():
                if isinstance(_c, list) and _c:
                    if _u in MATCH_GOALS_CACHE:
                        MATCH_GOALS_CACHE[_u]["cards"] = _c
                    else:
                        MATCH_GOALS_CACHE[_u] = {"goals": [], "cards": _c, "time": time.time(), "is_ft": True}
        print(f"Loaded {len(_loaded_cards)} matches into MATCH_CARDS_CACHE.")
except Exception as _e:
    pass

import unicodedata

def to_sahadan_slug(text):
    if not text:
        return ""
    tr_map = {'ı':'i', 'I':'i', 'İ':'i', 'ş':'s', 'Ş':'s', 'ğ':'g', 'Ğ':'g', 'ü':'u', 'Ü':'u', 'ö':'o', 'Ö':'o', 'ç':'c', 'Ç':'c'}
    for k, v in tr_map.items():
        text = text.replace(k, v)
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    return re.sub(r'[-\s]+', '-', text)

def get_cached_match_cards(uuid):
    if uuid and uuid in MATCH_GOALS_CACHE:
        return MATCH_GOALS_CACHE[uuid].get("cards", [])
    return []

def fetch_match_goals(home, away, uuid, min_goals=0):
    if not uuid:
        return []
    now = time.time()
    if uuid in MATCH_GOALS_CACHE:
        cached = MATCH_GOALS_CACHE[uuid]
        c_goals = cached.get("goals", [])
        # Eğer beklenen en az gol sayısı (skor) belirtilmişse ve cache'deki gol sayısı eksikse cache'i geç
        if min_goals <= 0 or len(c_goals) >= min_goals:
            # Finished or valid cache within 90 seconds
            if cached.get("is_ft") or (now - cached.get("time", 0) < 90):
                return c_goals

    slug = f"{to_sahadan_slug(home)}-vs-{to_sahadan_slug(away)}"
    url = f"https://www.sahadan.com/mac/{slug}/{uuid}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Cache-Control": "no-cache"
        })
        html = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        m = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', html)
        if not m:
            return []
        data = json.loads(m.group(1))

        memo = {}
        def deep_resolve(val, depth=0):
            if depth > 20: return val
            if isinstance(val, int) and 0 <= val < len(data):
                if val in memo: return memo[val]
                raw = data[val]
                if isinstance(raw, list) and len(raw) == 2 and raw[0] in ('ShallowReactive', 'Reactive', 'Set', 'Map'):
                    res = deep_resolve(raw[1], depth + 1)
                    memo[val] = res
                    return res
                if isinstance(raw, dict):
                    res = {}
                    memo[val] = res
                    for k, v in raw.items(): res[k] = deep_resolve(v, depth + 1)
                    return res
                if isinstance(raw, list):
                    res = []
                    memo[val] = res
                    for item in raw: res.append(deep_resolve(item, depth + 1))
                    return res
                return raw
            elif isinstance(val, dict):
                return {k: deep_resolve(v, depth + 1) for k, v in val.items()}
            elif isinstance(val, list):
                return [deep_resolve(v, depth + 1) for v in val]
            return val

        resolved = deep_resolve(2)
        events = []
        def find_key_events(obj, depth=0):
            if depth > 10: return
            if isinstance(obj, dict):
                if 'key_events' in obj and isinstance(obj['key_events'], list):
                    events.extend(obj['key_events'])
                    return
                for v in obj.values():
                    find_key_events(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    find_key_events(item, depth + 1)

        find_key_events(resolved)
        goals = []
        cards = []
        for ev in events:
            t = ev.get('type')
            if t in ('G', 'PG', 'OG'):
                scorer = ev.get('scorer', {}) or {}
                assist = ev.get('assist', {}) or {}
                goals.append({
                    'type': t,
                    'minute': ev.get('minute'),
                    'extra_min': ev.get('minute_extra'),
                    'team': ev.get('team'),
                    'scorer': scorer.get('name') or scorer.get('display_name') or 'Bilinmiyor',
                    'assist': assist.get('name') or assist.get('display_name') or '',
                    'score_A': ev.get('score_A'),
                    'score_B': ev.get('score_B')
                })
            elif t in ('RC', 'Y2C'):
                team_side = str(ev.get('team') or '').upper()
                player_obj = ev.get('player', {}) or {}
                p_name = player_obj.get('name') or player_obj.get('display_name') or ''
                cards.append({
                    'type': t,
                    'team': team_side,
                    'player': p_name,
                    'minute': ev.get('minute'),
                    'extra_min': ev.get('minute_extra')
                })

        # Check if match is finished from resolved data
        is_ft = False
        if isinstance(resolved, dict):
            status_val = str(resolved.get("status") or "").lower()
            if status_val in ("played", "ms", "ft", "finished"):
                is_ft = True

        MATCH_GOALS_CACHE[uuid] = {
            "goals": goals,
            "cards": cards,
            "time": now,
            "is_ft": is_ft
        }
        return goals
    except Exception as e:
        print(f"Error fetching match goals for {slug} ({uuid}):", e)
        return []


def fetch_sahadan():
    try:
        req = urllib.request.Request(TARGET_URL, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache"
        })
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8")
        
        for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
            if "weeklyStandings" in script or "competition-main" in script:
                data = json.loads(script)
                
                memo = {}
                def deep_resolve(val, depth=0):
                    if depth > 16:
                        return None
                    if isinstance(val, int) and 0 <= val < len(data):
                        if val in memo:
                            return memo[val]
                        raw = data[val]
                        if isinstance(raw, list) and len(raw) == 2 and raw[0] in ("ShallowReactive", "Reactive", "Set", "Map"):
                            res = deep_resolve(raw[1], depth + 1)
                            memo[val] = res
                            return res
                        if isinstance(raw, dict):
                            res = {}
                            memo[val] = res
                            for k, v in raw.items():
                                res[k] = deep_resolve(v, depth + 1)
                            return res
                        if isinstance(raw, list):
                            res = []
                            memo[val] = res
                            for item in raw:
                                res.append(deep_resolve(item, depth + 1))
                            return res
                        return raw
                    elif isinstance(val, dict):
                        return {k: deep_resolve(v, depth + 1) for k, v in val.items()}
                    elif isinstance(val, list):
                        return [deep_resolve(v, depth + 1) for v in val]
                    return val

                root_data = deep_resolve(2)
                comp_keys = [k for k in root_data.keys() if "competition" in k]
                if not comp_keys:
                    continue
                comp_data = root_data[comp_keys[0]]

                # Puan Tablosu
                rankings = comp_data.get("rankings", {})
                total_table = rankings.get("total", [{}])[0].get("table", [])
                clean_standings = []
                for row in total_table:
                    team = row.get("team", {}) or {}
                    pro = row.get("pro", 0) or 0
                    against = row.get("against", 0) or 0
                    clean_standings.append({
                        "rank": row.get("rank"),
                        "team_id": team.get("id"),
                        "team_uuid": team.get("uuid"),
                        "name": team.get("name"),
                        "display_name": team.get("display_name", team.get("name")),
                        "played": row.get("played", 0),
                        "win": row.get("win", 0),
                        "draw": row.get("draw", 0),
                        "lost": row.get("lost", 0),
                        "pro": pro,
                        "against": against,
                        "diff": pro - against,
                        "pts": row.get("pts", 0),
                        "serie": row.get("serie", ""),
                        "zone": row.get("zone", {})
                    })

                # Fikstür / Haftalar
                clean_weeks = []
                current_week_idx = 0
                gamesets = comp_data.get("gamesets", [])
                
                for idx, gs in enumerate(gamesets):
                    week_num = gs.get("name")
                    matches = []
                    has_active = False
                    for m in gs.get("matches", []):
                        tA = m.get("team_A", {}) or {}
                        tB = m.get("team_B", {}) or {}
                        st = m.get("status", "")
                        if st in ("Played", "Playing", "Live"):
                            has_active = True
                        matches.append({
                            "id": m.get("id"),
                            "date_time": m.get("date_time_utc"),
                            "match_time": m.get("match_time"),
                            "status": st,
                            "home_team": {
                                "id": tA.get("id"),
                                "uuid": tA.get("uuid"),
                                "name": tA.get("name"),
                                "display_name": tA.get("display_name", tA.get("name"))
                            },
                            "away_team": {
                                "id": tB.get("id"),
                                "uuid": tB.get("uuid"),
                                "name": tB.get("name"),
                                "display_name": tB.get("display_name", tB.get("name"))
                            },
                            "home_score": m.get("fts_A"),
                            "away_score": m.get("fts_B"),
                            "half_time_home": m.get("hts_A"),
                            "half_time_away": m.get("hts_B")
                        })
                    if has_active:
                        current_week_idx = idx
                    clean_weeks.append({
                        "week": week_num,
                        "matches": matches
                    })

                return {
                    "success": True,
                    "competition": comp_data.get("competition", {}).get("name", "Premier Lig"),
                    "source": TARGET_URL,
                    "current_week_index": current_week_idx,
                    "standings": clean_standings,
                    "weeks": clean_weeks
                }
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": False, "error": "Veri bulunamadı"}

from urllib.parse import urlparse

class PremierLeagueHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/stream-player":
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            match_id = query.get("id", [""])[0]
            server_name = query.get("server", ["falcon"])[0]
            player_html = None
            cache_key = f"{server_name}_{match_id}"
            now = time.time()
            if cache_key in STREAM_PLAYER_CACHE and (now - STREAM_PLAYER_CACHE[cache_key]["time"] < 120):
                player_html = STREAM_PLAYER_CACHE[cache_key]["html"]
            elif match_id:
                try:
                    target_url = f"https://ntv.cx/watch/{server_name}/{match_id}"
                    req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
                    html = urllib.request.urlopen(req, timeout=7).read().decode("utf-8")
                    m = re.search(r'src=[\"\'](/embed\?t=[^\"\']+)[\"\']', html)
                    if m:
                        embed_url = "https://ntv.cx" + m.group(1)
                        req2 = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Referer": target_url})
                        raw_html = urllib.request.urlopen(req2, timeout=7).read().decode("utf-8")
                        
                        # 1. Kill loading screen immediately from CSS parse cycle 0 & make streamIframe immediate
                        head_override = '<head><style>#loadingScreen, .loading-screen, .loading-container, .loading-progress, .loading-progress-bar { display: none !important; opacity: 0 !important; visibility: hidden !important; height: 0 !important; pointer-events: none !important; } #streamIframe { display: block !important; width: 100% !important; height: 100% !important; border: none !important; opacity: 1 !important; visibility: visible !important; }</style>'
                        clean = raw_html.replace('<head>', head_override)
                        clean = clean.replace('id="loadingScreen"', 'id="loadingScreen" style="display:none!important;"')
                        clean = re.sub(r'<div[^>]+id=[\"\']loadingScreen[\"\'][^>]*>.*?</div>\s*</div>', '', clean, flags=re.DOTALL)
                        
                        # 2. Strip popunder ads and trackers
                        clean = re.sub(r'<script[^>]*zeugmatacket[^>]*>.*?</script>', '', clean, flags=re.DOTALL)
                        clean = re.sub(r'aclib\.runPop\([^)]*\);?', '', clean)
                        clean = re.sub(r'//gg\.zeugmatacket\.com/[^\"\']*', '', clean)
                        
                        # 3. Add full autoplay & media permissions & autoplay query parameters
                        clean = re.sub(r'allow=[\"\'][^\"\']*[\"\']', 'allow="accelerometer; autoplay *; clipboard-write *; encrypted-media *; gyroscope; picture-in-picture *; web-share"', clean)
                        clean = clean.replace('ntvplayer.html?id=', 'ntvplayer.html?autoplay=1&muted=1&id=')
                        
                        # 4. Inject autoplay trigger
                        clean = clean.replace('</body>', '''
<script>
(function() {
    function tryPlay() {
        var ifr = document.getElementById("streamIframe");
        if (ifr && ifr.contentWindow) {
            try {
                ifr.focus();
                ifr.contentWindow.postMessage('{"event":"command","func":"playVideo","args":""}', '*');
                ifr.contentWindow.postMessage('play', '*');
            } catch(e) {}
        }
    }
    window.addEventListener("DOMContentLoaded", tryPlay);
    window.addEventListener("load", tryPlay);
    document.addEventListener("click", tryPlay);
    setTimeout(tryPlay, 500);
    setTimeout(tryPlay, 1500);
})();
</script>
</body>''')
                        player_html = clean
                        STREAM_PLAYER_CACHE[cache_key] = {"html": player_html, "time": now}
                except Exception as e:
                    print("Error generating stream player:", e)
            
            if player_html:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(player_html.encode("utf-8"))
            else:
                self.send_response(302)
                self.send_header("Location", f"https://ntv.cx/watch/{server_name}/{match_id}")
                self.end_headers()
            return
        elif parsed.path == "/api/stream-embed":
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            match_id = query.get("id", [""])[0]
            server_name = query.get("server", ["falcon"])[0]
            embed_url = None
            if match_id:
                try:
                    target_url = f"https://ntv.cx/watch/{server_name}/{match_id}"
                    req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
                    html = urllib.request.urlopen(req, timeout=6).read().decode("utf-8")
                    m = re.search(r'src=[\"\'](/embed\?t=[^\"\']+)[\"\']', html)
                    if m:
                        embed_url = "https://ntv.cx" + m.group(1)
                except Exception as e:
                    print("Error resolving embed:", e)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({"success": bool(embed_url), "embed_url": embed_url}).encode("utf-8"))
            return
        elif parsed.path == "/api/match-goals":
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            uuid = query.get("uuid", [""])[0]
            home = query.get("home", [""])[0]
            away = query.get("away", [""])[0]
            min_goals = int(query.get("min_goals", [0])[0] or 0)
            goals = []
            cards = []
            if uuid:
                goals = fetch_match_goals(home, away, uuid, min_goals=min_goals)
                cards = get_cached_match_cards(uuid)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "goals": goals, "cards": cards}, ensure_ascii=False).encode("utf-8"))
            return
        elif parsed.path == "/api/odds":
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            home = query.get("home", [""])[0]
            away = query.get("away", [""])[0]
            odds_result = None
            try:
                import build_desktop
                now = time.time()
                if not hasattr(PremierLeagueHandler, "_odds_cache") or (now - getattr(PremierLeagueHandler, "_odds_cache_time", 0) > 300):
                    PremierLeagueHandler._odds_cache = build_desktop.fetch_iddaa_odds()
                    PremierLeagueHandler._odds_cache_time = now
                if home and away:
                    odds_result = build_desktop.match_odds(home, away, PremierLeagueHandler._odds_cache)
            except Exception as e:
                print("Error fetching odds on api:", e)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({"success": bool(odds_result), "odds": odds_result}, ensure_ascii=False).encode("utf-8"))
            return
        elif parsed.path == "/api/data":
            data = fetch_sahadan()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif parsed.path == "/" or parsed.path == "":
            self.path = "/index.html"
            return http.server.SimpleHTTPRequestHandler.do_GET(self)
        else:
            return http.server.SimpleHTTPRequestHandler.do_GET(self)

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), PremierLeagueHandler) as httpd:
        print(f"Server çalışıyor: http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer durduruldu.")
