import urllib.request
import json
import re
import os
import sys
import time
try:
    import requests
except ImportError:
    requests = None

LEAGUES = [
    {
        "id": "super-lig-tr",
        "name": "Trendyol Süper Lig",
        "country": "Türkiye",
        "url": "https://www.sahadan.com/lig/trendyol-super-lig/482ofyysbdbeoxauk19yg7tdt"
    },
    {
        "id": "trendyol-1-lig",
        "name": "Trendyol 1. Lig",
        "country": "Türkiye",
        "url": "https://www.sahadan.com/lig/trendyol-1-lig/2o9svokc5s7diish3ycrzk7jm?round_id=95268"
    },
    {
        "id": "sampiyonlar-ligi",
        "name": "Şampiyonlar Ligi",
        "country": "Avrupa",
        "url": "https://www.sahadan.com/lig/sampiyonlar-ligi/4oogyu6o156iphvdvphwpck10?round_id=95533"
    },
    {
        "id": "avrupa-ligi",
        "name": "Avrupa Ligi",
        "country": "Avrupa",
        "url": "https://www.sahadan.com/lig/avrupa-ligi/4c1nfi2j1m731hcay25fcgndq?round_id=94654"
    },
    {
        "id": "konferans-ligi",
        "name": "Konferans Ligi",
        "country": "Avrupa",
        "url": "https://www.sahadan.com/lig/konferans-ligi/c7b8o53flg36wbuevfzy3lb10?round_id=95377"
    },
    {
        "id": "premier-lig-en",
        "name": "Premier Lig",
        "country": "İngiltere",
        "url": "https://www.sahadan.com/lig/premier-lig/2kwbbcootiqqgmrzs6o5inle5?round_id=94794"
    },
    {
        "id": "championship",
        "name": "Championship",
        "country": "İngiltere",
        "url": "https://www.sahadan.com/lig/championship/7ntvbsyq31jnzoqoa8850b9b8?round_id=94876"
    },
    {
        "id": "laliga",
        "name": "LaLiga",
        "country": "İspanya",
        "url": "https://www.sahadan.com/lig/laliga/34pl8szyvrbwcmfkuocjm3r6t?round_id=95118"
    },
    {
        "id": "serie-a",
        "name": "Serie A",
        "country": "İtalya",
        "url": "https://www.sahadan.com/lig/serie-a/1r097lpxe0xn03ihb7wi98kao?round_id=95212"
    },
    {
        "id": "bundesliga",
        "name": "Bundesliga",
        "country": "Almanya",
        "url": "https://www.sahadan.com/lig/bundesliga/6by3h89i2eykc341oz7lv1ddd?round_id=94362"
    },
    {
        "id": "ligue-1",
        "name": "Ligue 1",
        "country": "Fransa",
        "url": "https://www.sahadan.com/lig/ligue-1/dm5ka0os1e3dxcp3vh05kmp33?round_id=95454"
    },
    {
        "id": "eredivisie",
        "name": "Eredivisie",
        "country": "Hollanda",
        "url": "https://www.sahadan.com/lig/eredivisie/akmkihra9ruad09ljapsm84b3?round_id=95210"
    },
    {
        "id": "premier-lig-pt",
        "name": "Primeira Liga",
        "country": "Portekiz",
        "url": "https://www.sahadan.com/lig/premier-lig/8yi6ejjd1zudcqtbn07haahg6?round_id=94364"
    },
    {
        "id": "pro-lig-be",
        "name": "Pro Lig",
        "country": "Belçika",
        "url": "https://www.sahadan.com/lig/pro-lig/4zwgbb66rif2spcoeeol2motx?round_id=95776"
    },
    {
        "id": "premiership-sc",
        "name": "Premiership",
        "country": "İskoçya",
        "url": "https://www.sahadan.com/lig/premiership/e21cf135btr8t3upw0vl6n6x0?round_id=94447"
    },
    {
        "id": "super-lig-dk",
        "name": "Superliga",
        "country": "Danimarka",
        "url": "https://www.sahadan.com/lig/super-lig/29actv1ohj8r10kd9hu0jnb0n?round_id=94865"
    },
    {
        "id": "super-lig-ch",
        "name": "Super League",
        "country": "İsviçre",
        "url": "https://www.sahadan.com/lig/super-lig/e0lck99w8meo9qoalfrxgo33o?round_id=94518"
    },
    {
        "id": "eliteserien",
        "name": "Eliteserien",
        "country": "Norveç",
        "url": "https://www.sahadan.com/lig/eliteserien/9ynnnx1qmkizq1o3qr3v0nsuk?round_id=92120"
    },
    {
        "id": "czech-liga",
        "name": "Chance Liga",
        "country": "Çekya",
        "url": "https://www.sahadan.com/lig/czech-liga/bu1l7ckihyr0errxw61p0m05?round_id=95226"
    },
    {
        "id": "bundesliga-at",
        "name": "Bundesliga",
        "country": "Avusturya",
        "url": "https://www.sahadan.com/lig/bundesliga/5c96g1zm7vo5ons9c42uy2w3r?area=hcs1bd49qliqqity6o268jyt"
    },
    {
        "id": "fa-cup",
        "name": "FA Cup",
        "country": "İngiltere",
        "url": "https://www.sahadan.com/lig/fa-cup/2hj3286pqov1g1g59k2t2qcgm/fikstur",
        "min_date": "2026-11-15"
    },
    {
        "id": "lig-kupasi",
        "name": "Lig Kupası",
        "country": "İngiltere",
        "url": "https://www.sahadan.com/lig/lig-kupasi/725gd73msyt08xm76v7gkxj7u/fikstur"
    },
    {
        "id": "kral-kupasi",
        "name": "Kral Kupası",
        "country": "İspanya",
        "url": "https://www.sahadan.com/lig/kral-kupasi/apdwh753fupxheygs8seahh7x/fikstur"
    },
    {
        "id": "coppa-italia",
        "name": "İtalya Kupası",
        "country": "İtalya",
        "url": "https://www.sahadan.com/lig/coppa-italia/6694fff47wqxl10lrd9tb91f8/fikstur"
    },
    {
        "id": "fransa-kupasi",
        "name": "Fransa Kupası",
        "country": "Fransa",
        "url": "https://www.sahadan.com/lig/kupa/3n9mk5b2mxmq831wfmv6pu86i/fikstur"
    },
    {
        "id": "almanya-kupasi",
        "name": "Almanya Kupası",
        "country": "Almanya",
        "url": "https://www.sahadan.com/lig/kupa/486rhdgz7yc0sygziht7hje65/fikstur"
    },
    {
        "id": "turkiye-kupasi",
        "name": "Ziraat Türkiye Kupası",
        "country": "Türkiye",
        "url": "https://www.sahadan.com/lig/ziraat-turkiye-kupasi/7af85xa75vozt2l4hzi6ryts7/fikstur"
    }
]

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(APP_DIR, "leagues_cache.json")
GOALS_CACHE_FILE = os.path.join(APP_DIR, "all_goals_cache.json")
CARDS_CACHE_FILE = os.path.join(APP_DIR, "all_cards_cache.json")
TV_CACHE_FILE = os.path.join(APP_DIR, "all_tv_cache.json")
DESKTOP_HTML = "/Users/onur/Desktop/futbol_ligleri.html"
TEMPLATE_HTML = os.path.join(APP_DIR, "index.html")
OUTPUT_HTML = os.path.join(APP_DIR, "dist", "index.html")  # GitHub Pages çıktısı

def parse_sahadan_league(target_url, min_date=None, max_retries=3):
    clean_url = target_url.strip().replace("\u2028", "").replace("\u2029", "")
    
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(clean_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache"
            })
            html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
            
            for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
                if "weeklyStandings" in script or "competition-main" in script:
                    data = json.loads(script)
                    memo = {}
                    def deep_resolve(val, depth=0):
                        if depth > 16: return None
                        if isinstance(val, int) and 0 <= val < len(data):
                            if val in memo: return memo[val]
                            raw = data[val]
                            if isinstance(raw, list) and len(raw) == 2 and raw[0] in ("ShallowReactive", "Reactive", "Set", "Map"):
                                res = deep_resolve(raw[1], depth + 1)
                                memo[val] = res
                                return res
                            if isinstance(raw, dict):
                                res = {k: deep_resolve(v, depth + 1) for k, v in raw.items()}
                                memo[val] = res
                                return res
                            if isinstance(raw, list):
                                res = [deep_resolve(item, depth + 1) for item in raw]
                                memo[val] = res
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

                    # Standings
                    rankings = comp_data.get("rankings", {}) or {}
                    total_table = rankings.get("total", [{}])[0].get("table", []) if isinstance(rankings.get("total"), list) and len(rankings.get("total")) > 0 else []
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

                    # Weeks / Fixtures
                    clean_weeks = []
                    gamesets = comp_data.get("gamesets", []) or []
                    
                    for idx, gs in enumerate(gamesets):
                        week_num = gs.get("name")
                        matches = []
                        for m in gs.get("matches", []):
                            m_dt = m.get("date_time_utc")
                            if min_date and m_dt:
                                dt_str = str(m_dt)[:10]
                                if dt_str < min_date:
                                    continue
                            tA = m.get("team_A", {}) or {}
                            tB = m.get("team_B", {}) or {}
                            st = m.get("status", "")
                            matches.append({
                                "id": m.get("id"),
                                "uuid": m.get("uuid"),
                                "date_time": m_dt,
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
                        if matches or not min_date:
                            clean_weeks.append({
                                "week": week_num,
                                "matches": matches
                            })

                    # Calculate real current active week:
                    # 1. Prefer week with 'Playing' or 'Live'
                    current_week_idx = 0
                    found_active = False
                    for idx, gs in enumerate(clean_weeks):
                        if any(m.get("status") in ("Playing", "Live") for m in gs.get("matches", [])):
                            current_week_idx = idx
                            found_active = True
                            break

                    # 2. If none live, find week with unplayed 'Fixture' matches closest to current date
                    if not found_active:
                        import datetime
                        today = datetime.datetime.now().date()
                        min_diff = 9999
                        for idx, gs in enumerate(clean_weeks):
                            unplayed = [m for m in gs.get("matches", []) if m.get("status") == "Fixture"]
                            for m in unplayed:
                                dt_str = (m.get("date_time") or "")[:10]
                                if dt_str:
                                    try:
                                        m_date = datetime.date.fromisoformat(dt_str)
                                        diff = abs((m_date - today).days)
                                        if diff < min_diff:
                                            min_diff = diff
                                            current_week_idx = idx
                                            found_active = True
                                    except:
                                        pass

                    # 3. Fallback: if no fixture matches, last week with played matches
                    if not found_active:
                        for idx, gs in enumerate(clean_weeks):
                            if any(m.get("status") == "Played" for m in gs.get("matches", [])):
                                current_week_idx = idx

                    return {
                        "success": True,
                        "competition_title": comp_data.get("competition", {}).get("name", ""),
                        "current_week_index": current_week_idx,
                        "standings": clean_standings,
                        "weeks": clean_weeks
                    }
        except Exception as e:
            time.sleep(1.0)
            
    return None

def fetch_live_scores_today():
    print(f"Sahadan.com üzerinden günün canlı maçları ({len(LEAGUES)} Lig/Kupa) taranıyor...")
    league_uuids = {}
    for l in LEAGUES:
        url_path = l["url"].split("?")[0].rstrip("/")
        parts = [p for p in url_path.split("/") if p and p != "fikstur"]
        if parts:
            uuid = parts[-1]
            league_uuids[uuid] = l

    today_matches = []
    seen_match_ids = set()

    try:
        url = "https://www.sahadan.com/canli-sonuclar"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Cache-Control": "no-cache"
        }
        req = urllib.request.Request(url, headers=headers)
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8")
        m = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', html)
        if m:
            nuxt = json.loads(m.group(1))
            memo = {}
            def deep_resolve(val, depth=0):
                if depth > 40: return val
                if isinstance(val, int):
                    if val in memo: return memo[val]
                    if 0 <= val < len(nuxt):
                        raw = nuxt[val]
                        if isinstance(raw, (dict, list)):
                            memo[val] = raw
                            if isinstance(raw, dict):
                                res = {k: deep_resolve(v, depth+1) for k, v in raw.items()}
                            else:
                                if len(raw) == 2 and raw[0] == 'ShallowReactive':
                                    res = deep_resolve(raw[1], depth+1)
                                else:
                                    res = [deep_resolve(item, depth+1) for item in raw]
                            memo[val] = res
                            return res
                        return raw
                elif isinstance(val, dict):
                    return {k: deep_resolve(v, depth+1) for k, v in val.items()}
                elif isinstance(val, list):
                    if len(val) == 2 and val[0] == 'ShallowReactive':
                        return deep_resolve(val[1], depth+1)
                    return [deep_resolve(v, depth+1) for v in val]
                return val

            root = deep_resolve(2)
            for k, v in root.items():
                if isinstance(v, dict) and "data" in v and "areas" in v["data"]:
                    areas = v["data"]["areas"]
                    for a in areas:
                        for comp in a.get("competitions", []):
                            cuuid = comp.get("uuid")
                            if cuuid in league_uuids:
                                    linfo = league_uuids[cuuid]
                                    min_date = linfo.get("min_date")
                                    for m_item in comp.get("matches", []):
                                        mid = m_item.get("id")
                                        if mid in seen_match_ids:
                                            continue
                                        raw_dt = m_item.get("date_time_utc") or m_item.get("date_time")
                                        if min_date and raw_dt:
                                            dt_str = str(raw_dt)[:10]
                                            if dt_str < min_date:
                                                continue
                                        seen_match_ids.add(mid)
                                        tA = m_item.get("team_A", {}) or {}
                                        tB = m_item.get("team_B", {}) or {}
                                        raw_dt = m_item.get("date_time_utc")
                                        raw_time = m_item.get("match_time")
                                        local_time = raw_time
                                        if raw_dt and len(str(raw_dt)) >= 16:
                                            try:
                                                import datetime
                                                dt_utc = datetime.datetime.fromisoformat(str(raw_dt).replace(" ", "T"))
                                                dt_tr = dt_utc + datetime.timedelta(hours=3)
                                                local_time = dt_tr.strftime("%H:%M")
                                            except:
                                                pass
                                        elif raw_time and ":" in str(raw_time):
                                            try:
                                                parts = str(raw_time).split(":")
                                                hh = (int(parts[0]) + 3) % 24
                                                local_time = f"{hh:02d}:{parts[1]}"
                                            except:
                                                pass

                                        raw_st = str(m_item.get("status") or "").strip()
                                        raw_pr = str(m_item.get("period") or "").strip()
                                        is_m_ft = raw_st.lower() in ("played", "ms", "ft", "finished", "bitti") or raw_pr.lower() in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")

                                        today_matches.append({
                                            "league_id": linfo["id"],
                                            "league_name": linfo["name"],
                                            "league_country": linfo["country"],
                                            "match_id": mid,
                                            "uuid": m_item.get("uuid"),
                                            "match_uuid": m_item.get("match_uuid") or m_item.get("uuid"),
                                            "date_time": raw_dt,
                                            "match_time": local_time,
                                            "status": "Played" if is_m_ft else raw_st,
                                            "period": raw_pr,
                                            "minute": m_item.get("minute"),
                                            "minute_extra": m_item.get("minute_extra"),
                                            "home_team": tA.get("name") if isinstance(tA, dict) else str(tA),
                                            "away_team": tB.get("name") if isinstance(tB, dict) else str(tB),
                                            "home_score": m_item.get("fts_A"),
                                            "away_score": m_item.get("fts_B"),
                                            "half_time_home": m_item.get("hts_A"),
                                            "half_time_away": m_item.get("hts_B"),
                                            "rc_home": (m_item.get("extras") or {}).get("team_A_redcards") or m_item.get("rc_A", 0) or 0,
                                            "rc_away": (m_item.get("extras") or {}).get("team_B_redcards") or m_item.get("rc_B", 0) or 0,
                                            "rc_A": (m_item.get("extras") or {}).get("team_A_redcards") or m_item.get("rc_A", 0) or 0,
                                            "rc_B": (m_item.get("extras") or {}).get("team_B_redcards") or m_item.get("rc_B", 0) or 0,
                                        })
            print(f" ✓ Canlı skor bülteninden {len(LEAGUES)} lige ait toplam {len(today_matches)} maç listelendi.")
    except Exception as e:
        print(f" ! Canlı sonuçlar taranırken hata: {e}")

    return today_matches

import html as html_parser
from concurrent.futures import ThreadPoolExecutor
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

def fetch_match_goals_and_cards(home, away, uuid):
    if not uuid:
        return [], []
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
            return [], []
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
                s_name = scorer.get('name') or scorer.get('display_name') or ''
                if str(s_name).strip().lower() in ('bilinmiyor', 'unknown', 'none', 'null'):
                    s_name = ''
                a_name = assist.get('name') or assist.get('display_name') or ''
                if str(a_name).strip().lower() in ('bilinmiyor', 'unknown', 'none', 'null'):
                    a_name = ''
                goals.append({
                    'type': t,
                    'minute': ev.get('minute'),
                    'extra_min': ev.get('minute_extra'),
                    'team': ev.get('team'),
                    'scorer': s_name,
                    'assist': a_name,
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
        return goals, cards
    except Exception as e:
        return [], []

def fetch_match_goals(home, away, uuid):
    g, _ = fetch_match_goals_and_cards(home, away, uuid)
    return g

def fetch_match_tv_channels(home, away, uuid):
    if not uuid:
        return []
    slug = f"{to_sahadan_slug(home)}-vs-{to_sahadan_slug(away)}"
    url = f"https://www.sahadan.com/mac/{slug}/{uuid}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Cache-Control": "no-cache"
        })
        html = urllib.request.urlopen(req, timeout=6).read().decode("utf-8")
        m = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', html)
        if not m:
            return []
        data = json.loads(m.group(1))
        channels = []
        for item in data:
            if isinstance(item, dict) and "tr" in item:
                tr_val = item["tr"]
                items_to_check = tr_val if isinstance(tr_val, list) else (data[tr_val] if isinstance(tr_val, int) and tr_val < len(data) and isinstance(data[tr_val], list) else [])
                for ch_ptr in items_to_check:
                    ch_obj = data[ch_ptr] if isinstance(ch_ptr, int) and ch_ptr < len(data) else ch_ptr
                    if isinstance(ch_obj, dict) and "name" in ch_obj:
                        n_ptr = ch_obj["name"]
                        cname = data[n_ptr] if isinstance(n_ptr, int) and n_ptr < len(data) else n_ptr
                        if cname and isinstance(cname, str) and cname not in channels:
                            channels.append(cname)
        return channels
    except Exception:
        return []


def norm_team_name(s):
    if not s:
        return ""
    s = html_parser.unescape(s).replace("İ", "i").replace("I", "i").lower()
    # Normalize common abbreviations and prefixes
    s = re.sub(r"\bo\.?\s*h\.?\s*", "oh ", s)
    s = re.sub(r"\bç\.?\s*", " ", s)  # Ç. Rizespor -> Rizespor
    s = re.sub(r"\bfatih\s+", "f ", s)
    abbr_map = {
        r"\br\.\s*": "real ",
        r"\be\.\s*": "eintracht ",
        r"\bv\.\s*": "vitoria ",
        r"\bfortuna\s+": "f ",
        r"\bf\.\s*": "f ",
        r"\bh\.\s*kralove\b": "hradec kralove",
        r"\bman\.\s*": "manchester ",
        r"\bman\s+": "manchester ",
        r"\bdep\.\s*": "deportivo ",
        r"\bath\.\s*": "athletic ",
        r"\batl\.\s*": "atletico "
    }
    for pattern, repl in abbr_map.items():
        s = re.sub(pattern, repl, s)

    # Remove common club suffixes/prefixes
    s = re.sub(r"\b(sk|fk|fc|cf|cd|sc|as|w|praia)\b\.?", " ", s)

    ch_map = {'ü': 'u', 'ö': 'o', 'ı': 'i', 'ş': 's', 'ç': 'c', 'ğ': 'g', 'é': 'e', 'è': 'e', 'á': 'a', 'à': 'a', 'ä': 'a', 'ø': 'o', 'æ': 'ae', 'í': 'i', 'ó': 'o', 'ú': 'u'}
    for ch in [' ', '.', '-', '\t', '\'', '’', 'ü', 'ö', 'ı', 'ş', 'ç', 'ğ', 'é', 'è', 'á', 'à', 'ä', 'ø', 'æ', 'í', 'ó', 'ú', 'club', 'de']:
        s = s.replace(ch, ch_map.get(ch, ''))
    return s

def fetch_iddaa_odds(all_target_teams=None):
    print("İddaa bülteninden (Sahadan, iddaa.com & Nesine resmi bülteni) güncel ve ileri tarihli oranlar taranıyor...")
    all_odds = {}

    headers_sahadan = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache"
    }
    headers_iddaa = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Origin": "https://www.iddaa.com",
        "Referer": "https://www.iddaa.com/"
    }

    # 1. iddaa.com Resmi Sportsbook API (1. BİRİNCİL KAYNAK: Resmi bülten & anlık oranlar)
    try:
        req_i = urllib.request.Request("https://sportsbookv2.iddaa.com/sportsbook/events?type=1&sportId=1", headers=headers_iddaa)
        events = json.loads(urllib.request.urlopen(req_i, timeout=12).read().decode("utf-8")).get("data", {}).get("events", [])

        for e in events:
            if e.get("sid") == 1:
                hn = e.get("hn", "")
                an = e.get("an", "")
                if not hn or not an:
                    continue
                odds = {}
                for m in e.get("m", []):
                    st = m.get("st")
                    sov = str(m.get("sov") or "").strip()
                    o_dict = {str(o.get("n")): str(o.get("odd") or o.get("wodd")) for o in m.get("o", [])}
                    draw_key = "0" if "0" in o_dict else ("X" if "X" in o_dict else None)
                    # st == 4 is Maç Sonucu
                    if st == 4 and "1" in o_dict and "2" in o_dict and draw_key:
                        odds["ms1"] = o_dict["1"]
                        odds["ms0"] = o_dict[draw_key]
                        odds["ms2"] = o_dict["2"]
                    # st == 14 and sov == "2.5" is 2.5 Alt/Üst
                    elif st == 14 and sov == "2.5" and "Alt" in o_dict and "Üst" in o_dict:
                        odds["alt"] = o_dict["Alt"]
                        odds["ust"] = o_dict["Üst"]
                    # st == 131 is KG Var/Yok
                    elif st == 131 and "Var" in o_dict and "Yok" in o_dict:
                        odds["kg_var"] = o_dict["Var"]
                        odds["kg_yok"] = o_dict["Yok"]
                if "ms1" in odds and "ms0" in odds and "ms2" in odds:
                    all_odds[(hn, an)] = odds

        print(f" ✓ 1. Kaynak (iddaa.com resmi bülteni) senkronizasyonu tamamlandı: {len(all_odds)} maç.")
    except Exception as e:
        print(f" ! iddaa.com taranırken hata: {e}")

    # 2. Nesine / İddaa Resmi Full Ön Bülten (Haftalık ve ertesi gün tüm maçları)
    try:
        url_nesine = "https://cdnbulten.nesine.com/api/bulten/getprebultenfull"
        req_n = urllib.request.Request(url_nesine, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        res_n = json.loads(urllib.request.urlopen(req_n, timeout=12).read().decode("utf-8"))
        events_n = res_n.get("sg", {}).get("EA", [])
        for e in events_n:
            hn = e.get("HN", "").strip()
            an = e.get("AN", "").strip()
            if not hn or not an:
                continue
            odds = {}
            for m in e.get("MA", []):
                mtid = m.get("MTID")
                sov = m.get("SOV")
                oca = {o.get("N"): str(o.get("O")) for o in m.get("OCA", [])}
                # Maç Sonucu (1-X-2)
                if mtid == 1 and 1 in oca and 2 in oca and 3 in oca:
                    odds["ms1"] = oca[1]
                    odds["ms0"] = oca[2]
                    odds["ms2"] = oca[3]
                # 2.5 Alt/Üst
                elif mtid == 12 and sov == 2.5 and 1 in oca and 2 in oca:
                    odds["alt"] = oca[1]
                    odds["ust"] = oca[2]
                # KG Var/Yok
                elif mtid == 49 and 1 in oca and 2 in oca:
                    odds["kg_var"] = oca[1]
                    odds["kg_yok"] = oca[2]
            if "ms1" in odds and "ms0" in odds and "ms2" in odds:
                if (hn, an) not in all_odds:
                    all_odds[(hn, an)] = odds
                else:
                    for k, v in odds.items():
                        if k not in all_odds[(hn, an)]:
                            all_odds[(hn, an)][k] = v
        print(f" ✓ 2. Kaynak (Nesine resmi bülteni) eklendi. Toplam havuz: {len(all_odds)} maç.")
    except Exception as e:
        print(f" ! Nesine bülteni okunurken hata: {e}")

    # 3. Sahadan.com Nuxt Programı (Bugün ve bültendeki tüm maçlar)
    try:
        url_s = "https://www.sahadan.com/iddaa-programi"
        req_s = urllib.request.Request(url_s, headers=headers_sahadan)
        html_s = urllib.request.urlopen(req_s, timeout=12).read().decode("utf-8")
        m = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', html_s)
        if m:
            nuxt_data = json.loads(m.group(1))
            def resolve(val):
                if isinstance(val, int) and 0 <= val < len(nuxt_data):
                    return nuxt_data[val]
                return val

            for item in nuxt_data:
                if isinstance(item, dict) and "team_A" in item and "team_B" in item and "markets" in item:
                    tA = resolve(item["team_A"])
                    tB = resolve(item["team_B"])
                    if not isinstance(tA, str) or not isinstance(tB, str):
                        continue
                    markets = resolve(item["markets"])
                    odds = {}
                    if isinstance(markets, list):
                        for m_idx in markets:
                            m_val = resolve(m_idx)
                            if not isinstance(m_val, dict):
                                continue
                            mi = resolve(m_val.get("i"))
                            o_list = resolve(m_val.get("o"))
                            if isinstance(o_list, list):
                                for opt_idx in o_list:
                                    opt = resolve(opt_idx)
                                    if isinstance(opt, dict):
                                        b = resolve(opt.get("b"))
                                        # Sadece b == 14 resmi İddaa oranıdır (b == 15 Kral Oran/promosyondur)
                                        if b != 14:
                                            continue
                                        lines = resolve(opt.get("l"))
                                        if isinstance(lines, list):
                                            for line_idx in lines:
                                                line_d = resolve(line_idx)
                                                if isinstance(line_d, dict):
                                                    name = resolve(line_d.get("n"))
                                                    val = resolve(line_d.get("v"))
                                                    if mi == 1:
                                                        if name == "1":
                                                            odds["ms1"] = str(val)
                                                        elif name in ("0", "X", "x"):
                                                            odds["ms0"] = str(val)
                                                        elif name == "2":
                                                            odds["ms2"] = str(val)
                                                    elif mi == 10:
                                                        if name == "Alt":
                                                            odds["alt"] = str(val)
                                                        elif name == "Üst":
                                                            odds["ust"] = str(val)
                                                    elif mi == 6:
                                                        if name == "Var":
                                                            odds["kg_var"] = str(val)
                                                        elif name == "Yok":
                                                            odds["kg_yok"] = str(val)
                    if "ms1" in odds and "ms0" in odds and "ms2" in odds:
                        if (tA, tB) not in all_odds:
                            all_odds[(tA, tB)] = odds
                        else:
                            for k, v in odds.items():
                                if k not in all_odds[(tA, tB)]:
                                    all_odds[(tA, tB)][k] = v
        print(f" ✓ 3. Kaynak (Sahadan bülteni) tamamlandı. Toplam havuz: {len(all_odds)} maç.")
    except Exception as e:
        print(f" ! Sahadan bülteni okunurken hata: {e}")

    # Build clean lookup dictionary and pre-tokenized index
    clean_dict = {}
    pretokenized_list = []
    for (tA, tB), odds in all_odds.items():
        nA = norm_team_name(tA)
        nB = norm_team_name(tB)
        clean_dict[f"{nA}__{nB}"] = odds
        tokA = set(get_team_tokens(tA))
        tokB = set(get_team_tokens(tB))
        if tokA and tokB:
            pretokenized_list.append((tokA, tokB, odds))

    return {"exact": clean_dict, "tokenized": pretokenized_list, "raw": all_odds}

def get_team_tokens(name):
    if not name:
        return []
    s = html_parser.unescape(name).replace("İ", "i").replace("I", "i").lower()
    ch_map = {'ü': 'u', 'ö': 'o', 'ı': 'i', 'ş': 's', 'ç': 'c', 'ğ': 'g', 'é': 'e', 'è': 'e', 'á': 'a', 'à': 'a', 'ä': 'a', 'ø': 'o', 'æ': 'ae', 'í': 'i', 'ó': 'o', 'ú': 'u'}
    for k, v in ch_map.items():
        s = s.replace(k, v)
    words = re.findall(r'[a-z0-9]+', s)
    stop_words = {'sk', 'fk', 'fc', 'cf', 'cd', 'sc', 'as', 'w', 'club', 'de', 'spor', 'kulubu', 'kulub', 'women', 'united', 'city', 'c', 'praia'}
    filtered = [w for w in words if w not in stop_words and len(w) >= 3]
    if not filtered:
        filtered = [w for w in words if w not in stop_words and len(w) >= 2]
    return filtered

def match_odds(tA, tB, odds_lookup):
    if not odds_lookup:
        return None
    exact_dict = odds_lookup.get("exact", {})
    nA = norm_team_name(tA)
    nB = norm_team_name(tB)
    k = f"{nA}__{nB}"
    if k in exact_dict:
        return exact_dict[k]
    
    tokensA = set(get_team_tokens(tA))
    tokensB = set(get_team_tokens(tB))
    if not tokensA or not tokensB:
        return None

    # Pre-tokenized instant match
    for c_tokensA, c_tokensB, ov in odds_lookup.get("tokenized", []):
        if (tokensA & c_tokensA) and (tokensB & c_tokensB):
            return ov
    return None
def fetch_tv_broadcasts():
    print("Sahadan.com TV Programı (Canlı Yayın Akışı) taranıyor...")
    tv_map = {}
    try:
        url = "https://www.sahadan.com/tv-programi"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Cache-Control": "no-cache"
        }
        html_text = ""
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as response:
                html_text = response.read().decode("utf-8")
        except:
            if requests:
                resp = requests.get(url, headers=headers, timeout=12)
                if resp.status_code == 200:
                    html_text = resp.text
        if html_text:
            scripts = re.findall(r'<script[^>]*>(.*?)</script>', html_text, re.DOTALL)
            for s in scripts:
                s_strip = s.strip()
                if s_strip.startswith('[["ShallowReactive"') and "tv-broadcasts" in s_strip:
                    data = json.loads(s_strip)
                    memo = {}
                    def deep_resolve(val, depth=0):
                        if depth > 40: return None
                        if isinstance(val, int):
                            if 0 <= val < len(data):
                                if val in memo: return memo[val]
                                raw = data[val]
                                if isinstance(raw, list) and len(raw) == 2 and raw[0] in ('ShallowReactive', 'Reactive', 'Set', 'Map'):
                                    res = deep_resolve(raw[1], depth + 1)
                                    memo[val] = res
                                    return res
                                if isinstance(raw, dict):
                                    res = {k: deep_resolve(v, depth + 1) for k, v in raw.items()}
                                    memo[val] = res
                                    return res
                                if isinstance(raw, list):
                                    res = [deep_resolve(item, depth + 1) for item in raw]
                                    memo[val] = res
                                    return res
                                return raw
                        elif isinstance(val, dict):
                            return {k: deep_resolve(v, depth + 1) for k, v in val.items()}
                        elif isinstance(val, list):
                            return [deep_resolve(v, depth + 1) for v in val]
                        return val

                    resolved = deep_resolve(2)
                    broadcasts = resolved.get("tv-broadcasts", {}).get("data", {}).get("broadcasts", [])
                    for b in broadcasts:
                        chs = [c.get("name") for c in (b.get("channels") or []) if c.get("name")]
                        if not chs:
                            continue
                        m = b.get("match") or {}
                        mid = m.get("id")
                        muuid = m.get("uuid")
                        mname = m.get("name") or b.get("name") or ""

                        if mid:
                            tv_map[mid] = chs
                            tv_map[str(mid)] = chs
                        if muuid:
                            tv_map[muuid] = chs
                        if " - " in mname:
                            parts = mname.split(" - ")
                            h = norm_team_name(parts[0].strip())
                            a = norm_team_name(parts[1].strip())
                            if h and a:
                                tv_map[f"{h}___{a}"] = chs
                    break
        print(f" ✓ Sahadan TV Programından {len(tv_map)} anahtar ile yayın kanalları toplandı.")
    except Exception as e:
        print(f" ! TV Programı taranırken hata: {e}")
    return tv_map

def build_desktop_html():
    print(f"Sahadan.com üzerinden {len(LEAGUES)} ligin verileri taranıyor...")
    cached_data = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
        except:
            cached_data = {}

    for l in LEAGUES:
        lid = l["id"]
        name = l["name"]
        country = l["country"]
        url = l["url"]
        min_date = l.get("min_date")

        data = parse_sahadan_league(url, min_date=min_date)
        if data and (len(data.get("standings", [])) > 0 or len(data.get("weeks", [])) > 0 or "kupa" in lid or "cup" in lid):
            cached_data[lid] = data
            s_count = len(data.get("standings", []))
            w_count = len(data.get("weeks", []))
            print(f" ✓ {name} ({country}): {s_count} Takım, {w_count} Hafta/Tur")
        else:
            if lid in cached_data:
                print(f" ! {name} ({country}): Canlı bağlantı gecikti, kayıtlı veri korundu.")
            else:
                print(f" ✗ {name} ({country}): Alınamadı!")
        time.sleep(0.3)

    # Collect all team names across leagues and cups to optimize iddaa querying
    all_target_teams = set()
    for lid, ldata in cached_data.items():
        for s in ldata.get("standings", []):
            all_target_teams.add(norm_team_name(s.get("name", "")))
        for w in ldata.get("weeks", []):
            for m in w.get("matches", []):
                h = m.get("home_team", {}).get("name")
                a = m.get("away_team", {}).get("name")
                if h: all_target_teams.add(norm_team_name(h))
                if a: all_target_teams.add(norm_team_name(a))

    # İddaa oranlarını çek ve eşleştir
    clean_odds_dict = fetch_iddaa_odds(all_target_teams)
    matched_odds = 0
    if clean_odds_dict:
        for lid, ldata in cached_data.items():
            for w in ldata.get("weeks", []):
                for m in w.get("matches", []):
                    o = match_odds(m["home_team"]["name"], m["away_team"]["name"], clean_odds_dict)
                    if o:
                        m["odds"] = o
                        matched_odds += 1
        print(f" ✓ Toplam {matched_odds} maça İddaa oranları (MS, 2.5 Alt/Üst, KG) eksiksiz eşleştirildi.")

    # Canlı Skorlar (Bugün liglerde oynanacak/oynanan tüm maçlar)
    today_live_matches = fetch_live_scores_today()
    if clean_odds_dict and today_live_matches:
        for tm in today_live_matches:
            o = match_odds(tm["home_team"], tm["away_team"], clean_odds_dict)
            if o:
                tm["odds"] = o

    # TV Yayın Akışı (Hangi kanal veriyor?)
    tv_map = fetch_tv_broadcasts()
    if tv_map:
        matched_tv_live = 0
        if today_live_matches:
            for tm in today_live_matches:
                mid = tm.get("match_id")
                muuid = tm.get("uuid") or tm.get("match_uuid")
                h_norm = norm_team_name(tm.get("home_team", ""))
                a_norm = norm_team_name(tm.get("away_team", ""))
                pair_key = f"{h_norm}___{a_norm}"

                chs = None
                if mid and mid in tv_map:
                    chs = tv_map[mid]
                elif muuid and muuid in tv_map:
                    chs = tv_map[muuid]
                elif pair_key in tv_map:
                    chs = tv_map[pair_key]

                if chs:
                    tm["tv_channels"] = chs
                    matched_tv_live += 1
        print(f" ✓ Toplam {matched_tv_live} canlı maça TV yayın kanalları eşleştirildi.")

        # Fikstür maçlarına da TV kanallarını bağla
        matched_tv_fix = 0
        for lid, ldata in cached_data.items():
            for w in ldata.get("weeks", []):
                for m in w.get("matches", []):
                    mid = m.get("id")
                    muuid = m.get("uuid")
                    h_norm = norm_team_name(m.get("home_team", {}).get("name", ""))
                    a_norm = norm_team_name(m.get("away_team", {}).get("name", ""))
                    pair_key = f"{h_norm}___{a_norm}"

                    chs = None
                    if mid and mid in tv_map:
                        chs = tv_map[mid]
                    elif muuid and muuid in tv_map:
                        chs = tv_map[muuid]
                    elif pair_key in tv_map:
                        chs = tv_map[pair_key]

                    if chs:
                        m["tv_channels"] = chs
                        matched_tv_fix += 1

    # TV önbelleğini yükle (Daha önce taranmış maç yayınları)
    tv_cache_dict = {}
    if os.path.exists(TV_CACHE_FILE):
        try:
            with open(TV_CACHE_FILE, "r", encoding="utf-8") as tf:
                tv_cache_dict = json.load(tf)
        except Exception:
            tv_cache_dict = {}

    # TV kanalı henüz bulunamamış güncel aktif hafta maçlarını tespit et (Öncelikle Şampiyonlar Ligi, Avrupa Kupaları ve Majör Ligler)
    priority_order = ["sampiyonlar-ligi", "avrupa-ligi", "konferans-ligi", "super-lig-tr", "premier-lig-en", "laliga", "serie-a", "bundesliga", "ligue-1"]
    ordered_lids = [lid for lid in priority_order if lid in cached_data] + [lid for lid in cached_data if lid not in priority_order]

    matches_needing_tv = []
    for lid in ordered_lids:
        ldata = cached_data[lid]
        curr_idx = ldata.get("current_week_index", 0)
        weeks = ldata.get("weeks", [])
        # Aktif hafta ve bir sonraki haftadaki başlanmamış maçları tara
        for check_idx in [curr_idx, curr_idx + 1]:
            if 0 <= check_idx < len(weeks):
                for m in weeks[check_idx].get("matches", []):
                    if not m.get("tv_channels"):
                        muuid = m.get("uuid")
                        if muuid and muuid in tv_cache_dict:
                            m["tv_channels"] = tv_cache_dict[muuid]
                        elif muuid:
                            matches_needing_tv.append(m)

    if matches_needing_tv:
        print(f"Aktif haftalardaki TV kanalı eksik {len(matches_needing_tv)} maç taranıyor...")
        def fetch_tv_for_m(m):
            muuid = m.get("uuid")
            h = m.get("home_team", {}).get("name", "")
            a = m.get("away_team", {}).get("name", "")
            try:
                chs = fetch_match_tv_channels(h, a, muuid)
                if chs:
                    m["tv_channels"] = chs
                    tv_cache_dict[muuid] = chs
                else:
                    tv_cache_dict[muuid] = []
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=12) as executor:
            list(executor.map(fetch_tv_for_m, matches_needing_tv))
        print(" ✓ Fikstür maçları için TV yayın kanalları başarıyla tarandı.")

    # Canlı maçlar listesine de bulunan TV kanallarını bağla
    if today_live_matches:
        for tm in today_live_matches:
            if not tm.get("tv_channels"):
                muuid = tm.get("uuid") or tm.get("match_uuid")
                if muuid and muuid in tv_cache_dict:
                    tm["tv_channels"] = tv_cache_dict[muuid]

    # TV önbelleğini tüm haftalardaki maçlara bağla
    for lid, ldata in cached_data.items():
        for w in ldata.get("weeks", []):
            for m in w.get("matches", []):
                muuid = m.get("uuid")
                if muuid and muuid in tv_cache_dict and not m.get("tv_channels"):
                    m["tv_channels"] = tv_cache_dict[muuid]

    # TV önbelleğini diske kaydet
    if tv_cache_dict:
        try:
            with open(TV_CACHE_FILE, "w", encoding="utf-8") as tf:
                json.dump(tv_cache_dict, tf, ensure_ascii=False)
        except Exception:
            pass
    # Canlı ve tamamlanan maçların gollerini ve kırmızı kartlarını pre-fetch et (Hızlı açılması için)
    goals_cache_dict = {}
    if os.path.exists(GOALS_CACHE_FILE):
        try:
            with open(GOALS_CACHE_FILE, "r", encoding="utf-8") as gf:
                goals_cache_dict = json.load(gf)
        except Exception:
            goals_cache_dict = {}

    cards_cache_dict = {}
    if os.path.exists(CARDS_CACHE_FILE):
        try:
            with open(CARDS_CACHE_FILE, "r", encoding="utf-8") as cf:
                cards_cache_dict = json.load(cf)
        except Exception:
            cards_cache_dict = {}

    scored_today_matches = []
    for tm in (today_live_matches or []):
        hs = tm.get("home_score")
        as_ = tm.get("away_score")
        muuid = tm.get("uuid") or tm.get("match_uuid")
        rc_count = (tm.get("rc_home") or 0) + (tm.get("rc_away") or 0)
        if (((hs is not None and hs > 0) or (as_ is not None and as_ > 0)) or rc_count > 0) and muuid:
            total_score = (hs or 0) + (as_ or 0)
            cached_goals = goals_cache_dict.get(muuid)
            cached_cards = cards_cache_dict.get(muuid)
            st = str(tm.get("status") or "").lower()
            is_finished = st in ("played", "ms", "ft", "finished", "bitti")
            # If match is currently ongoing, or if cached goals count doesn't match total score, or any scorer is missing, or cards missing, fetch fresh!
            has_missing = any(not g.get('scorer') for g in (cached_goals or []))
            cards_missing = rc_count > 0 and (not cached_cards or len(cached_cards) < rc_count)
            if not is_finished or not cached_goals or len(cached_goals) != total_score or has_missing or cards_missing:
                scored_today_matches.append(tm)
            else:
                tm["goals"] = cached_goals
                if cached_cards:
                    tm["cards"] = cached_cards
        elif muuid and muuid in cards_cache_dict and cards_cache_dict[muuid]:
            tm["cards"] = cards_cache_dict[muuid]

    if scored_today_matches:
        print(f"Gol veya kırmızı kart olan {len(scored_today_matches)} güncel maç için detaylar taranıyor...")
        def fetch_goals_for_tm(tm):
            muuid = tm.get("uuid") or tm.get("match_uuid")
            try:
                g, c = fetch_match_goals_and_cards(tm.get("home_team", ""), tm.get("away_team", ""), muuid)
                if g:
                    tm["goals"] = g
                    goals_cache_dict[muuid] = g
                if c:
                    tm["cards"] = c
                    cards_cache_dict[muuid] = c
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(fetch_goals_for_tm, scored_today_matches))
        print(f" ✓ Gol ve kart bilgileri başarıyla eşleştirildi.")

    # Fikstürdeki tamamlanmış maçlara da gol ve kart bilgilerini cache üzerinden doğrudan bağla
    matched_goals_fix = 0
    matched_cards_fix = 0
    for lid, ldata in cached_data.items():
        for w in ldata.get("weeks", []):
            for m in w.get("matches", []):
                muuid = m.get("uuid")
                if muuid:
                    if muuid in goals_cache_dict and goals_cache_dict[muuid]:
                        m["goals"] = goals_cache_dict[muuid]
                        matched_goals_fix += 1
                    if muuid in cards_cache_dict and cards_cache_dict[muuid]:
                        m["cards"] = cards_cache_dict[muuid]
                        matched_cards_fix += 1
    if matched_goals_fix > 0:
        print(f" ✓ Fikstürdeki {matched_goals_fix} maça kayıtlı gol bilgileri bağlandı.")
    if matched_cards_fix > 0:
        print(f" ✓ Fikstürdeki {matched_cards_fix} maça kayıtlı kart bilgileri bağlandı.")

    # Gol ve kart cache dosyasını kaydet
    if goals_cache_dict:
        try:
            with open(GOALS_CACHE_FILE, "w", encoding="utf-8") as gf:
                json.dump(goals_cache_dict, gf, ensure_ascii=False)
        except Exception:
            pass
    if cards_cache_dict:
        try:
            with open(CARDS_CACHE_FILE, "w", encoding="utf-8") as cf:
                json.dump(cards_cache_dict, cf, ensure_ascii=False)
        except Exception:
            pass

    # Cache yaz
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cached_data, f, ensure_ascii=False)

    payload = {
        "leagues_list": [
            {"id": l["id"], "name": l["name"], "country": l["country"]}
            for l in LEAGUES
        ],
        "default_league": "super-lig-tr",
        "live_scores_today": today_live_matches,
        "all_goals_cache": goals_cache_dict,
        "all_cards_cache": cards_cache_dict,
        "all_tv_cache": tv_cache_dict,
        "data": cached_data
    }

    with open(TEMPLATE_HTML, "r", encoding="utf-8") as f:
        template = f.read()

    # Socket.io v2 client kütüphanesini doğrudan HTML içine göm (Sıfır CDN bağımlılığı)
    socket_js_path = os.path.join(os.path.dirname(__file__), "socket.io.v2.slim.js")
    if os.path.exists(socket_js_path):
        with open(socket_js_path, "r", encoding="utf-8") as sf:
            socket_code = sf.read()
        template = re.sub(
            r'<script\s+src=[\'\"][^\'\"]*socket\.io[^\'\"]*[\'\"]>\s*</script>',
            lambda _: f'<script>\n{socket_code}\n</script>',
            template
        )

    injected_js = f"window.INITIAL_ALL_LEAGUES = {json.dumps(payload, ensure_ascii=False)};\n"
    if "// EMBEDDED_DATA_PLACEHOLDER" in template:
        modified_html = template.replace("// EMBEDDED_DATA_PLACEHOLDER", injected_js)
    else:
        modified_html = re.sub(r'window\.INITIAL_ALL_LEAGUES\s*=\s*\{.*?\};\n', lambda _: injected_js, template)

    # Desktop'a yaz (sadece macOS'ta)
    if os.path.isdir("/Users/onur/Desktop"):
        with open(DESKTOP_HTML, "w", encoding="utf-8") as f:
            f.write(modified_html)
        with open("/Users/onur/Desktop/premier_lig.html", "w", encoding="utf-8") as f:
            f.write(modified_html)

    # GitHub Pages çıktısı
    dist_dir = os.path.dirname(OUTPUT_HTML)
    os.makedirs(dist_dir, exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(modified_html)
    with open(TEMPLATE_HTML, "w", encoding="utf-8") as f:
        f.write(modified_html)

    # PWA dosyalarını dist/ klasörüne kopyala
    import shutil
    for fname in ["manifest.json", "sw.js"]:
        src = os.path.join(APP_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dist_dir, fname))
    icons_src = os.path.join(APP_DIR, "icons")
    icons_dst = os.path.join(dist_dir, "icons")
    if os.path.isdir(icons_src):
        shutil.copytree(icons_src, icons_dst, dirs_exist_ok=True)

    print(f"\nİşlem başarıyla tamamlandı! Standalone Neon Lig Tablosu:")
    print(f"1. {DESKTOP_HTML}")
    print(f"2. /Users/onur/Desktop/premier_lig.html")
    print(f"3. {OUTPUT_HTML} (GitHub Pages)")

    print(f"\nİşlem tamamlandı! {len(cached_data)}/{len(LEAGUES)} lig ve turnuva hazır:")
    print(f"- {DESKTOP_HTML}")

if __name__ == "__main__":
    build_desktop_html()
