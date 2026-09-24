import http.server
import socketserver
import json
import os
import sys
import threading
import time
import datetime
import urllib.request
import re
import html
import unicodedata
import socketio
import concurrent.futures
from pywebpush import webpush, WebPushException
PORT = int(os.environ.get("PORT", 8080))
SUBSCRIPTIONS_FILE = "subscriptions.json"
VAPID_FILE = "vapid_keys.json"
CACHE_FILE = "leagues_cache.json"
STREAM_PLAYER_CACHE = {}
MATCH_GOALS_CACHE = {}
MATCH_CARDS_CACHE = {}
GOALS_SCRAPE_IN_PROGRESS = {}
latest_matches_summary = []
is_initial_sync = True

# Semaphore: Sahadan scrape isteklerini eş zamanlı max 2 ile sınırla (429 koruması)
_GOALS_BG_SEM = threading.Semaphore(2)

def normalize_team_name(name):
    """Takım ismini karşılaştırma ve eşleştirme için normalize eder."""
    if not name:
        return ""
    t = str(name).strip().lower()
    t = t.replace("ı", "i").replace("İ", "i").replace("ş", "s").replace("Ş", "s")
    t = t.replace("ğ", "g").replace("Ğ", "g").replace("ü", "u").replace("Ü", "u")
    t = t.replace("ö", "o").replace("Ö", "o").replace("ç", "c").replace("Ç", "c")
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode('utf-8')
    t = re.sub(r'[^\w\s]', '', t)
    return re.sub(r'\s+', ' ', t).strip()

DISK_CACHE_LOCK = threading.Lock()

def save_goals_multi_keys(keys, goals, cards=None, is_ft=False):
    """Golcü ve kırmızı kart listesini verilen tüm key'ler (uuid, match_id, team pair) altına kaydeder."""
    if not goals and not cards:
        return
    if not keys:
        return
    now = time.time()
    clean_keys = set()
    for k in keys:
        if k:
            clean_keys.add(str(k).strip())
    
    has_missing = any(not g.get('scorer') for g in goals) if goals else False
    cache_time = (now - 3) if (has_missing and not is_ft) else now

    for k in clean_keys:
        existing_cards = MATCH_GOALS_CACHE.get(k, {}).get("cards", [])
        final_cards = cards if (cards is not None and len(cards) >= len(existing_cards)) else existing_cards
        if goals:
            if k in MATCH_GOALS_CACHE:
                old_g = MATCH_GOALS_CACHE[k].get("goals", [])
                # Önbellekte zaten daha fazla gol varsa daha az gollü veriyle ezme
                if old_g and len(old_g) > len(goals):
                    continue
                # Mevcut hafıza kaydı zaten tam ise ve yeni gelen eksikse ezme
                if has_missing and old_g and all(og.get("scorer") for og in old_g) and len(old_g) >= len(goals):
                    continue
            MATCH_GOALS_CACHE[k] = {
                "goals": goals,
                "cards": final_cards,
                "time": cache_time,
                "is_ft": is_ft
            }
        elif cards:
            if k in MATCH_GOALS_CACHE:
                MATCH_GOALS_CACHE[k]["cards"] = cards
            else:
                MATCH_GOALS_CACHE[k] = {
                    "goals": [],
                    "cards": cards,
                    "time": cache_time,
                    "is_ft": is_ft
                }

        if cards:
            rc_h = sum(1 for c in cards if str(c.get("team")).upper() == "A")
            rc_a = sum(1 for c in cards if str(c.get("team")).upper() == "B")
            MATCH_CARDS_CACHE[k] = {"data": {"rc_home": rc_h, "rc_away": rc_a, "cards": cards}, "time": now}
            if k in live_matches_state:
                tracked = live_matches_state[k]
                if rc_h > tracked.get("rc_home", 0):
                    tracked["rc_home"] = rc_h
                if rc_a > tracked.get("rc_away", 0):
                    tracked["rc_away"] = rc_a
            for sm in latest_matches_summary:
                if str(sm.get("id")) == k or str(sm.get("uuid")) == k:
                    if rc_h > (sm.get("rc_home") or 0):
                        sm["rc_home"] = rc_h
                        sm["rc_A"] = rc_h
                    if rc_a > (sm.get("rc_away") or 0):
                        sm["rc_away"] = rc_a
                        sm["rc_B"] = rc_a
    
    # Kalıcı disk önbelleğine sadece golcüler tamsa veya maç bittiyse yaz (atomic + lock)
    if goals and (not has_missing or is_ft):
        with DISK_CACHE_LOCK:
            try:
                cache_path = os.path.join(os.path.dirname(__file__), "all_goals_cache.json")
                temp_path = cache_path + f".tmp.{os.getpid()}"
                existing = {}
                if os.path.exists(cache_path):
                    try:
                        with open(cache_path, "r", encoding="utf-8") as f:
                            existing = json.load(f)
                    except Exception:
                        existing = {}
                for k in clean_keys:
                    if has_missing and k in existing and all(eg.get('scorer') for eg in existing[k]):
                        continue
                    existing[k] = goals
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False)
                os.replace(temp_path, cache_path)
            except Exception as e:
                print(f"save_goals_multi_keys disk hatası:", e)

    if cards:
        with DISK_CACHE_LOCK:
            try:
                c_cache_path = os.path.join(os.path.dirname(__file__), "all_cards_cache.json")
                c_temp_path = c_cache_path + f".tmp.{os.getpid()}"
                c_existing = {}
                if os.path.exists(c_cache_path):
                    try:
                        with open(c_cache_path, "r", encoding="utf-8") as cf:
                            c_existing = json.load(cf)
                    except Exception:
                        c_existing = {}
                for k in clean_keys:
                    c_existing[k] = cards
                with open(c_temp_path, "w", encoding="utf-8") as cf:
                    json.dump(c_existing, cf, ensure_ascii=False)
                os.replace(c_temp_path, c_cache_path)
            except Exception as ce:
                print(f"save_cards disk hatası:", ce)

def _save_goals_to_disk(uuid, goals):
    """Eski fonksiyonla geriye uyumluluk: tek key kaydet."""
    save_goals_multi_keys([uuid], goals)

def get_cached_match_cards(uuid="", home="", away=""):
    """Daha önce scrape edilmiş veya önbellekteki kırmızı kartları getirir."""
    keys = []
    if uuid:
        keys.append(str(uuid).strip())
    if home and away:
        keys.append(f"{normalize_team_name(home)}___{normalize_team_name(away)}")
    scrape_u = resolve_match_uuid(uuid, home, away)
    if scrape_u and scrape_u not in keys:
        keys.append(scrape_u)
    for k in keys:
        if k in MATCH_CARDS_CACHE:
            d = MATCH_CARDS_CACHE[k].get("data", {})
            if d and d.get("cards"):
                return d["cards"]
        if k in MATCH_GOALS_CACHE:
            c = MATCH_GOALS_CACHE[k].get("cards", [])
            if c:
                return c
    return []

# Preload persisted match goals cache if available
try:
    _cache_file = os.path.join(os.path.dirname(__file__), "all_goals_cache.json")
    if os.path.exists(_cache_file):
        with open(_cache_file, "r", encoding="utf-8") as _f:
            _loaded = json.load(_f)
            for _u, _g in _loaded.items():
                _has_missing = any(not g.get("scorer") for g in _g) if isinstance(_g, list) else True
                MATCH_GOALS_CACHE[_u] = {
                    "goals": _g,
                    "cards": [],
                    "time": 0 if _has_missing else time.time(),
                    "is_ft": False
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
                    rc_h = sum(1 for c in _c if c.get("team") == "A")
                    rc_a = sum(1 for c in _c if c.get("team") == "B")
                    MATCH_CARDS_CACHE[_u] = {"data": {"rc_home": rc_h, "rc_away": rc_a, "cards": _c}, "time": time.time()}
                    if _u in MATCH_GOALS_CACHE:
                        MATCH_GOALS_CACHE[_u]["cards"] = _c
                    else:
                        MATCH_GOALS_CACHE[_u] = {"goals": [], "cards": _c, "time": time.time(), "is_ft": False}
        print(f"Loaded {len(_loaded_cards)} matches into MATCH_CARDS_CACHE.")
except Exception as _e:
    print("Could not preload all_cards_cache.json:", _e)

# Uygulamamızdaki 26 lig/kupaya ait maç ID'leri (leagues_cache.json'dan)
# Sadece bu maçlar için golcü arka plan fetch'i yapılır (Bolivya vb. dışlanır)
KNOWN_MATCH_IDS = set()
KNOWN_COMPETITION_TITLES = set()  # Dinamik kupa maçları için competition title filtresi
KNOWN_TEAMS = set()  # 26 lig/kupadaki tüm takım isimleri (normalize). Jenerik lig adları
# ("Premier Lig", "Serie A", "Kupa") birçok ülkede geçtiği için comp-title tek başına
# yetmez; en az bir takımın bizden olması şart (Rusya/Brezilya/Mısır sızıntısını keser).
# Jenerik lig/kupa adları: birçok ülkede aynı isim geçer ("Premier Lig",
# "Serie A", "Süper Lig", "Kupa"...). Bunlarda tek takım yetmez, ikisi de
# bizden olmalı (Slavia Prag'ın Çekya Kupası maçı gibi sızıntılar için).
_GENERIC_COMP_TITLES = {
    "premier lig", "premier league", "premiyer lig",
    "serie a", "serie b", "seriya a",
    "süper lig", "super lig", "superlig", "superliga", "super league",
    "kupa", "cup", "cupa", "coppa", "pokal", "coupe",
    "pro lig", "pro league", "premiership",
    "1. lig", "2. lig", "first division", "second division",
}
MATCH_ID_TO_UUID = {}  # Numeric id -> Alphanumeric uuid eşleme sözlüğü
TEAM_PAIR_TO_UUID = {} # "norm(home)___norm(away)" -> Alphanumeric uuid eşleme sözlüğü
MATCH_TO_LEAGUE = {}   # uuid / id / "norm(home)___norm(away)" -> league_id eşleme sözlüğü

# Golcü takibi yalnızca bu 11 ana odak lig/kupada aktiftir (Kullanıcı talebi)
GOAL_TRACKED_LEAGUE_IDS = {
    # Türkiye
    "super-lig-tr",
    "trendyol-1-lig",
    "turkiye-kupasi",
    # İngiltere
    "premier-lig-en",
    "fa-cup",
    "lig-kupasi",
    # İspanya
    "laliga",
    "kral-kupasi",
    # Avrupa Kupaları
    "sampiyonlar-ligi",
    "avrupa-ligi",
    "konferans-ligi",
}

def is_goal_tracking_enabled(uuid="", home="", away="", comp_title=""):
    """
    Golcü takibi yalnızca belirlenen 11 lig/kupada (Türkiye, Premier Lig/Kupalar, İspanya ve Avrupa Kupaları) aktiftir.
    Diğer ligler (Championship, Almanya, İtalya, Fransa, Hollanda, Portekiz, Belçika, İskoçya, Danimarka, Norveç, Çekya, Avusturya, İsviçre)
    için sistemi yormamak adına golcü sorgulaması yapılmaz.
    """
    if comp_title:
        ct = str(comp_title).strip().lower()
        for tc in ("trendyol süper lig", "trendyol 1. lig", "ziraat türkiye kupası", "premier lig", "fa cup", "lig kupası", "laliga", "kral kupası", "şampiyonlar ligi", "avrupa ligi", "konferans ligi", "uluslar ligi", "nations league"):
            if tc in ct:
                return True
    u_str = str(uuid or "").strip()
    if u_str and u_str in MATCH_TO_LEAGUE:
        return MATCH_TO_LEAGUE[u_str] in GOAL_TRACKED_LEAGUE_IDS
    if home and away:
        pair = f"{normalize_team_name(home)}___{normalize_team_name(away)}"
        if pair in MATCH_TO_LEAGUE:
            return MATCH_TO_LEAGUE[pair] in GOAL_TRACKED_LEAGUE_IDS
    return False

try:
    _lc_file = os.path.join(os.path.dirname(__file__), "leagues_cache.json")
    if os.path.exists(_lc_file):
        with open(_lc_file, "r", encoding="utf-8") as _lf:
            _lc = json.load(_lf)
        for _lid, _league in _lc.items():
            _title = _league.get("competition_title", "")
            if _title:
                KNOWN_COMPETITION_TITLES.add(_title.strip().lower())
            for _week in _league.get("weeks", []):
                for _match in _week.get("matches", []):
                    _u = str(_match.get("uuid") or "").strip()
                    _i = str(_match.get("id") or "").strip()
                    if _u:
                        KNOWN_MATCH_IDS.add(_u)
                        MATCH_TO_LEAGUE[_u] = _lid
                    if _i:
                        KNOWN_MATCH_IDS.add(_i)
                        MATCH_TO_LEAGUE[_i] = _lid
                    if _i and _u:
                        MATCH_ID_TO_UUID[_i] = _u
                    _h = _match.get("home_team")
                    _a = _match.get("away_team")
                    _hn = (_h.get("name") or _h.get("display_name") or "") if isinstance(_h, dict) else str(_h or "")
                    _an = (_a.get("name") or _a.get("display_name") or "") if isinstance(_a, dict) else str(_a or "")
                    if _hn and _an:
                        _tp = f"{normalize_team_name(_hn)}___{normalize_team_name(_an)}"
                        MATCH_TO_LEAGUE[_tp] = _lid
                        if _u and not _u.isdigit():
                            TEAM_PAIR_TO_UUID[_tp] = _u
                    for _tk in ("home_team", "away_team"):
                        _tobj = _match.get(_tk)
                        _tname = _tobj.get("name") if isinstance(_tobj, dict) else _tobj
                        if _tname:
                            KNOWN_TEAMS.add(normalize_team_name(_tname))
        print(f"Loaded {len(KNOWN_MATCH_IDS)} known match IDs, {len(MATCH_ID_TO_UUID)} id->uuid pairs, {len(KNOWN_COMPETITION_TITLES)} competitions, {len(KNOWN_TEAMS)} teams, {len(TEAM_PAIR_TO_UUID)} team pairs, {len(MATCH_TO_LEAGUE)} league mappings from leagues_cache.json.")
except Exception as _e:
    print("Could not load leagues_cache.json for KNOWN_MATCH_IDS:", _e)

# Yalnızca Canlı Skorlar sekmesinde izlenecek bağımsız turnuvalar (Puan durumu / fikstür gerekmez)
STANDALONE_LIVE_COMPETITIONS = {
    "uefa uluslar ligi",
    "uefa nations league",
    "nations league"
}
for _sc in STANDALONE_LIVE_COMPETITIONS:
    KNOWN_COMPETITION_TITLES.add(_sc)

# Sunucu başlangıcında leagues_cache.json'dan dünün ve bugünün maçlarını latest_matches_summary'ye önceden doldur.
# Böylece Sahadan full sync API'si 429/502 verse bile maç listesi hiçbir zaman boş kalmaz ve anlık eventler bu listeye işlenir.
MATCH_DATETIME_MAP = {}
try:
    _lc_pre_file = os.path.join(os.path.dirname(__file__), "leagues_cache.json")
    if os.path.exists(_lc_pre_file):
        with open(_lc_pre_file, "r", encoding="utf-8") as _lpf:
            _lc_pre = json.load(_lpf)
        _today_str = datetime.date.today().strftime("%Y-%m-%d")
        _yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        _pre_map = {}
        for _lid, _league in _lc_pre.items():
            for _week in _league.get("weeks", []):
                for _m in _week.get("matches", []):
                    _dt = _m.get("date_time", "")
                    _mid = str(_m.get("id") or _m.get("match_id") or _m.get("uuid") or "")
                    _uuid = str(_m.get("uuid") or _m.get("match_uuid") or "")
                    if _mid and _dt: MATCH_DATETIME_MAP[_mid] = _dt
                    if _uuid and _dt: MATCH_DATETIME_MAP[_uuid] = _dt
                    if _today_str in _dt or _yesterday_str in _dt:
                        _mid = str(_m.get("id") or _m.get("match_id") or _m.get("uuid") or "")
                        _uuid = str(_m.get("uuid") or _m.get("match_uuid") or "")
                        _t_a = _m.get("home_team", {}).get("name", "") if isinstance(_m.get("home_team"), dict) else str(_m.get("home_team") or "")
                        _t_b = _m.get("away_team", {}).get("name", "") if isinstance(_m.get("away_team"), dict) else str(_m.get("away_team") or "")
                        _raw_st = str(_m.get("status") or "Fixture")
                        _m_dict = {
                            "id": _m.get("id") or _mid,
                            "match_id": _m.get("id") or _mid,
                            "uuid": _uuid,
                            "match_uuid": _uuid,
                            "status": _raw_st,
                            "period": _m.get("period") or "",
                            "minute": _m.get("minute"),
                            "fts_A": _m.get("home_score"),
                            "fts_B": _m.get("away_score"),
                            "hts_A": _m.get("half_time_home"),
                            "hts_B": _m.get("half_time_away"),
                            "rc_A": _m.get("rc_home", 0),
                            "rc_B": _m.get("rc_away", 0),
                            "rc_home": _m.get("rc_home", 0),
                            "rc_away": _m.get("rc_away", 0),
                            "home_team_name": _t_a,
                            "away_team_name": _t_b,
                            "date_time": _dt,
                            "match_time": _m.get("match_time", ""),
                            "extras": {}
                        }
                        _pre_map[_mid] = _m_dict
                        if _uuid:
                            _pre_map[_uuid] = _m_dict
        if _pre_map:
            latest_matches_summary = list({v["id"]: v for v in _pre_map.values()}.values())
            is_initial_sync = False
            print(f"Preloaded {len(latest_matches_summary)} matches into latest_matches_summary from leagues_cache.json.")
except Exception as _pre_err:
    print("Could not preload latest_matches_summary from leagues_cache.json:", _pre_err)


def resolve_match_uuid(raw_id, home="", away=""):
    """
    Verilen id veya uuid'nin alphanumeric sahadan/mackolik slug uuid'sini bulur.
    Numeric ID (örn: 5149721) verilirse bunu MATCH_ID_TO_UUID, live_matches_state
    veya latest_matches_summary üzerinden çözer (örn: drs3yh074bsdvvfek225z6xhw).
    """
    s_id = str(raw_id or "").strip()
    if s_id and not s_id.isdigit():
        return s_id
    if s_id in MATCH_ID_TO_UUID:
        return MATCH_ID_TO_UUID[s_id]
    if s_id in live_matches_state:
        obj = live_matches_state[s_id]
        cand = str(obj.get("uuid") or obj.get("match_uuid") or "").strip()
        if cand and not cand.isdigit():
            return cand
    for m in latest_matches_summary:
        if str(m.get("id")) == s_id or str(m.get("match_id")) == s_id:
            cand = str(m.get("uuid") or m.get("match_uuid") or "").strip()
            if cand and not cand.isdigit():
                return cand
    if home and away:
        hn = normalize_team_name(home)
        an = normalize_team_name(away)
        pair_k = f"{hn}___{an}"
        if pair_k in TEAM_PAIR_TO_UUID:
            cand = TEAM_PAIR_TO_UUID[pair_k]
            if cand and not cand.isdigit():
                return cand
        for cand_m in live_matches_state.values():
            if normalize_team_name(cand_m.get("home_team")) == hn and normalize_team_name(cand_m.get("away_team")) == an:
                cand = str(cand_m.get("uuid") or cand_m.get("match_uuid") or "").strip()
                if cand and not cand.isdigit():
                    return cand
        for m in latest_matches_summary:
            m_h = normalize_team_name(m.get("home_team_name") or m.get("team_A", {}).get("name") or "")
            m_a = normalize_team_name(m.get("away_team_name") or m.get("team_B", {}).get("name") or "")
            if m_h == hn and m_a == an:
                cand = str(m.get("uuid") or m.get("match_uuid") or "").strip()
                if cand and not cand.isdigit():
                    return cand
    return s_id

def to_sahadan_slug(text):
    if not text:
        return ""
    tr_map = {'ı':'i', 'I':'i', 'İ':'i', 'ş':'s', 'Ş':'s', 'ğ':'g', 'Ğ':'g', 'ü':'u', 'Ü':'u', 'ö':'o', 'Ö':'o', 'ç':'c', 'Ç':'c'}
    for k, v in tr_map.items():
        text = text.replace(k, v)
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    return re.sub(r'[-\s]+', '-', text)

def _parse_mackolik_key_events(data_dict):
    """Mackolik AJAX veya HTML widget ayarlarından (keyEvents) golleri ve kartları ayıklar."""
    if not isinstance(data_dict, dict):
        return [], [], False
    events = data_dict.get("keyEvents") or []
    st = str(data_dict.get("matchState") or "").lower()
    is_ft = st in ("played", "ft", "finished", "ms")
    goals = []
    cards = []
    for ev in events:
        t = str(ev.get("type") or "").lower()
        sub = str(ev.get("subType") or "").lower()
        pos = str(ev.get("position") or "").lower()
        team_side = "A" if pos == "home" else ("B" if pos == "away" else "")
        
        raw_min = ev.get("timeMin")
        extra_min = ev.get("timeMinExtra")
        minute_val = raw_min
        if raw_min and "+" in str(raw_min):
            try:
                parts = str(raw_min).split("+")
                minute_val = int(parts[0].strip())
                if not extra_min:
                    extra_min = int(parts[1].strip())
            except Exception:
                pass
        elif raw_min is not None:
            try:
                minute_val = int(str(raw_min).strip())
            except Exception:
                pass

        p_name = (ev.get("playerName") or "").strip()
        if p_name.lower() in ("bilinmiyor", "unknown", "none", "null", "-"):
            p_name = ""
            
        assist_name = (ev.get("assistPlayerName") or "").strip()
        if assist_name.lower() in ("bilinmiyor", "unknown", "none", "null", "-"):
            assist_name = ""

        sc_a, sc_b = None, None
        score_str = ev.get("score") or ""
        if score_str and "-" in score_str:
            parts = score_str.split("-")
            try:
                sc_a = int(parts[0].strip())
                sc_b = int(parts[1].strip())
            except Exception:
                pass

        if t == "goal" or "goal" in sub or "penalty" in sub or "own" in sub:
            g_type = "G"
            if "penalty" in sub or "penalty" in t:
                g_type = "PG"
            elif "own" in sub or "own" in t:
                g_type = "OG"
            goals.append({
                "type": g_type,
                "minute": minute_val,
                "extra_min": extra_min,
                "team": team_side,
                "scorer": p_name,
                "assist": assist_name,
                "score_A": sc_a,
                "score_B": sc_b
            })
        else:
            is_red = t in ("redcard", "rc", "y2c") or any(rc_kw in sub for rc_kw in ("redcard", "yellowredcard", "y2c", "rc", "red"))
            if is_red:
                c_type = "Y2C" if ("yellowred" in sub or "y2c" in sub) else "RC"
                cards.append({
                    "type": c_type,
                    "team": team_side,
                    "player": p_name,
                    "minute": minute_val,
                    "extra_min": extra_min
                })
    return goals, cards, is_ft

def parse_mackolik_events_from_html(html_text):
    """Mackolik maç detay HTML sayfasından keyEvents widget verisini parse eder."""
    import html as _html_mod
    m = re.search(r'data-module=[\"\']key-events[\"\'][^>]*data-settings=[\"\'](.*?)[\"\']', html_text)
    if not m:
        m = re.search(r'data-settings=[\"\'](.*?)[\"\'][^>]*data-module=[\"\']key-events[\"\']', html_text)
    if not m:
        return [], [], False
    try:
        settings = json.loads(_html_mod.unescape(m.group(1)))
        return _parse_mackolik_key_events(settings)
    except Exception:
        return [], [], False

def parse_sahadan_nuxt_events(html_text):
    """Sahadan Nuxt 3 data tag'inden key_events listesini parse eder."""
    m = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', html_text)
    if not m:
        return [], [], False
    try:
        data = json.loads(m.group(1))
    except Exception:
        return [], [], False

    memo = {}
    def deep_resolve(val, depth=0):
        if depth > 25: return val
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

    events = []
    for item in data:
        if isinstance(item, dict) and 'key_events' in item:
            ke_val = item['key_events']
            raw_list = data[ke_val] if isinstance(ke_val, int) and ke_val < len(data) else ke_val
            if isinstance(raw_list, list):
                for ev_ref in raw_list:
                    ev = deep_resolve(ev_ref)
                    if isinstance(ev, dict):
                        events.append(ev)
            break

    if not events:
        resolved = deep_resolve(2)
        def find_key_events(obj, depth=0):
            if depth > 12: return
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
            scorer_raw = scorer.get('name') or scorer.get('display_name') or ''
            if str(scorer_raw).strip().lower() in ('bilinmiyor', 'unknown', 'none', 'null'):
                scorer_raw = ''
            assist_raw = assist.get('name') or assist.get('display_name') or ''
            if str(assist_raw).strip().lower() in ('bilinmiyor', 'unknown', 'none', 'null'):
                assist_raw = ''
            goals.append({
                'type': t,
                'minute': ev.get('minute'),
                'extra_min': ev.get('minute_extra'),
                'team': ev.get('team'),
                'scorer': scorer_raw,
                'assist': assist_raw,
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

    is_ft = False
    for item in data:
        if isinstance(item, dict) and 'status' in item and ('period' in item or 'attendance' in item):
            st = str(deep_resolve(item['status']) or '').lower()
            if st in ('played', 'ms', 'ft', 'finished'):
                is_ft = True
                break

    return goals, cards, is_ft

def _merge_goals_lists(primary, secondary):
    """İki farklı kaynaktan (Mackolik ve Sahadan) gelen gol verilerini akıllıca birleştirir."""
    if not primary:
        return secondary or []
    if not secondary:
        return primary or []
    p_complete = all(g.get("scorer") for g in primary)
    s_complete = all(g.get("scorer") for g in secondary)
    if p_complete and len(primary) >= len(secondary):
        return primary
    if s_complete and len(secondary) >= len(primary):
        return secondary
    
    # Eksik golcüleri diğer kaynaktan tamamla
    base = [dict(g) for g in (primary if len(primary) >= len(secondary) else secondary)]
    donor = secondary if len(primary) >= len(secondary) else primary
    
    for g in base:
        if not g.get("scorer"):
            for d in donor:
                if d.get("scorer") and d.get("team") == g.get("team"):
                    try:
                        g_min = int(str(g.get("minute") or 0).split("+")[0].strip())
                        d_min = int(str(d.get("minute") or 0).split("+")[0].strip())
                        if abs(g_min - d_min) <= 1:
                            g["scorer"] = d["scorer"]
                            if not g.get("assist") and d.get("assist"):
                                g["assist"] = d["assist"]
                            break
                    except Exception:
                        pass
    return base

def parse_sahadan_api_detail(data):
    """Sahadan /api/index/match-detail JSON yanıtından gol, kart ve maç bitti durumunu çeker."""
    ke = data.get("key_events") or []
    goals = []
    cards = []
    for ev in ke:
        t = ev.get("type")
        if t in ("G", "PG", "OG"):
            scorer_obj = ev.get("scorer") or {}
            assist_obj = ev.get("assist") or {}
            scorer = scorer_obj.get("name") or scorer_obj.get("display_name") or ""
            if str(scorer).strip().lower() in ("bilinmiyor", "unknown", "none", "null"):
                scorer = ""
            assist = assist_obj.get("name") or assist_obj.get("display_name") or ""
            if str(assist).strip().lower() in ("bilinmiyor", "unknown", "none", "null"):
                assist = ""
            goals.append({
                "type": t,
                "minute": ev.get("minute"),
                "extra_min": ev.get("minute_extra"),
                "team": ev.get("team"),
                "scorer": scorer,
                "assist": assist,
                "score_A": ev.get("score_A"),
                "score_B": ev.get("score_B")
            })
        elif t in ("RC", "Y2C"):
            p_obj = ev.get("player") or {}
            p_name = p_obj.get("name") or p_obj.get("display_name") or ""
            cards.append({
                "type": t,
                "team": str(ev.get("team") or "").upper(),
                "player": p_name,
                "minute": ev.get("minute"),
                "extra_min": ev.get("minute_extra")
            })
    match_info = data.get("match") or {}
    st = str(match_info.get("status") or "").lower()
    pr = str(match_info.get("period") or "").lower()
    is_ft = st in ("played", "ms", "ft", "finished", "bitti") or pr in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")
    return goals, cards, is_ft

def parse_lineup_from_api(lineup_data, home, away):
    """Sahadan match-detail JSON içindeki lineup nesnesinden kadro ve diziliş verisi çeker."""
    if not lineup_data or not isinstance(lineup_data, dict):
        return None
    team_a_data = lineup_data.get("team_A") or {}
    team_b_data = lineup_data.get("team_B") or {}

    def _parse_team(t_dict):
        raw_players = t_dict.get("players") or []
        starters = []
        for p in raw_players:
            px = p.get("x")
            py = p.get("y")
            if px is not None and py is not None:
                p_info = p.get("player") or {}
                name = p_info.get("formation_name") or p_info.get("name") or p_info.get("match_name") or ""
                starters.append({"name": name, "x": px, "y": py})
        f_raw = str(t_dict.get("formation") or "").strip()
        f_str = "-".join(list(f_raw)) if (len(f_raw) in (3, 4) and f_raw.isdigit()) else f_raw
        return {"formation": f_str, "players": starters}

    pa = _parse_team(team_a_data)
    pb = _parse_team(team_b_data)
    if not pa["players"] and not pb["players"]:
        return None
    return {
        "success": True,
        "has_lineup": True,
        "team_A": {"name": home, "formation": pa["formation"], "players": pa["players"]},
        "team_B": {"name": away, "formation": pb["formation"], "players": pb["players"]}
    }

# ==============================================================================
# SAHADAN / MACKOLİK GOLCÜ ALTYAPISI (SAHADAN API + OPTA AJAX + HTML)
# ==============================================================================

def fetch_match_goals(home, away, uuid, min_goals=0, force_refresh=False):
    if not uuid and not (home and away):
        return []
    if not is_goal_tracking_enabled(uuid, home, away):
        return []
    now = time.time()

    # live_matches_state içinde bu maça ait diğer ID'ler veya takım isimleri var mı bak
    match_obj = None
    if uuid and uuid in live_matches_state:
        match_obj = live_matches_state[uuid]
    elif home and away:
        h_n = normalize_team_name(home)
        a_n = normalize_team_name(away)
        for cand_m in live_matches_state.values():
            if normalize_team_name(cand_m.get("home_team")) == h_n and normalize_team_name(cand_m.get("away_team")) == a_n:
                match_obj = cand_m
                break

    if (not home or not away) and uuid:
        u_str = str(uuid).strip()
        if u_str in match_names_map:
            home, away = match_names_map[u_str]
        elif match_obj:
            home = match_obj.get("home_team")
            away = match_obj.get("away_team")

    # Tüm olası alias anahtarlarını topla (uuid, match_id ve takim-cifti hem bosluklu hem bosluksuz)
    cand_keys = []
    if uuid:
        cand_keys.append(str(uuid).strip())
    if home and away:
        h_norm = normalize_team_name(home)
        a_norm = normalize_team_name(away)
        if h_norm and a_norm:
            cand_keys.append(f"{h_norm}___{a_norm}")
            cand_keys.append(f"{re.sub(r'[^a-z0-9]', '', h_norm)}___{re.sub(r'[^a-z0-9]', '', a_norm)}")

    # live_matches_state içinde bu maça ait diğer ID'ler var mı bak
    match_obj = None
    if uuid and uuid in live_matches_state:
        match_obj = live_matches_state[uuid]
    elif home and away:
        h_n = normalize_team_name(home)
        a_n = normalize_team_name(away)
        for cand_m in live_matches_state.values():
            if normalize_team_name(cand_m.get("home_team")) == h_n and normalize_team_name(cand_m.get("away_team")) == a_n:
                match_obj = cand_m
                break

    if match_obj:
        for k in ("uuid", "match_uuid", "id", "match_id"):
            val = match_obj.get(k)
            if val and str(val).strip() not in cand_keys:
                cand_keys.append(str(val).strip())

    # force_refresh=True ise cache'i temizle (gol iptali sonrası bayat veri döndürmesin)
    if force_refresh:
        for ck in list(cand_keys):
            if ck in MATCH_GOALS_CACHE:
                del MATCH_GOALS_CACHE[ck]
                log_event(f"🗑️ force_refresh: cache temizlendi [{ck}]")

    # 1. Önbellek kontrolü (HERHANGİ bir alias altında varsa)
    if not force_refresh:
        for ck in cand_keys:
            if ck in MATCH_GOALS_CACHE:
                cached = MATCH_GOALS_CACHE[ck]
                c_goals = cached.get("goals", [])
                has_missing_scorer = any(not g.get('scorer') for g in c_goals)
                
                # Eğer dolu goller varsa:
                if len(c_goals) > 0:
                    # Eğer daha fazla gol bekleniyorsa (min_goals > len(c_goals)), cache eksiktir, taze çekilmelidir!
                    if min_goals > len(c_goals):
                        if now - cached.get("time", 0) < 1.5:
                            return c_goals
                    else:
                        # Maç bittiyse (is_ft) hemen dön
                        if cached.get("is_ft"):
                            return c_goals
                        # İstenen asgari gol sayısı karşılanmışsa ve eksik golcü yoksa (60 sn geçerli)
                        if not has_missing_scorer:
                            if now - cached.get("time", 0) < 60:
                                return c_goals
                        # Eksik golcü / yeni gol beklentisi varsa 1.5 saniyede bir taze çek (flood koruması)
                        if now - cached.get("time", 0) < 1.5:
                            return c_goals
                else:
                    # Henüz hiç gol yoksa: Sadece min_goals istenmemişse ve son 1.5 saniyede sorgulanmışsa cache dön
                    if min_goals <= 0 and (now - cached.get("time", 0) < 1.5):
                        return c_goals

    slug = f"{to_sahadan_slug(home)}-vs-{to_sahadan_slug(away)}"
    # Scrape için kullanılacak sahadan/mackolik alphanumeric uuid'si
    scrape_uuid = resolve_match_uuid(uuid, home, away)
    if not scrape_uuid or str(scrape_uuid).strip().isdigit():
        log_event(f"⏭️ Golcü scrape atlandı (UUID çözülemedi): home={home} away={away} uuid={uuid} -> scrape={scrape_uuid}")
        return []

    # Alphanumeric UUID'yi de alias listesine ekle
    if scrape_uuid not in cand_keys:
        cand_keys.append(scrape_uuid)

    # --- In-progress guard: aynı maç için eş zamanlı scraping'i engelle ---
    scrape_key = str(scrape_uuid).strip()
    if not force_refresh and scrape_key in GOALS_SCRAPE_IN_PROGRESS:
        ev = GOALS_SCRAPE_IN_PROGRESS[scrape_key]
        log_event(f"⏳ Scraping zaten devam ediyor, bekleniyor: {scrape_key}")
        ev.wait(timeout=13)
        # Scraping bitti, cache'den oku
        for ck in cand_keys:
            if ck in MATCH_GOALS_CACHE:
                cached = MATCH_GOALS_CACHE[ck]
                if cached.get("goals"):
                    log_event(f"✅ In-progress bekle sonrası cache hit: {scrape_key}")
                    return cached["goals"]
        return []

    # Scraping başlatılıyor, event oluştur ve kaydet
    _scrape_event = threading.Event()
    GOALS_SCRAPE_IN_PROGRESS[scrape_key] = _scrape_event

    ts_bust = int(now * 1000)

    ajax_url = f"https://www.mackolik.com/ajax/football/key-events?ajaxViewName=events&matchId={scrape_uuid}"
    ajax_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"https://www.mackolik.com/mac/{slug}/{scrape_uuid}",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    html_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin"
    }
    sh_api_url = f"https://www.sahadan.com/api/index/match-detail?a=bs&e=sam&match_uuid={scrape_uuid}&application=mackolik.com&language=tr&country=tr"
    sh_url = f"https://www.sahadan.com/mac/{slug}/{scrape_uuid}"
    mk_url = f"https://www.mackolik.com/mac/{slug}/{scrape_uuid}"

    def _fetch_sahadan_api():
        try:
            req = urllib.request.Request(sh_api_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": f"https://www.sahadan.com/mac/{slug}/{scrape_uuid}",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache"
            })
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                d = raw.get("data") if isinstance(raw, dict) else {}
                if d:
                    goals, cards, ft = parse_sahadan_api_detail(d)
                    if d.get("lineup") and scrape_uuid:
                        try:
                            parsed_lu = parse_lineup_from_api(d.get("lineup"), home, away)
                            if parsed_lu and parsed_lu.get("has_lineup"):
                                for k in cand_keys:
                                    MATCH_LINEUPS_CACHE[k] = {"data": parsed_lu, "time": now}
                        except Exception:
                            pass
                    return goals, cards, ft
        except Exception:
            pass
        return [], [], False

    def _fetch_ajax():
        try:
            req = urllib.request.Request(ajax_url, headers=ajax_headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                data = raw.get("data") if isinstance(raw, dict) else {}
                if data:
                    return _parse_mackolik_key_events(data)
        except Exception:
            pass
        return [], [], False

    def _fetch_html_page(target_url, ref_domain):
        headers = dict(html_headers)
        headers["Referer"] = f"https://www.{ref_domain}.com/canli-sonuclar"
        for attempt in range(2):
            try:
                req = urllib.request.Request(target_url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = resp.read().decode("utf-8")
                    if data and len(data) > 500:
                        return data
            except urllib.error.HTTPError as he:
                if he.code in (429, 502, 503) and attempt == 0:
                    time.sleep(0.4)
                    continue
                break
            except Exception:
                if attempt == 0:
                    time.sleep(0.3)
                    continue
                break
        return None

    def _fetch_sahadan():
        sh_html = _fetch_html_page(sh_url, "sahadan")
        if not sh_html:
            return [], [], False
        if "__NUXT_DATA__" in sh_html:
            goals, cards, ft = parse_sahadan_nuxt_events(sh_html)
            if goals:
                return goals, cards, ft
        if "widget-key-events" in sh_html or "data-module" in sh_html:
            return parse_mackolik_events_from_html(sh_html)
        return [], [], False

    def _fetch_mackolik_html():
        mk_html = _fetch_html_page(mk_url, "mackolik")
        if not mk_html:
            return [], [], False
        goals, cards, ft = parse_mackolik_events_from_html(mk_html)
        if not goals and "__NUXT_DATA__" in mk_html:
            return parse_sahadan_nuxt_events(mk_html)
        return goals, cards, ft

    # --- 4 kaynağı PARALEL başlat (Öncelik Mackolik AJAX [0.06s] + Sahadan JSON API) ---
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_ajax = executor.submit(_fetch_ajax)
            fut_api  = executor.submit(_fetch_sahadan_api)
            fut_sh   = executor.submit(_fetch_sahadan)
            fut_mk   = executor.submit(_fetch_mackolik_html)

            results = {}
            pending = {fut_ajax: "ajax", fut_api: "sahadan_api", fut_sh: "sahadan", fut_mk: "mackolik"}
            # İlk "tam" sonucu bekle; tam değilse diğerlerini de topla
            complete_goals, complete_cards, complete_ft, winner = None, None, None, None
            for fut in concurrent.futures.as_completed(pending, timeout=12):
                src = pending[fut]
                try:
                    g, c, ft = fut.result()
                except Exception:
                    g, c, ft = [], [], False
                results[src] = (g, c, ft)
                is_complete = (min_goals > 0 and len(g) >= min_goals and not any(not x.get("scorer") for x in g)) or (min_goals <= 0 and len(g) > 0 and not any(not x.get("scorer") for x in g))
                if is_complete and complete_goals is None:
                    complete_goals, complete_cards, complete_ft, winner = g, c, ft, src
                    break

        api_goals, api_cards, api_ft = results.get("sahadan_api", ([], [], False))
        a_goals, a_cards, a_ft = results.get("ajax", ([], [], False))
        sh_goals, sh_cards, sh_ft = results.get("sahadan", ([], [], False))
        mk_goals, mk_cards, mk_ft = results.get("mackolik", ([], [], False))

        if complete_goals is not None:
            # Tam sonuç bulundu, hemen kaydet ve dön
            cards = complete_cards or []
            save_goals_multi_keys(cand_keys, complete_goals, cards=cards, is_ft=complete_ft)
            log_event(f"⚡ fetch_match_goals (Paralel/{winner}) {len(complete_goals)} gol buldu, {len(cards)} kırmızı kart: {slug} ({scrape_uuid})")
            return complete_goals

        # Tam sonuç yok, tüm kaynakları birleştir (öncelik: Ajax > Sahadan API > Sahadan HTML > Mackolik HTML)
        goals = _merge_goals_lists(a_goals, api_goals)
        if sh_goals:
            goals = _merge_goals_lists(goals, sh_goals)
        if mk_goals:
            goals = _merge_goals_lists(goals, mk_goals)

        cards = a_cards or api_cards or sh_cards or mk_cards
        is_ft = a_ft or api_ft or sh_ft or mk_ft
        success_domain = "Ajax" if a_goals else ("SahadanAPI" if api_goals else ("Sahadan" if sh_goals else ("Mackolik" if mk_goals else "None")))

        has_missing_scorer = any(not g.get("scorer") for g in goals) if goals else True

        if goals or cards:
            if not has_missing_scorer:
                save_goals_multi_keys(cand_keys, goals, cards=cards, is_ft=is_ft)
            else:
                save_goals_multi_keys(cand_keys, goals, cards=cards, is_ft=is_ft)
                for k in cand_keys:
                    if k in MATCH_GOALS_CACHE:
                        MATCH_GOALS_CACHE[k]["time"] = now - 3

        log_event(f"✅ fetch_match_goals (Paralel/{success_domain}) {len(goals)} gol buldu, {len(cards)} kırmızı kart (scorer_missing={has_missing_scorer}): {slug} ({scrape_uuid})")
        return goals
    except Exception as e:
        log_event(f"❌ Error fetching match goals for {slug} ({uuid}): {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        _scrape_event.set()
        GOALS_SCRAPE_IN_PROGRESS.pop(scrape_key, None)


def fetch_match_red_cards(home, away, uuid):
    """
    Sahadan ve Mackolik maç detay sayfasından RC (Direkt Kırmızı) ve Y2C (2. Sarıdan Kırmızı) olaylarını çeker.
    """
    if not uuid:
        return {"rc_home": 0, "rc_away": 0, "cards": []}
    now = time.time()
    
    scrape_uuid = resolve_match_uuid(uuid, home, away)
    cache_keys = [str(uuid).strip()]
    if scrape_uuid and scrape_uuid != uuid:
        cache_keys.append(scrape_uuid)

    for ck in cache_keys:
        if ck in MATCH_CARDS_CACHE:
            cached = MATCH_CARDS_CACHE[ck]
            if now - cached.get("time", 0) < 15:
                return cached["data"]

    # 1. Doğrudan Sahadan JSON API (hızlı, 0.2s ve en güncel veriler)
    if scrape_uuid and not str(scrape_uuid).isdigit():
        try:
            api_url = f"https://www.sahadan.com/api/index/match-detail?a=bs&e=sam&match_uuid={scrape_uuid}&application=mackolik.com&language=tr&country=tr"
            req_api = urllib.request.Request(api_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Cache-Control": "no-cache"
            })
            with urllib.request.urlopen(req_api, timeout=4) as api_resp:
                api_raw = json.loads(api_resp.read().decode("utf-8"))
                api_data = api_raw.get("data") if isinstance(api_raw, dict) else {}
                if api_data:
                    goals, cards, is_ft = parse_sahadan_api_detail(api_data)
                    m_info = api_data.get("match") or {}
                    ext = m_info.get("extras") or {}
                    rc_h = int(ext.get("team_A_redcards") or 0)
                    rc_a = int(ext.get("team_B_redcards") or 0)
                    if cards:
                        rc_h = max(rc_h, sum(1 for c in cards if str(c.get("team")).upper() == "A"))
                        rc_a = max(rc_a, sum(1 for c in cards if str(c.get("team")).upper() == "B"))
                    res_data = {
                        "rc_home": rc_h,
                        "rc_away": rc_a,
                        "cards": cards,
                        "fts_A": m_info.get("fts_A"),
                        "fts_B": m_info.get("fts_B"),
                        "minute": m_info.get("minute"),
                        "status": "Played" if is_ft else (m_info.get("status") or ""),
                        "period": m_info.get("period") or "",
                        "is_ft": is_ft
                    }
                    for ck in cache_keys:
                        MATCH_CARDS_CACHE[ck] = {"data": res_data, "time": now}
                    if goals or cards:
                        save_goals_multi_keys(cache_keys, goals, cards=cards, is_ft=is_ft)
                    return res_data
        except Exception:
            pass

    slug = f"{to_sahadan_slug(home)}-vs-{to_sahadan_slug(away)}"
    ts_bust = int(now * 1000)
    candidate_urls = [
        f"https://www.sahadan.com/mac/{slug}/{scrape_uuid}",
        f"https://www.mackolik.com/mac/{slug}/{scrape_uuid}"
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "tr-TR,tr;q=0.9",
        "Cache-Control": "no-cache"
    }

    html = None
    for target_url in candidate_urls:
        try:
            req = urllib.request.Request(target_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8")
                if html and len(html) > 500:
                    break
        except Exception:
            continue

    if not html:
        return {"rc_home": 0, "rc_away": 0, "cards": []}

    try:
        goals, cards, _ = parse_mackolik_events_from_html(html)
        if not cards and ("__NUXT_DATA__" in html):
            _, cards, _ = parse_sahadan_nuxt_events(html)

        rc_home = sum(1 for c in cards if c.get("team") == "A")
        rc_away = sum(1 for c in cards if c.get("team") == "B")
        res_data = {"rc_home": rc_home, "rc_away": rc_away, "cards": cards}
        for ck in cache_keys:
            MATCH_CARDS_CACHE[ck] = {"data": res_data, "time": now}
        return res_data
    except Exception as e:
        log_event(f"Kırmızı kart çekme hatası ({slug}): {e}")
        return {"rc_home": 0, "rc_away": 0, "cards": []}

MATCH_LINEUPS_CACHE = {}

def format_formation_str(f_raw):
    if not f_raw:
        return ""
    f_str = str(f_raw).strip()
    if len(f_str) in (3, 4) and f_str.isdigit():
        return "-".join(list(f_str))
    return f_str

def fetch_match_lineup(home, away, uuid, force_refresh=False):
    """Sahadan API ve HTML scraping ile kadro ve diziliş verisi çeker."""
    if not uuid and not (home and away):
        return {"success": False, "has_lineup": False, "message": "Maç ID eksik."}

    now = time.time()
    match_obj = None
    if uuid and uuid in live_matches_state:
        match_obj = live_matches_state[uuid]
    elif home and away:
        h_n = normalize_team_name(home)
        a_n = normalize_team_name(away)
        for cand_m in live_matches_state.values():
            if normalize_team_name(cand_m.get("home_team")) == h_n and normalize_team_name(cand_m.get("away_team")) == a_n:
                match_obj = cand_m
                break

    if (not home or not away or "Ã" in str(home) or "Ã" in str(away)) and uuid:
        u_str = str(uuid).strip()
        if u_str in match_names_map:
            home, away = match_names_map[u_str]
        elif match_obj:
            home = match_obj.get("home_team")
            away = match_obj.get("away_team")

    scrape_uuid = resolve_match_uuid(uuid, home, away)
    if not scrape_uuid:
        return {"success": False, "has_lineup": False, "message": "Maç ID eksik."}

    cand_keys = []
    if uuid:
        cand_keys.append(str(uuid).strip())
    if scrape_uuid and str(scrape_uuid).strip() not in cand_keys:
        cand_keys.append(str(scrape_uuid).strip())
    if home and away:
        h_norm = normalize_team_name(home)
        a_norm = normalize_team_name(away)
        if h_norm and a_norm:
            cand_keys.append(f"{h_norm}___{a_norm}")
            cand_keys.append(f"{re.sub(r'[^a-z0-9]', '', h_norm)}___{re.sub(r'[^a-z0-9]', '', a_norm)}")

    # force_refresh=True ise önbelleği temizle
    if force_refresh:
        for cand_k in cand_keys:
            MATCH_LINEUPS_CACHE.pop(cand_k, None)

    # 1. Önbellek kontrolü (HERHANGİ bir alias altında varsa)
    if not force_refresh:
        for cand_k in cand_keys:
            if cand_k in MATCH_LINEUPS_CACHE:
                cached = MATCH_LINEUPS_CACHE[cand_k]
                if cached.get("data", {}).get("has_lineup"):
                    if now - cached.get("time", 0) < 864000:
                        return cached["data"]
                elif now - cached.get("time", 0) < 5:
                    return cached["data"]

    slug = f"{to_sahadan_slug(home)}-vs-{to_sahadan_slug(away)}"

    # 2. Hızlı ve doğrudan Sahadan JSON API'si ile kadroyu çekmeyi dene (0.4s - 4s, 0s cache)
    try:
        api_lu_url = f"https://www.sahadan.com/api/index/match-detail?a=bs&e=sam&match_uuid={scrape_uuid}&application=mackolik.com&language=tr&country=tr"
        req_lu = urllib.request.Request(api_lu_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.sahadan.com/mac/{slug}/{scrape_uuid}",
            "Cache-Control": "no-cache"
        })
        with urllib.request.urlopen(req_lu, timeout=8) as lu_resp:
            lu_raw = json.loads(lu_resp.read().decode("utf-8"))
            lu_d = lu_raw.get("data") if isinstance(lu_raw, dict) else {}
            if lu_d and lu_d.get("lineup"):
                parsed_api_lu = parse_lineup_from_api(lu_d.get("lineup"), home, away)
                if parsed_api_lu and parsed_api_lu.get("has_lineup"):
                    for k in cand_keys:
                        MATCH_LINEUPS_CACHE[k] = {"data": parsed_api_lu, "time": now}
                    log_event(f"🟢 fetch_match_lineup (Sahadan API) {home} vs {away} kadroları yüklendi.")
                    return parsed_api_lu
    except Exception as _api_lu_err:
        log_event(f"⚠️ fetch_match_lineup Sahadan API uyarısı ({home} vs {away}): {_api_lu_err}")

    url = f"https://www.sahadan.com/mac/{slug}/{scrape_uuid}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.sahadan.com/",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin"
    }

    html = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=headers)
            html = urllib.request.urlopen(req, timeout=9).read().decode("utf-8")
            break
        except urllib.error.HTTPError as he:
            if he.code == 429 and attempt == 0:
                time.sleep(1.2)
                continue
            log_event(f"Kadro çekme HTTP hatası ({slug}): {he.code} {he.reason}")
            if he.code == 429:
                res_err = {"success": False, "has_lineup": False, "message": "Sahadan sunucuları anlık yoğun. Lütfen birkaç saniye sonra tekrar deneyin."}
                for k in cand_keys:
                    MATCH_LINEUPS_CACHE[k] = {"data": res_err, "time": now}
                return res_err
            res_err = {"success": False, "has_lineup": False, "message": f"Kadro bilgisi alınamadı (HTTP {he.code})."}
            for k in cand_keys:
                MATCH_LINEUPS_CACHE[k] = {"data": res_err, "time": now}
            return res_err
        except Exception as e:
            if attempt == 0:
                time.sleep(0.5)
                continue
            log_event(f"Kadro çekme hatası ({slug}): {e}")
            res_err = {"success": False, "has_lineup": False, "message": "Kadro yüklenirken bağlantı hatası oluştu."}
            for k in cand_keys:
                MATCH_LINEUPS_CACHE[k] = {"data": res_err, "time": now}
            return res_err

    if not html:
        res_err = {"success": False, "has_lineup": False, "message": "Kadro bilgisi alınamadı."}
        for k in cand_keys:
            MATCH_LINEUPS_CACHE[k] = {"data": res_err, "time": now}
        return res_err

    try:
        m = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', html)
        if not m:
            res = {"success": True, "has_lineup": False, "message": "Bu maç için kadro bilgisi henüz mevcut değil."}
            for k in cand_keys:
                MATCH_LINEUPS_CACHE[k] = {"data": res, "time": now}
            return res

        data = json.loads(m.group(1))
        memo = {}
        def deep_resolve(val, depth=0):
            if depth > 25: return val
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

        lineup_data = None
        for k, v in resolved.items():
            if isinstance(v, dict) and "data" in v and "lineup" in v["data"]:
                lineup_data = v["data"]["lineup"]
                break

        if not lineup_data or not isinstance(lineup_data, dict):
            # Teşhis: NUXT yapısını logla - hangi key'ler var, data alt-key'leri ne
            try:
                top_keys = list(resolved.keys())[:20]
                data_subkeys = {}
                for _k, _v in resolved.items():
                    if isinstance(_v, dict) and "data" in _v and isinstance(_v["data"], dict):
                        data_subkeys[str(_k)] = list(_v["data"].keys())[:15]
                # 'lineup' kelimesini içeren herhangi bir key ara
                lineup_mentions = []
                def _search_lineup(obj, path="", depth=0):
                    if depth > 4: return
                    if isinstance(obj, dict):
                        for _k2, _v2 in obj.items():
                            if "lineup" in str(_k2).lower():
                                lineup_mentions.append(f"{path}.{_k2}")
                            _search_lineup(_v2, f"{path}.{_k2}", depth+1)
                    elif isinstance(obj, list) and depth < 3:
                        for i, _v2 in enumerate(obj[:5]):
                            _search_lineup(_v2, f"{path}[{i}]", depth+1)
                _search_lineup(resolved)
                log_event(f"🔍 Kadro NUXT debug ({home} vs {away}): top_keys={top_keys} | data_subkeys={data_subkeys} | lineup_mentions={lineup_mentions[:10]}")
            except Exception as _de:
                log_event(f"🔍 Kadro NUXT debug hata: {_de}")
            res = {"success": True, "has_lineup": False, "message": "Kadro henüz açıklanmadı."}
            for k in cand_keys:
                MATCH_LINEUPS_CACHE[k] = {"data": res, "time": now}
            return res

        team_a_data = lineup_data.get("team_A") or {}
        team_b_data = lineup_data.get("team_B") or {}

        def parse_team_lineup(t_dict):
            raw_players = t_dict.get("players") or []
            starters = []
            for p in raw_players:
                px = p.get("x")
                py = p.get("y")
                if px is not None and py is not None:
                    p_info = p.get("player") or {}
                    name = p_info.get("formation_name") or p_info.get("name") or p_info.get("match_name") or ""
                    starters.append({
                        "name": name,
                        "x": px,
                        "y": py
                    })
            return {
                "formation": format_formation_str(t_dict.get("formation")),
                "players": starters
            }

        parsed_a = parse_team_lineup(team_a_data)
        parsed_b = parse_team_lineup(team_b_data)

        if not parsed_a["players"] and not parsed_b["players"]:
            res = {"success": True, "has_lineup": False, "message": "Kadro henüz açıklanmadı."}
            for k in cand_keys:
                MATCH_LINEUPS_CACHE[k] = {"data": res, "time": now}
            return res

        res = {
            "success": True,
            "has_lineup": True,
            "team_A": {
                "name": home,
                "formation": parsed_a["formation"],
                "players": parsed_a["players"]
            },
            "team_B": {
                "name": away,
                "formation": parsed_b["formation"],
                "players": parsed_b["players"]
            }
        }
        for k in cand_keys:
            MATCH_LINEUPS_CACHE[k] = {"data": res, "time": now}
        log_event(f"🟢 fetch_match_lineup (Sahadan) {home} vs {away} kadroları yüklendi.")
        return res
    except Exception as e:
        log_event(f"Kadro parse hatası ({slug}): {e}")
        res_err = {"success": False, "has_lineup": False, "message": f"Kadro yüklenirken hata: {e}"}
        for k in cand_keys:
            MATCH_LINEUPS_CACHE[k] = {"data": res_err, "time": now}
        return res_err

last_push_logs = []

def log_event(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    print(entry, flush=True)
    last_push_logs.append(entry)
    if len(last_push_logs) > 50:
        last_push_logs.pop(0)

# Load VAPID keys
if not os.path.exists(VAPID_FILE):
    log_event("HATA: vapid_keys.json bulunamadı!")
    sys.exit(1)

with open(VAPID_FILE, "r") as f:
    vapid_keys = json.load(f)

# Load team names mapping from cache
match_names_map = {}
def load_match_names():
    global match_names_map
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            count = 0
            for lid, ldata in d.items():
                if isinstance(ldata, dict):
                    for w in ldata.get("weeks", []):
                        for m in w.get("matches", []):
                            mid = str(m.get("id", ""))
                            muuid = str(m.get("uuid", "")).strip()
                            h = m.get("home_team", {}).get("name", "")
                            a = m.get("away_team", {}).get("name", "")
                            if h and a:
                                if mid:
                                    match_names_map[mid] = (h, a)
                                if muuid:
                                    match_names_map[muuid] = (h, a)
                                count += 1
            log_event(f"{count} maçın takım isimleri önbellekten yüklendi.")
        except Exception as e:
            log_event(f"Önbellek okuma hatası: {e}")

load_match_names()

# Load subscriptions
def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        try:
            with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_subscriptions(subs):
    with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=2, ensure_ascii=False)

def send_push_to_sub(sub, payload):
    endpoint = sub.get("endpoint", "")
    try:
        claims = {"sub": "mailto:oktemonur7@gmail.com"}
        headers = {"Urgency": "high"}
        resp = webpush(
            subscription_info=sub,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=vapid_keys["private_key"],
            vapid_claims=claims,
            ttl=86400,
            headers=headers
        )
        status_code = getattr(resp, "status_code", 200)
        log_event(f"Push İletildi ({status_code}): {endpoint[:50]}...")
        return True, f"HTTP {status_code}"
    except WebPushException as ex:
        status = getattr(ex.response, "status_code", None) if ex.response else None
        body = getattr(ex.response, "text", str(ex)) if ex.response else str(ex)
        err_msg = f"WebPushException ({status}): {body}"
        log_event(f"Push Hatası: {err_msg}")
        if status in (404, 410):
            return "expired", err_msg
        return False, err_msg
    except Exception as e:
        err_msg = f"Beklenmeyen Hata: {type(e).__name__}: {e}"
        log_event(f"Push Hatası: {err_msg}")
        return False, err_msg

# Check if a match matches subscriber's favorites
def is_match_favorited(sub, match_identifiers):
    favs = sub.get("favorites", [])
    if not favs:
        return False
    fav_set = set(str(f).strip().lower() for f in favs)
    for ident in match_identifiers:
        if ident and str(ident).strip().lower() in fav_set:
            return True
    return False

# Send push ONLY to subscribers who favorited this match
def send_push_for_match(match_identifiers, payload):
    subs = load_subscriptions()
    if not subs:
        return 0

    target_subs = [s for s in subs if is_match_favorited(s, match_identifiers)]
    if not target_subs:
        return 0

    log_event(f"Maç bildirimi ({len(target_subs)} abone): {payload.get('title')} - {payload.get('body')}")
    
    # Push bildirimini arka planda non-blocking olarak hemen gönder, socket/sync döngüsü beklemesin
    def _dispatch_worker(targets, pl):
        expired_endpoints = set()
        for sub in targets:
            ok, _ = send_push_to_sub(sub, pl)
            if ok == "expired":
                expired_endpoints.add(sub.get("endpoint"))
        if expired_endpoints:
            all_current = load_subscriptions()
            active_subs = [s for s in all_current if s.get("endpoint") not in expired_endpoints]
            save_subscriptions(active_subs)

    threading.Thread(target=_dispatch_worker, args=(target_subs, payload), daemon=True).start()
    return len(target_subs)

# Send push to ALL subscribers (Test button / system)
def send_push_to_all(payload):
    subs = load_subscriptions()
    if not subs:
        log_event("Push gönderilemedi: Kayıtlı abone yok.")
        return 0, "Kayıtlı abone cihaz bulunamadı"

    log_event(f"Test bildirimi {len(subs)} aboneye iletiliyor...")
    expired_endpoints = set()
    sent_count = 0
    last_err = ""

    for sub in subs:
        ok, msg = send_push_to_sub(sub, payload)
        if ok is True:
            sent_count += 1
        else:
            last_err = msg
            if ok == "expired":
                expired_endpoints.add(sub.get("endpoint"))

    if expired_endpoints:
        active_subs = [s for s in subs if s.get("endpoint") not in expired_endpoints]
        save_subscriptions(active_subs)

    return sent_count, last_err

# Match state tracking
live_matches_state = {}

def process_match_update(update, is_initial=False, is_from_full_sync=False):
    if not update or not isinstance(update, dict):
        return

    mid = str(update.get("id") or update.get("match_id") or update.get("uuid") or "")
    if not mid:
        return

    match_ids = [
        mid,
        str(update.get("id", "")),
        str(update.get("match_id", "")),
        str(update.get("uuid", "")),
        str(update.get("match_uuid", ""))
    ]
    match_ids = [i for i in match_ids if i]

    # Canlı Maç Takip Nesnesi Çözümleme (Numeric ID, Match ID, UUID tek nesnede birleştirilir)
    m = None
    for cand_id in match_ids:
        if cand_id in live_matches_state:
            m = live_matches_state[cand_id]
            break

    cached_names = match_names_map.get(mid, ("Ev Sahibi", "Deplasman"))

    if m is None:
        m = {
            "id": update.get("id") or update.get("match_id") or "",
            "uuid": update.get("uuid") or update.get("match_uuid") or "",
            "home_team": update.get("home_team_name") or cached_names[0],
            "away_team": update.get("away_team_name") or cached_names[1],
            "home_score": None,
            "away_score": None,
            "ht_home": None,
            "ht_away": None,
            "status": "",
            "period": "",
            "minute": "",
            "rc_home": 0,
            "rc_away": 0,
            "notified_scores": set(),
            "notified_ht": False,
            "notified_ft": False,
            "cancelled_scores_cooldown": {},
            "notified_cancel_scores": set()
        }

    # update içindeki id veya uuid varsa m üzerine güncelle
    if update.get("id") and not m.get("id"):
        m["id"] = str(update["id"])
    if update.get("uuid") and not m.get("uuid"):
        m["uuid"] = str(update["uuid"])
    if update.get("match_uuid") and not m.get("uuid"):
        m["uuid"] = str(update["match_uuid"])
    
    # Tüm ID varyasyonlarını aynı referansa bağla (böylece socket.io uuid ve full-sync id aynı maçı günceller)
    for cand_id in match_ids:
        live_matches_state[cand_id] = m

    if "notified_scores" not in m:
        m["notified_scores"] = set()
    if "notified_cancel_scores" not in m:
        m["notified_cancel_scores"] = set()

    if "home_team" not in m or not m["home_team"]:
        m["home_team"] = update.get("home_team_name") or cached_names[0]
    if "away_team" not in m or not m["away_team"]:
        m["away_team"] = update.get("away_team_name") or cached_names[1]

    if m.get("home_team") == "Ev Sahibi" and cached_names[0] != "Ev Sahibi":
        m["home_team"] = cached_names[0]
        m["away_team"] = cached_names[1]

    if "home_team_name" in update and update["home_team_name"]:
        m["home_team"] = update["home_team_name"]
    if "away_team_name" in update and update["away_team_name"]:
        m["away_team"] = update["away_team_name"]
    if "minute" in update and update["minute"] is not None:
        new_min_val = str(update["minute"])
        try:
            cur_min = int(m.get("minute") or 0)
            in_min = int(new_min_val)
            if in_min < cur_min and cur_min > 0 and is_from_full_sync:
                pass  # Bayat tam senkronizasyonun dakikayı geriye çekmesini engelle
            else:
                m["minute"] = new_min_val
        except (ValueError, TypeError):
            m["minute"] = new_min_val

    all_identifiers = match_ids + [m["home_team"], m["away_team"]]

    new_home = update.get("fts_A")
    new_away = update.get("fts_B")

    new_status = str(update.get("status") or "").strip()
    new_period = str(update.get("period") or "").strip()
    is_ht = new_period in ("Half Time", "Devre Arası", "HT") or new_status in ("Half Time", "Devre Arası", "HT")
    is_ft = new_status.lower() in ("played", "ms", "ft", "finished", "bitti") or new_period.lower() in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")

    if is_ft:
        m["status"] = "Played"
        m["period"] = new_period or "Full Time"
    elif new_status:
        m["status"] = new_status
    if new_period:
        m["period"] = new_period

    if is_initial:
        if new_home is not None:
            try: m["home_score"] = int(new_home)
            except ValueError: pass
        if new_away is not None:
            try: m["away_score"] = int(new_away)
            except ValueError: pass
        if m["home_score"] is not None and m["away_score"] is not None:
            m["notified_scores"].add((m["home_score"], m["away_score"]))
        if update.get("hts_A") is not None: m["ht_home"] = update["hts_A"]
        if update.get("hts_B") is not None: m["ht_away"] = update["hts_B"]
        ext_init = update.get("extras") or {}
        for kA in ("rc_A", "rc_home", "red_cards_A", "rcA", "redCardsA", "team_A_redcards"):
            val_init = update.get(kA) if update.get(kA) is not None else ext_init.get(kA)
            if val_init is not None:
                try:
                    m["rc_home"] = int(val_init)
                    break
                except (ValueError, TypeError): pass
        for kB in ("rc_B", "rc_away", "red_cards_B", "rcB", "redCardsB", "team_B_redcards"):
            val_init = update.get(kB) if update.get(kB) is not None else ext_init.get(kB)
            if val_init is not None:
                try:
                    m["rc_away"] = int(val_init)
                    break
                except (ValueError, TypeError): pass
        if is_ht or is_ft:
            m["notified_ht"] = True
        if is_ft:
            m["notified_ft"] = True
        return

    # 1. GOL KONTROLÜ
    goal_team = ""
    goal_scored = False

    new_h = None
    new_a = None
    if new_home is not None:
        try: new_h = int(new_home)
        except ValueError: pass
    if new_away is not None:
        try: new_a = int(new_away)
        except ValueError: pass

    # Skor düşüş kontrolü (VAR / Gol İptali vs Jitter Koruması)
    is_home_cancel = False
    is_away_cancel = False
    now_ts = time.time()
    last_goal_time = m.get("last_goal_time", 0)

    # İptal Edilen Skorlar Karantinası (300 saniyelik Cooldown / Tombstone)
    if "cancelled_scores_cooldown" not in m:
        m["cancelled_scores_cooldown"] = {}
    
    # 300 saniyesi dolmuş eski karantina kayıtlarını temizle
    m["cancelled_scores_cooldown"] = {
        sc: exp_time for sc, exp_time in m["cancelled_scores_cooldown"].items()
        if now_ts < exp_time
    }

    # İptal edilmiş bir skor geliyorsa (bayat paket): kabul etme!
    incoming_pair = (
        new_h if new_h is not None else m.get("home_score"),
        new_a if new_a is not None else m.get("away_score")
    )
    if incoming_pair in m["cancelled_scores_cooldown"]:
        # Bu skor son 5 dakika içinde iptal edilmiştir, bayat paketle skoru tekrar hortlatma!
        new_h = m.get("home_score")
        new_a = m.get("away_score")

    # Jitter / Bayat Paket Koruması:
    if is_from_full_sync:
        # Full sync (soccer-live-results) CDN önbelleğinden geldiği için canlı maçta asla skor düşüremez ve iptal tetikleyemez!
        if new_h is not None and m.get("home_score") is not None and new_h < m["home_score"]:
            new_h = m["home_score"]
        if new_a is not None and m.get("away_score") is not None and new_a < m["away_score"]:
            new_a = m["away_score"]
    else:
        if new_h is not None and m.get("home_score") is not None and new_h < m["home_score"]:
            is_home_cancel = True
        if new_a is not None and m.get("away_score") is not None and new_a < m["away_score"]:
            is_away_cancel = True

    # 1. GERÇEK GOL İPTALİ TESPİTİ (VAR veya Yan Hakem Ofsaytı)
    if is_home_cancel or is_away_cancel:
        old_score_pair = (m["home_score"], m["away_score"])
        cancel_pair = (new_h, new_a)
        
        # 300 saniye (5 dakika) boyunca iptal edilen bu skora geri dönülse dahi (bayat paket) tekrar GOL bildirimi gitmesini ve skorun hortlamasını engelle
        m["cancelled_scores_cooldown"][old_score_pair] = now_ts + 300
        m["home_score"] = new_h
        m["away_score"] = new_a
        m["last_goal_time"] = 0  # Gol iptal edildi, taze gol zamanını sıfırla

        # İptal edilen skoru notified_scores'dan sil (ileride gerçekten atılırsa bildirim gitsin)
        if "notified_scores" in m:
            m["notified_scores"].discard(old_score_pair)

        # DEDUPLICATION: Aynı maçta aynı iptal skoru için 5 dakika boyunca tekrar tekrar iptal push'u gönderme
        cancel_dedup_key = f"{old_score_pair}->{cancel_pair}"
        if "notified_cancel_scores" not in m:
            m["notified_cancel_scores"] = set()

        if cancel_dedup_key not in m["notified_cancel_scores"]:
            m["notified_cancel_scores"].add(cancel_dedup_key)
            team_str = f" {m['home_team']}" if is_home_cancel else f" {m['away_team']}"
            cancel_title = f"❌ GOL İPTAL!{team_str}"
            cancel_body = f"{m['home_team']} {new_h} - {new_a} {m['away_team']}"
            log_event(f"GOL İPTAL EDİLDİ: {cancel_title} -> {cancel_body}")
            
            send_push_for_match(all_identifiers, {
                "title": cancel_title,
                "body": cancel_body,
                "icon": "icons/icon-192.png",
                "tag": f"goal-cancel-{mid}-{new_h}-{new_a}"
            })
        else:
            log_event(f"GOL İPTAL TEKRARI ENGELLENDİ (Deduplicated): {mid} {cancel_dedup_key}")

        # Gol iptal edildiğinde önbellekteki golleri anında temizle
        for ck in all_identifiers:
            if ck in MATCH_GOALS_CACHE:
                del MATCH_GOALS_CACHE[ck]

    if new_h is not None:
        if m["home_score"] is not None and new_h > m["home_score"]:
            goal_scored = True
            goal_team = m["home_team"]
            m["last_goal_time"] = now_ts
        m["home_score"] = new_h

    if new_a is not None:
        if m["away_score"] is not None and new_a > m["away_score"]:
            goal_scored = True
            goal_team = m["away_team"]
            m["last_goal_time"] = now_ts
        m["away_score"] = new_a

    # DEDUPLICATION & COOLDOWN: 
    # 1. Aynı skor için daha önce bildirim gitmişse TEKRAR BİLDİRİM GİTMEZ.
    # 2. Skor son 300 saniye içinde VAR ile İPTAL EDİLMİŞSE bayat paket dalgalanması engellenir.
    score_pair = (m["home_score"], m["away_score"])
    is_in_cancel_cooldown = score_pair in m.get("cancelled_scores_cooldown", {})
    if goal_scored and not is_in_cancel_cooldown and score_pair not in m["notified_scores"]:
        m["notified_scores"].add(score_pair)
        min_str = f"{m['minute']}'" if m["minute"] else "Canlı"
        team_str = f" {goal_team}" if goal_team else ""
        title = f"⚽ GOL!{team_str} ({min_str})"
        body = f"{m['home_team']} {m['home_score']} - {m['away_score']} {m['away_team']}"
        log_event(f"GOL TESPİT EDİLDİ: {title} -> {body}")
        send_push_for_match(all_identifiers, {
            "title": title,
            "body": body,
            "icon": "icons/icon-192.png",
            "tag": f"goal-{mid}-{m['home_score']}-{m['away_score']}"
        })

        # Arka planda golcü bilgisini çek ve cache'e kaydet
        # (Uygulama kapalı kullanıcılar açtığında golcü hazır gelir)
        _expected = (m["home_score"] or 0) + (m["away_score"] or 0)
        _h, _a = m["home_team"], m["away_team"]
        _u = resolve_match_uuid(m.get("uuid") or mid, _h, _a)
        _match_keys = list(set(match_ids + [
            _u,
            str(mid),
            f"{normalize_team_name(_h)}___{normalize_team_name(_a)}"
        ]))

        def _bg_fetch_goals(h, a, u, expected, keys, match_ref):
            # İlk deneme: 1.0s bekle (akışın ilk paketini yakalamak için)
            time.sleep(1.0)
            max_attempts = 36  # ~3 dakika boyunca 5 saniyede bir sorgula
            for attempt in range(max_attempts):
                try:
                    goals = fetch_match_goals(h, a, u, min_goals=expected)
                    has_all = len(goals) >= expected and all(g.get("scorer") for g in goals)
                    if has_all:
                        save_goals_multi_keys(keys, goals, is_ft=False)
                        log_event(f"✅ Golcü cache'e yazıldı ({h} vs {a}, {len(goals)} gol, deneme {attempt+1})")
                        # İkinci aşama push: golcü belli olunca favorilere isimle bildir
                        try:
                            last = goals[-1] if goals else {}
                            scorer = (last.get("scorer") or "").strip()
                            if scorer:
                                minute = last.get("minute") or ""
                                min_str = f" {minute}'" if minute else ""
                                hs = match_ref.get("home_score", 0) or 0
                                as_ = match_ref.get("away_score", 0) or 0
                                send_push_for_match(list(set(keys + [h, a])), {
                                    "title": f"⚽ Gol: {scorer} ({h} vs {a})",
                                    "body": f"{h} {hs} - {as_} {a} — {scorer}{min_str}",
                                    "icon": "icons/icon-192.png",
                                    "tag": f"scorer-{mid}-{hs}-{as_}"
                                })
                        except Exception as _push_e:
                            log_event(f"Golcü 2. push hatası ({h} vs {a}): {_push_e}")
                        return  # Başarıyla tamamlandı
                    if goals:
                        # Kısmi veri var, sadece in-memory güncelle ama aramaya devam et
                        log_event(f"⏳ Golcü kısmen geldi ({h} vs {a}, {len(goals)}/{expected} gol, deneme {attempt+1}) — 5sn sonra yeniden deniyor")
                        for k in keys:
                            if k:
                                ck = str(k).strip()
                                old_g = MATCH_GOALS_CACHE.get(ck, {}).get("goals", [])
                                if old_g and len(old_g) > len(goals):
                                    continue
                                MATCH_GOALS_CACHE[ck] = {"goals": goals, "time": time.time() - 3, "is_ft": False}
                    else:
                        log_event(f"⏳ Golcü henüz yok ({h} vs {a}, deneme {attempt+1}/{max_attempts})")
                except Exception as e:
                    log_event(f"_bg_fetch_goals hata ({h} vs {a}, deneme {attempt+1}): {e}")
                if attempt < max_attempts - 1:
                    time.sleep(5)
            log_event(f"⚠️ Golcü {max_attempts} denemede çekilemedi: {h} vs {a}")

        # Sadece izin verilen 12 ligdeki maçlar için golcü çek (Kullanıcı talebi doğrultusunda diğer ligler filtrelenir)
        _is_known = (not KNOWN_MATCH_IDS) or (_u in KNOWN_MATCH_IDS) or (mid in KNOWN_MATCH_IDS) or any(k in KNOWN_MATCH_IDS for k in match_ids)
        if _is_known and is_goal_tracking_enabled(uuid=_u, home=_h, away=_a):
            threading.Thread(target=_bg_fetch_goals, args=(_h, _a, _u, _expected, _match_keys, m), daemon=True).start()
        else:
            log_event(f"⏭️ Golcü fetch atlandı (hariç tutulan/bilinmeyen lig): {_h} vs {_a} (id={mid}, uuid={_u})")


    # 2. İLK YARI BİTTİ KONTROLÜ
    if update.get("hts_A") is not None:
        m["ht_home"] = update["hts_A"]
    if update.get("hts_B") is not None:
        m["ht_away"] = update["hts_B"]

    if is_ht and not m["notified_ht"]:
        m["notified_ht"] = True
        ht_h = m["ht_home"] if m["ht_home"] is not None else (m["home_score"] if m["home_score"] is not None else 0)
        ht_a = m["ht_away"] if m["ht_away"] is not None else (m["away_score"] if m["away_score"] is not None else 0)
        title = "⏸️ İlk Yarı Bitti"
        body = f"{m['home_team']} {ht_h} - {ht_a} {m['away_team']}"
        log_event(f"İLK YARI BİTTİ: {title} -> {body}")
        send_push_for_match(all_identifiers, {
            "title": title,
            "body": body,
            "icon": "icons/icon-192.png",
            "tag": f"ht-{mid}"
        })

    # 3. MAÇ BİTTİ KONTROLÜ
    if is_ft and not m["notified_ft"]:
        m["notified_ft"] = True
        m["status"] = "Played"
        h = m["home_score"] if m["home_score"] is not None else 0
        a = m["away_score"] if m["away_score"] is not None else 0
        title = "🏁 Maç Bitti"
        body = f"{m['home_team']} {h} - {a} {m['away_team']}"
        log_event(f"MAÇ BİTTİ: {title} -> {body}")
        send_push_for_match(all_identifiers, {
            "title": title,
            "body": body,
            "icon": "icons/icon-192.png",
            "tag": f"ft-{mid}"
        })

        # Maç bittiğinde golcüleri nihai olarak çekip kalıcı diske kaydet (Yalnızca izin verilen ligler)
        _ft_expected = h + a
        _ft_h, _ft_a = m["home_team"], m["away_team"]
        _ft_u = resolve_match_uuid(m.get("uuid") or mid, _ft_h, _ft_a)
        if _ft_expected > 0 and is_goal_tracking_enabled(uuid=_ft_u, home=_ft_h, away=_ft_a):
            _ft_keys = list(set(match_ids + [_ft_u, str(mid), f"{normalize_team_name(_ft_h)}___{normalize_team_name(_ft_a)}"]))
            def _bg_ft_goals(h_name, a_name, u_id, exp_g, keys):
                time.sleep(3)
                for attempt in range(20):
                    try:
                        g_res = fetch_match_goals(h_name, a_name, u_id, min_goals=exp_g)
                        if len(g_res) >= exp_g and all(g.get("scorer") for g in g_res):
                            save_goals_multi_keys(keys, g_res, is_ft=True)
                            log_event(f"🏁 Bitmiş maç golcüleri kalıcı cache'e yazıldı ({h_name} vs {a_name})")
                            return
                        if g_res:
                            save_goals_multi_keys(keys, g_res, is_ft=False)
                    except Exception:
                        pass
                    if attempt < 19:
                        time.sleep(5)
            threading.Thread(target=_bg_ft_goals, args=(_ft_h, _ft_a, _ft_u, _ft_expected, _ft_keys), daemon=True).start()

    # 4. KIRMIZI KART KONTROLÜ
    ext_rc = update.get("extras") or {}
    for kA in ("rc_A", "rc_home", "red_cards_A", "rcA", "redCardsA", "team_A_redcards"):
        val = update.get(kA) if update.get(kA) is not None else ext_rc.get(kA)
        if val is not None:
            try:
                new_rc_h = int(val)
                if new_rc_h > m["rc_home"]:
                    m["rc_home"] = new_rc_h
                    h_team = str(m.get('home_team') or '').strip()
                    a_team = str(m.get('away_team') or '').strip()
                    if h_team and a_team and h_team.lower() not in ('none', 'null', 'ev sahibi') and a_team.lower() not in ('none', 'null', 'deplasman'):
                        min_str = f"{m['minute']}'" if m["minute"] else "Canlı"
                        title = f"🟥 Kırmızı Kart! {h_team} ({min_str})"
                        body = f"{h_team} {m.get('home_score',0)} - {m.get('away_score',0)} {a_team}"
                        log_event(f"KIRMIZI KART: {title}")
                        send_push_for_match(all_identifiers, {
                            "title": title,
                            "body": body,
                            "icon": "icons/icon-192.png",
                            "tag": f"rc-{mid}-{time.time()}"
                        })
                break
            except (ValueError, TypeError):
                pass

    for kB in ("rc_B", "rc_away", "red_cards_B", "rcB", "redCardsB", "team_B_redcards"):
        val = update.get(kB) if update.get(kB) is not None else ext_rc.get(kB)
        if val is not None:
            try:
                new_rc_a = int(val)
                if new_rc_a > m["rc_away"]:
                    m["rc_away"] = new_rc_a
                    h_team = str(m.get('home_team') or '').strip()
                    a_team = str(m.get('away_team') or '').strip()
                    if h_team and a_team and h_team.lower() not in ('none', 'null', 'ev sahibi') and a_team.lower() not in ('none', 'null', 'deplasman'):
                        min_str = f"{m['minute']}'" if m["minute"] else "Canlı"
                        title = f"🟥 Kırmızı Kart! {a_team} ({min_str})"
                        body = f"{h_team} {m.get('home_score',0)} - {m.get('away_score',0)} {a_team}"
                        log_event(f"KIRMIZI KART: {title}")
                        send_push_for_match(all_identifiers, {
                            "title": title,
                            "body": body,
                            "icon": "icons/icon-192.png",
                            "tag": f"rc-{mid}-{time.time()}"
                        })
                break
            except (ValueError, TypeError):
                pass

# SAHADAN REAL-TIME HTTP SYNC ENGINE
last_7am_reset_date = ""

def check_and_reset_subscribers_at_7am():
    global last_7am_reset_date
    tz_tr = datetime.timezone(datetime.timedelta(hours=3))
    now = datetime.datetime.now(tz_tr)
    cycle_dt = now if now.hour >= 7 else (now - datetime.timedelta(days=1))
    current_cycle = cycle_dt.strftime("%Y-%m-%d")

    if not last_7am_reset_date:
        last_7am_reset_date = current_cycle
        return

    if last_7am_reset_date != current_cycle:
        last_7am_reset_date = current_cycle
        try:
            # 10 günden (864000 sn) eski kadro verilerini temizle, yenileri koru
            cutoff = time.time() - 864000
            expired_keys = [k for k, v in MATCH_LINEUPS_CACHE.items() if v.get("time", 0) < cutoff]
            for k in expired_keys:
                MATCH_LINEUPS_CACHE.pop(k, None)
            if expired_keys:
                log_event(f"🌅 Sabah 07:00: 10 günden eski {len(expired_keys)} maç kadrosu önbellekten silindi.")

            subs = load_subscriptions()
            cleared = 0
            for s in subs:
                if s.get("favorites"):
                    s["favorites"] = []
                    cleared += 1
            if cleared > 0:
                save_subscriptions(subs)
                log_event(f"🌅 Sabah 07:00 sıfırlaması: {cleared} abonenin favorileri temizlendi.")
        except Exception as e:
            log_event(f"Sabah 07:00 sıfırlama hatası: {e}")

def get_match_period_rank(period_str, status_str=""):
    p = str(period_str or "").lower().strip()
    s = str(status_str or "").lower().strip()
    if s in ("played", "ms", "ft", "finished", "bitti") or p in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti"):
        return 6
    if "penalt" in p:
        return 5
    if "extra" in p or "uzatma" in p or p == "et":
        return 4
    if "second" in p or "2" in p:
        return 3
    if p in ("half time", "devre arası", "ht", "iy"):
        return 2
    if "first" in p or "1" in p:
        return 1
    if s in ("playing", "live", "inprogress", "in progress", "devam", "oynuyor"):
        return 1
    return 0

def sahadan_http_sync_worker():
    global is_initial_sync, latest_matches_summary
    log_event("🔄 Sahadan Canlı HTTP Senkronizasyon Servisi Başlatıldı.")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://www.sahadan.com/canli-sonuclar",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    tz_tr = datetime.timezone(datetime.timedelta(hours=3))
    last_full_fetch = 0
    last_lineup_preload = 0

    while True:
        now = time.time()
        check_and_reset_subscribers_at_7am()

        # 1. Her 30 saniyede bir tüm maçların durumunu çek (soccer-live-results / soccer-sync-data)
        if now - last_full_fetch >= 30:
            try:
                now_dt = datetime.datetime.now(tz_tr)
                dates_to_sync = [
                    (now_dt - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
                    now_dt.strftime("%Y-%m-%d")
                ]
                new_summary_map = {}
                for sync_date in dates_to_sync:
                    candidate_urls = [
                        f"https://www.sahadan.com/api/index/soccer-live-results?a=bs&e=sams&add_playing=1&extended_period=1&date={sync_date}&application=mackolik.com&language=tr",
                        f"https://www.sahadan.com/api/index/soccer-sync-data?a=bs&e=sams&add_playing=1&date={sync_date}&application=mackolik.com&language=tr"
                    ]
                    raw = None
                    for live_url in candidate_urls:
                        try:
                            req = urllib.request.Request(live_url, headers=headers)
                            with urllib.request.urlopen(req, timeout=10) as res:
                                raw = json.loads(res.read().decode("utf-8"))
                                if raw and raw.get("data", {}).get("areas"):
                                    break
                        except Exception as url_err:
                            continue

                    if not raw:
                        log_event(f"Sahadan sync error for {sync_date}: Both endpoints failed")
                        continue

                    try:
                        areas = raw.get("data", {}).get("areas", [])
                        for a in areas:
                            for c in a.get("competitions", []):
                                # Dinamik kupa filtresi: competition title bizim liglerimizden biriyle eşleşiyorsa
                                # yeni tur maçlarını (FA Cup vs.) anında KNOWN_MATCH_IDS'e ekle
                                _comp_title = str(c.get("title") or c.get("name") or "").strip().lower()
                                _comp_is_ours = (_comp_title in KNOWN_COMPETITION_TITLES)
                                for m in c.get("matches", []):
                                    mid = m.get("id")
                                    uuid = m.get("uuid")
                                    is_match_known = (str(mid) in KNOWN_MATCH_IDS) or (str(uuid) in KNOWN_MATCH_IDS)

                                    # Yabancı lig ve maçları ele:
                                    # Önceden tanımlı olmayan (is_match_known False) maçlarda:
                                    # SADECE Uluslar Ligi (STANDALONE_LIVE_COMPETITIONS) veya bizim kupalarımız (FA Cup, TR Kupası vb.) kabul edilir.
                                    # Normal lig maçları (Premier Lig, Süper Lig, Serie A vb.) için tüm maçlar zaten leagues_cache'de ve KNOWN_MATCH_IDS'dedir!
                                    if not is_match_known:
                                        is_standalone = (_comp_title in STANDALONE_LIVE_COMPETITIONS)
                                        is_cup = any(k in _comp_title for k in ("fa cup", "lig kupası", "kral kupası", "türkiye kupası", "ziraat türkiye kupası"))
                                        if not (is_standalone or (_comp_is_ours and is_cup)):
                                            continue  # Bilinmeyen lig maçlarını (Ukrayna, Kosova, Slovakya vb.) kesinlikle engelle

                                        # Kupa maçlarında en az bir takımın bizim liglerimizden olması şart (Tayland/BAE lig kupalarını keser)
                                        if is_cup and KNOWN_TEAMS:
                                            _ta0 = normalize_team_name((m.get("team_A") or {}).get("name", ""))
                                            _tb0 = normalize_team_name((m.get("team_B") or {}).get("name", ""))
                                            if not (_ta0 in KNOWN_TEAMS or _tb0 in KNOWN_TEAMS):
                                                continue

                                    if "fa cup" in _comp_title or _comp_title == "fa cup":
                                        m_dt_raw = m.get("date_time_utc") or m.get("date_time") or ""
                                        if not m_dt_raw or str(m_dt_raw)[:10] < "2026-11-15":
                                            continue
                                    if _comp_is_ours and (mid or uuid):
                                        if uuid: KNOWN_MATCH_IDS.add(str(uuid))
                                        if mid:  KNOWN_MATCH_IDS.add(str(mid))
                                    if mid and uuid:
                                        MATCH_ID_TO_UUID[str(mid)] = str(uuid)
                                    t_a = m.get("team_A", {}).get("name", "")
                                    t_b = m.get("team_B", {}).get("name", "")
                                    if mid and t_a and t_b:
                                        match_names_map[str(mid)] = (t_a, t_b)
                                    if uuid and t_a and t_b:
                                        match_names_map[str(uuid)] = (t_a, t_b)
                                        _hn = normalize_team_name(t_a)
                                        _an = normalize_team_name(t_b)
                                        if _hn and _an:
                                            TEAM_PAIR_TO_UUID[f"{_hn}___{_an}"] = str(uuid)

                                    raw_st = str(m.get("status") or "").strip()
                                    raw_pr = str(m.get("period") or "").strip()
                                    is_m_ft = raw_st.lower() in ("played", "ms", "ft", "finished", "bitti") or raw_pr.lower() in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")

                                    ext = m.get("extras") or {}
                                    rc_h = ext.get("team_A_redcards") or m.get("rc_A") or m.get("rc_home") or 0
                                    rc_a = ext.get("team_B_redcards") or m.get("rc_B") or m.get("rc_away") or 0
                                    try: rc_h = int(rc_h)
                                    except: rc_h = 0
                                    try: rc_a = int(rc_a)
                                    except: rc_a = 0

                                    _c_display_title = str(c.get("title") or c.get("name") or "UEFA Uluslar Ligi").strip()
                                    match_dict = {
                                        "id": mid,
                                        "match_id": mid,
                                        "uuid": uuid,
                                        "match_uuid": uuid,
                                        "date_time": m.get("date_time_utc") or m.get("date_time") or "",
                                        "match_time": m.get("match_time") or "",
                                        "status": "Played" if is_m_ft else raw_st,
                                        "period": raw_pr,
                                        "minute": m.get("minute"),
                                        "fts_A": m.get("fts_A"),
                                        "fts_B": m.get("fts_B"),
                                        "hts_A": m.get("hts_A"),
                                        "hts_B": m.get("hts_B"),
                                        "rc_A": rc_h,
                                        "rc_B": rc_a,
                                        "rc_home": rc_h,
                                        "rc_away": rc_a,
                                        "home_team_name": t_a,
                                        "away_team_name": t_b,
                                        "home_team": t_a,
                                        "away_team": t_b,
                                        "competition_name": _c_display_title,
                                        "league_name": _c_display_title,
                                        "extras": ext
                                    }

                                    # Canlı takip edilen maç varsa ve full sync eski/düşük skor/dakika döndüyse koru
                                    tracked = live_matches_state.get(str(mid)) or (live_matches_state.get(str(uuid)) if uuid else None)
                                    if tracked:
                                        old_h = tracked.get("home_score")
                                        old_a = tracked.get("away_score")
                                        old_min = tracked.get("minute")
                                        # Full sync (soccer-live-results) CDN önbelleğidir; canlı maçta skoru ASLA geriye çekemez
                                        if old_h is not None and (match_dict.get("fts_A") is None or int(match_dict.get("fts_A", 0)) < old_h):
                                            match_dict["fts_A"] = old_h
                                        if old_a is not None and (match_dict.get("fts_B") is None or int(match_dict.get("fts_B", 0)) < old_a):
                                            match_dict["fts_B"] = old_a
                                        if old_min is not None and match_dict.get("minute") is not None:
                                            try:
                                                if int(match_dict["minute"]) < int(old_min):
                                                    match_dict["minute"] = old_min
                                            except (ValueError, TypeError):
                                                pass
                                        if tracked.get("rc_home"):
                                            match_dict["rc_A"] = max(match_dict.get("rc_A", 0), tracked["rc_home"])
                                            match_dict["rc_home"] = match_dict["rc_A"]
                                        if tracked.get("rc_away"):
                                            match_dict["rc_B"] = max(match_dict.get("rc_B", 0), tracked["rc_away"])
                                            match_dict["rc_away"] = match_dict["rc_B"]

                                    new_summary_map[str(mid)] = match_dict
                                    process_match_update(match_dict, is_initial=is_initial_sync, is_from_full_sync=True)
                    except Exception as sync_err:
                        log_event(f"Sahadan sync error for {sync_date}: {sync_err}")

                # API 429/502 verdiyse doğrudan Sahadan HTML Canlı Sonuçlar sayfasından (__NUXT_DATA__) tüm canlı maçları çek
                if not new_summary_map:
                    try:
                        html_req = urllib.request.Request("https://www.sahadan.com/canli-sonuclar", headers=headers)
                        with urllib.request.urlopen(html_req, timeout=10) as h_res:
                            h_text = h_res.read().decode("utf-8")
                            nm = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', h_text)
                            if nm:
                                n_data = json.loads(nm.group(1))
                                n_memo = {}
                                def n_resolve(val, depth=0):
                                    if depth > 20: return val
                                    if isinstance(val, int) and 0 <= val < len(n_data):
                                        if val in n_memo: return n_memo[val]
                                        raw = n_data[val]
                                        if isinstance(raw, list) and len(raw) == 2 and raw[0] in ('ShallowReactive', 'Reactive', 'Set', 'Map'):
                                            res = n_resolve(raw[1], depth + 1)
                                            n_memo[val] = res
                                            return res
                                        if isinstance(raw, dict):
                                            res = {}
                                            n_memo[val] = res
                                            for k, v in raw.items(): res[k] = n_resolve(v, depth + 1)
                                            return res
                                        if isinstance(raw, list):
                                            res = [n_resolve(x, depth + 1) for x in raw]
                                            n_memo[val] = res
                                            return res
                                        return raw
                                    if isinstance(val, dict):
                                        return {k: n_resolve(v, depth + 1) for k, v in val.items()}
                                    if isinstance(val, list):
                                        return [n_resolve(x, depth + 1) for x in val]
                                    return val

                                for item in n_data:
                                    if isinstance(item, dict) and 'team_A' in item and 'team_B' in item and 'status' in item:
                                        rm = n_resolve(item)
                                        mid = str(rm.get("id") or rm.get("match_id") or "")
                                        uuid = str(rm.get("uuid") or rm.get("match_uuid") or "")
                                        if KNOWN_MATCH_IDS and (mid not in KNOWN_MATCH_IDS) and (uuid not in KNOWN_MATCH_IDS):
                                            continue
                                        t_a = rm.get("team_A", {}).get("name", "") if isinstance(rm.get("team_A"), dict) else str(rm.get("team_A") or "")
                                        t_b = rm.get("team_B", {}).get("name", "") if isinstance(rm.get("team_B"), dict) else str(rm.get("team_B") or "")
                                        if mid and t_a and t_b: match_names_map[mid] = (t_a, t_b)
                                        if uuid and t_a and t_b: match_names_map[uuid] = (t_a, t_b)
                                        raw_st = str(rm.get("status") or "").strip()
                                        raw_pr = str(rm.get("period") or "").strip()
                                        is_m_ft = raw_st.lower() in ("played", "ms", "ft", "finished", "bitti") or raw_pr.lower() in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")
                                        ext = rm.get("extras") or {}
                                        match_dict = {
                                            "id": mid,
                                            "match_id": mid,
                                            "uuid": uuid,
                                            "match_uuid": uuid,
                                            "status": "Played" if is_m_ft else raw_st,
                                            "period": raw_pr,
                                            "minute": rm.get("minute"),
                                            "fts_A": rm.get("fts_A"),
                                            "fts_B": rm.get("fts_B"),
                                            "hts_A": rm.get("hts_A"),
                                            "hts_B": rm.get("hts_B"),
                                            "rc_A": ext.get("team_A_redcards") or rm.get("rc_A") or rm.get("rc_home") or 0,
                                            "rc_B": ext.get("team_B_redcards") or rm.get("rc_B") or rm.get("rc_away") or 0,
                                            "rc_home": ext.get("team_A_redcards") or rm.get("rc_A") or rm.get("rc_home") or 0,
                                            "rc_away": ext.get("team_B_redcards") or rm.get("rc_B") or rm.get("rc_away") or 0,
                                            "home_team_name": t_a,
                                            "away_team_name": t_b,
                                            "extras": ext
                                        }
                                        new_summary_map[mid] = match_dict
                                        process_match_update(match_dict, is_initial=False, is_from_full_sync=True)
                                if new_summary_map:
                                    log_event(f"✓ Sahadan HTML fallback ile {len(new_summary_map)} maç durumu başarıyla çekildi.")
                    except Exception as html_sync_err:
                        log_event(f"Sahadan HTML fallback hatası: {html_sync_err}")

                if new_summary_map:
                    # Mevcut özet listesiyle birleştir (mevcut maçları ezmeden güncelle)
                    existing_map = {str(m.get("id")): m for m in latest_matches_summary}
                    for mid_k, m_val in new_summary_map.items():
                        existing_map[str(mid_k)] = m_val
                    latest_matches_summary = list(existing_map.values())
                    last_full_fetch = now
                    if is_initial_sync:
                        is_initial_sync = False
                        live_cnt = len([x for x in latest_matches_summary if str(x.get("status") or "").lower() == "playing"])
                        played_cnt = len([x for x in latest_matches_summary if str(x.get("status") or "").lower() == "played"])
                        log_event(f"✓ Sahadan canlı maç tablosu yüklendi (2 gün): Toplam {len(latest_matches_summary)} maç (Canlı: {live_cnt}, Biten: {played_cnt})")

                    # Başlamasına <= 75 dk kalmış maçların kadrolarını arka planda önceden önbelleğe al
                    if (now - last_lineup_preload >= 60) and latest_matches_summary:
                        last_lineup_preload = now
                        def _preload_lineups_bg(matches_to_check):
                            try:
                                for _sm in matches_to_check:
                                    _st = str(_sm.get("status") or "").lower()
                                    if _st in ("fixture", "not started", "", "time postponed"):
                                        _dt_str = str(_sm.get("date_time") or "")
                                        _diff_sec = 999999
                                        if _dt_str:
                                            try:
                                                _iso = _dt_str.replace(" ", "T")
                                                if not _iso.endswith("Z"): _iso += "Z"
                                                _mts = datetime.datetime.fromisoformat(_iso.replace("Z", "+00:00")).timestamp()
                                                _diff_sec = _mts - time.time()
                                            except Exception:
                                                pass
                                        # Başlamasına 75 dk veya daha az kalmışsa (veya 15 dk geçmişse)
                                        if -900 <= _diff_sec <= 4500:
                                            _u = _sm.get("uuid") or _sm.get("match_uuid") or _sm.get("id")
                                            _h = _sm.get("home_team_name") or ""
                                            _a = _sm.get("away_team_name") or ""
                                            if _u and _h and _a:
                                                _cand_keys = [str(_u), f"{normalize_team_name(_h)}___{normalize_team_name(_a)}"]
                                                if not any(ck in MATCH_LINEUPS_CACHE and MATCH_LINEUPS_CACHE[ck].get("data", {}).get("has_lineup") for ck in _cand_keys):
                                                    fetch_match_lineup(_h, _a, _u, force_refresh=False)
                                                    time.sleep(0.5)
                            except Exception:
                                pass
                        threading.Thread(target=_preload_lineups_bg, args=(list(latest_matches_summary),), daemon=True).start()
                else:
                    # 429 veya 502 durumunda her 1.5 sn saldırmak yerine 20 sn bekle
                    last_full_fetch = now - 10
                    is_initial_sync = False
            except Exception as e:
                log_event(f"Sahadan full sync hatası: {e}")
                last_full_fetch = now - 10
                is_initial_sync = False


        # 2. Her 3 saniyede bir anlık olayları çek (soccer-sync-data)
        if not is_initial_sync:
            try:
                u = int(now / 2)
                sync_url = f"https://www.sahadan.com/api/index/soccer-sync-data?a=bs&e=sces&u={u}"
                req = urllib.request.Request(sync_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as res:
                    changes = json.loads(res.read().decode("utf-8"))
                    if changes and isinstance(changes, list):
                        for item in changes:
                            mid = str(item.get("match_id") or item.get("id") or item.get("uuid") or "")
                            uuid = str(item.get("uuid") or item.get("match_uuid") or "")
                            if KNOWN_MATCH_IDS and (mid not in KNOWN_MATCH_IDS) and (uuid not in KNOWN_MATCH_IDS):
                                continue
                            process_match_update(item, is_initial=False)
                            tracked = live_matches_state.get(mid)
                            found_in_summary = False
                            for existing in latest_matches_summary:
                                if str(existing.get("id")) == mid or str(existing.get("uuid")) == mid:
                                    found_in_summary = True
                                    if tracked and tracked.get("home_score") is not None:
                                        existing["fts_A"] = tracked["home_score"]
                                    elif item.get("fts_A") is not None:
                                        existing["fts_A"] = item["fts_A"]

                                    if tracked and tracked.get("away_score") is not None:
                                        existing["fts_B"] = tracked["away_score"]
                                    elif item.get("fts_B") is not None:
                                        existing["fts_B"] = item["fts_B"]

                                    st = str(item.get("status") or (tracked.get("status") if tracked else "") or "").strip()
                                    pr = str(item.get("period") or (tracked.get("period") if tracked else "") or "").strip()
                                    is_end = st.lower() in ("played", "ms", "ft", "finished", "bitti") or pr.lower() in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")
                                    cur_rank = get_match_period_rank(existing.get("period"), existing.get("status"))
                                    new_rank = get_match_period_rank(pr, st)
                                    is_regression = (new_rank < cur_rank and cur_rank >= 2)
                                    if not is_regression:
                                        if is_end:
                                            existing["status"] = "Played"
                                        elif st and st.lower() != "fixture":
                                            existing["status"] = st
                                        elif pr:
                                            existing["period"] = pr
                                            if any(k in pr.lower() for k in ("half", "yarı", "1h", "2h", "ht", "iy")):
                                                existing["status"] = "Playing"

                                    if tracked and tracked.get("minute"):
                                        existing["minute"] = tracked["minute"]
                                    elif item.get("minute") is not None:
                                        existing["minute"] = item["minute"]

                                    if existing.get("minute") and str(existing.get("status") or "").lower() == "fixture":
                                        existing["status"] = "Playing"

                                    if tracked and tracked.get("rc_home") is not None:
                                        existing["rc_A"] = tracked["rc_home"]
                                        existing["rc_home"] = tracked["rc_home"]
                                    if tracked and tracked.get("rc_away") is not None:
                                        existing["rc_B"] = tracked["rc_away"]
                                        existing["rc_away"] = tracked["rc_away"]
                                    break
                            
                            # Eğer maç özette yoksa yeni maç kartı oluştur ve listeye ekle
                            if not found_in_summary and (item.get("status") or item.get("period")):
                                h_name = tracked.get("home_team", "") if tracked else ""
                                a_name = tracked.get("away_team", "") if tracked else ""
                                if not h_name or not a_name:
                                    cached_pair = match_names_map.get(mid, ("", ""))
                                    h_name, a_name = cached_pair[0], cached_pair[1]
                                new_entry = {
                                    "id": mid,
                                    "match_id": mid,
                                    "uuid": uuid,
                                    "match_uuid": uuid,
                                    "status": item.get("status") or (tracked.get("status") if tracked else "Playing"),
                                    "period": item.get("period") or (tracked.get("period") if tracked else ""),
                                    "minute": item.get("minute") or (tracked.get("minute") if tracked else ""),
                                    "fts_A": item.get("fts_A") if item.get("fts_A") is not None else (tracked.get("home_score") if tracked else None),
                                    "fts_B": item.get("fts_B") if item.get("fts_B") is not None else (tracked.get("away_score") if tracked else None),
                                    "hts_A": item.get("hts_A"),
                                    "hts_B": item.get("hts_B"),
                                    "rc_A": tracked.get("rc_home", 0) if tracked else 0,
                                    "rc_B": tracked.get("rc_away", 0) if tracked else 0,
                                    "rc_home": tracked.get("rc_home", 0) if tracked else 0,
                                    "rc_away": tracked.get("rc_away", 0) if tracked else 0,
                                    "home_team_name": h_name,
                                    "away_team_name": a_name,
                                    "extras": item.get("extras") or {}
                                }
                                latest_matches_summary.append(new_entry)
            except Exception:
                pass

        time.sleep(1.5)

# Live WebSocket Listener (İkincil hızlı kanal)
def start_socket_listener():
    sio = socketio.Client(reconnection=True, reconnection_delay=2, reconnection_delay_max=10)

    @sio.on("connect")
    def on_connect():
        log_event("✓ Sahadan Canlı Socket Yayınına Bağlandı!")
        sio.emit("join-room", "soccer")

    @sio.on("disconnect")
    def on_disconnect():
        log_event("⚠ Socket bağlantısı koptu, yeniden bağlanılıyor...")

    @sio.on("matches")
    def on_matches(data):
        if not data:
            return
        content = data.get("content") if isinstance(data, dict) and "content" in data else data
        items = content if isinstance(content, list) else [content]
        for item in items:
            process_match_update(item, is_initial=False)
            raw_mid = item.get("match_id") or item.get("id")
            raw_uuid = item.get("uuid") or item.get("match_uuid")
            mid = str(raw_mid or "").strip()
            uuid = str(raw_uuid or "").strip()
            if not mid and not uuid:
                continue
            if KNOWN_MATCH_IDS and (mid not in KNOWN_MATCH_IDS) and (uuid not in KNOWN_MATCH_IDS):
                continue
            tracked = live_matches_state.get(mid) or (live_matches_state.get(uuid) if uuid else None)
            found_in_summary = False
            for existing in latest_matches_summary:
                ex_id = str(existing.get("id") or existing.get("match_id") or "").strip()
                ex_u = str(existing.get("uuid") or existing.get("match_uuid") or "").strip()
                if (mid and ex_id == mid) or (uuid and ex_u == uuid) or (mid and ex_u == mid) or (uuid and ex_id == uuid):
                    found_in_summary = True
                    # eksik uuid veya id varsa güncelle
                    if uuid and not existing.get("uuid"):
                        existing["uuid"] = uuid
                        existing["match_uuid"] = uuid
                    if mid and not existing.get("id"):
                        existing["id"] = mid
                        existing["match_id"] = mid

                    if tracked and tracked.get("home_score") is not None:
                        existing["fts_A"] = tracked["home_score"]
                    elif item.get("fts_A") is not None:
                        existing["fts_A"] = item["fts_A"]

                    if tracked and tracked.get("away_score") is not None:
                        existing["fts_B"] = tracked["away_score"]
                    elif item.get("fts_B") is not None:
                        existing["fts_B"] = item["fts_B"]

                    st = str(item.get("status") or (tracked.get("status") if tracked else "") or "").strip()
                    pr = str(item.get("period") or (tracked.get("period") if tracked else "") or "").strip()
                    is_end = st.lower() in ("played", "ms", "ft", "finished", "bitti") or pr.lower() in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")
                    cur_rank = get_match_period_rank(existing.get("period"), existing.get("status"))
                    new_rank = get_match_period_rank(pr, st)
                    is_regression = (new_rank < cur_rank and cur_rank >= 2)
                    if not is_regression:
                        if is_end:
                            existing["status"] = "Played"
                        elif st and st.lower() != "fixture":
                            existing["status"] = st
                        elif pr:
                            existing["period"] = pr
                            if any(k in pr.lower() for k in ("half", "yarı", "1h", "2h", "ht", "iy")):
                                existing["status"] = "Playing"

                    if tracked and tracked.get("minute"):
                        existing["minute"] = tracked["minute"]
                    elif item.get("minute") is not None:
                        existing["minute"] = item["minute"]

                    if existing.get("minute") and str(existing.get("status") or "").lower() == "fixture":
                        existing["status"] = "Playing"

                    # Kırmızı kart sayılarını güncelle (tracked veya item'dan, max al)
                    if tracked:
                        new_rc_h = max(int(tracked.get("rc_home") or 0), int(existing.get("rc_A") or existing.get("rc_home") or 0))
                        new_rc_a = max(int(tracked.get("rc_away") or 0), int(existing.get("rc_B") or existing.get("rc_away") or 0))
                        existing["rc_A"] = new_rc_h
                        existing["rc_home"] = new_rc_h
                        existing["rc_B"] = new_rc_a
                        existing["rc_away"] = new_rc_a
                    break

            if not found_in_summary and (item.get("status") or item.get("period")):
                h_name = tracked.get("home_team", "") if tracked else ""
                a_name = tracked.get("away_team", "") if tracked else ""
                if not h_name or not a_name:
                    cached_pair = match_names_map.get(mid or uuid, ("", ""))
                    h_name, a_name = cached_pair[0], cached_pair[1]
                if not h_name or not a_name:
                    continue  # İsimsiz sahte kayıt eklenmesini kesinlikle engelle

                new_entry = {
                    "id": mid or uuid,
                    "match_id": mid or uuid,
                    "uuid": uuid or mid,
                    "match_uuid": uuid or mid,
                    "status": item.get("status") or (tracked.get("status") if tracked else "Playing"),
                    "period": item.get("period") or (tracked.get("period") if tracked else ""),
                    "minute": item.get("minute") or (tracked.get("minute") if tracked else ""),
                    "fts_A": item.get("fts_A") if item.get("fts_A") is not None else (tracked.get("home_score") if tracked else None),
                    "fts_B": item.get("fts_B") if item.get("fts_B") is not None else (tracked.get("away_score") if tracked else None),
                    "hts_A": item.get("hts_A"),
                    "hts_B": item.get("hts_B"),
                    "rc_A": tracked.get("rc_home", 0) if tracked else 0,
                    "rc_B": tracked.get("rc_away", 0) if tracked else 0,
                    "rc_home": tracked.get("rc_home", 0) if tracked else 0,
                    "rc_away": tracked.get("rc_away", 0) if tracked else 0,
                    "home_team_name": h_name,
                    "away_team_name": a_name,
                    "home_team": h_name,
                    "away_team": a_name,
                    "extras": item.get("extras") or {}
                }
                latest_matches_summary.append(new_entry)


    while True:
        try:
            sio.connect("https://socket.mackolikfeeds.com/mksh", socketio_path="/socket.io", transports=["websocket"], wait_timeout=10)
            sio.wait()
        except Exception:
            time.sleep(5)

# Canlı Maçlar & Kırmızı Kart Sürekli Derin Senkronizasyon Servisi (12 sn periyot)
def red_card_monitor_worker():
    time.sleep(15)  # Sunucu ilk açılışta maç verilerinin oturmasını bekle
    log_event("✓ Canlı Maç & Kırmızı Kart Derin Senkronizasyon Servisi aktif (12 sn periyot).")

    while True:
        try:
            time.sleep(12)

            subs = load_subscriptions()
            all_favs = set()
            for s in subs:
                for f in s.get("favorites", []):
                    if f:
                        all_favs.add(str(f).strip().lower())

            # Canlı oynanan tüm maçları bul (CDN bayatlamasına karşı derin koruma)
            live_matches = []
            for m in list(latest_matches_summary):
                st = str(m.get("status") or "").strip().lower()
                pr = str(m.get("period") or "").strip().lower()
                min_val = str(m.get("minute") or "").strip()
                is_live = st in ("playing", "canlı", "1.yarı", "2.yarı", "uzatma") or any(k in pr for k in ("half", "yarı", "1h", "2h", "ht", "iy")) or (min_val.isdigit() and st != "played")
                if is_live:
                    live_matches.append(m)

            if not live_matches:
                continue

            for m in live_matches:
                mid = str(m.get("id") or m.get("match_id") or m.get("uuid") or "")
                uuid = str(m.get("uuid") or m.get("match_uuid") or "")
                h_name = str(m.get("home_team") or m.get("home_team_name") or "")
                a_name = str(m.get("away_team") or m.get("away_team_name") or "")

                if not uuid or not h_name or not a_name:
                    continue

                card_data = fetch_match_red_cards(h_name, a_name, uuid)
                new_rc_h = card_data.get("rc_home", 0)
                new_rc_a = card_data.get("rc_away", 0)

                # CDN bayatlamasına karşı canlı maç özetini de (skor, dakika, durum, kart) güncelle
                if card_data.get("fts_A") is not None:
                    m["fts_A"] = card_data["fts_A"]
                if card_data.get("fts_B") is not None:
                    m["fts_B"] = card_data["fts_B"]
                if card_data.get("minute"):
                    m["minute"] = card_data["minute"]
                if card_data.get("status"):
                    m["status"] = card_data["status"]
                if card_data.get("period"):
                    m["period"] = card_data["period"]

                m["rc_home"] = new_rc_h
                m["rc_A"] = new_rc_h
                m["rc_away"] = new_rc_a
                m["rc_B"] = new_rc_a

                tracked = live_matches_state.setdefault(mid, {
                    "home_team": h_name,
                    "away_team": a_name,
                    "home_score": m.get("fts_A"),
                    "away_score": m.get("fts_B"),
                    "minute": m.get("minute"),
                    "status": m.get("status"),
                    "period": m.get("period"),
                    "rc_home": 0,
                    "rc_away": 0,
                    "notified_scores": set(),
                    "notified_ht": False,
                    "notified_ft": False
                })
                if uuid and uuid != mid:
                    live_matches_state[uuid] = tracked

                if card_data.get("fts_A") is not None:
                    tracked["home_score"] = card_data["fts_A"]
                if card_data.get("fts_B") is not None:
                    tracked["away_score"] = card_data["fts_B"]
                if card_data.get("minute"):
                    tracked["minute"] = card_data["minute"]
                if card_data.get("status"):
                    tracked["status"] = card_data["status"]
                if card_data.get("period"):
                    tracked["period"] = card_data["period"]

                old_rc_h = tracked.get("rc_home", 0)
                old_rc_a = tracked.get("rc_away", 0)

                all_identifiers = [
                    mid,
                    str(m.get("id", "")),
                    str(m.get("match_id", "")),
                    uuid,
                    h_name,
                    a_name
                ]
                all_identifiers = [i for i in all_identifiers if i]

                min_str = f"{m.get('minute')}'" if m.get('minute') else "Canlı"
                h_score = tracked.get('home_score', m.get('fts_A', 0)) or 0
                a_score = tracked.get('away_score', m.get('fts_B', 0)) or 0

                # Ev Sahibi Kırmızı Kart
                if new_rc_h > old_rc_h:
                    tracked["rc_home"] = new_rc_h
                    m["rc_A"] = new_rc_h
                    m["rc_home"] = new_rc_h
                    p_name = ""
                    for c in card_data.get("cards", []):
                        if str(c.get("team")).upper() == "A" and c.get("player"):
                            p_name = f" ({c['player']})"
                            break
                    title = f"🟥 Kırmızı Kart! {h_name} ({min_str})"
                    body = f"{h_name}{p_name} kırmızı kart gördü! ({h_name} {h_score} - {a_score} {a_name})"
                    log_event(f"KIRMIZI KART: {title} -> {body}")
                    send_push_for_match(all_identifiers, {
                        "title": title,
                        "body": body,
                        "icon": "icons/icon-192.png",
                        "tag": f"rc-{mid}-{time.time()}"
                    })

                # Deplasman Kırmızı Kart
                if new_rc_a > old_rc_a:
                    tracked["rc_away"] = new_rc_a
                    m["rc_B"] = new_rc_a
                    m["rc_away"] = new_rc_a
                    p_name = ""
                    for c in card_data.get("cards", []):
                        if str(c.get("team")).upper() == "B" and c.get("player"):
                            p_name = f" ({c['player']})"
                            break
                    title = f"🟥 Kırmızı Kart! {a_name} ({min_str})"
                    body = f"{a_name}{p_name} kırmızı kart gördü! ({h_name} {h_score} - {a_score} {a_name})"
                    log_event(f"KIRMIZI KART: {title} -> {body}")
                    send_push_for_match(all_identifiers, {
                        "title": title,
                        "body": body,
                        "icon": "icons/icon-192.png",
                        "tag": f"rc-{mid}-{time.time()}"
                    })

                time.sleep(0.3)

        except Exception as e:
            log_event(f"Kırmızı kart izleyici döngü hatası: {e}")
            time.sleep(10)

class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if self.path.startswith("/api/stream-player"):
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
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
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(player_html.encode("utf-8"))
            else:
                self.send_response(302)
                self.send_header("Location", f"https://ntv.cx/watch/{server_name}/{match_id}")
                self.end_headers()
            return

        if self.path.startswith("/api/stream-embed"):
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
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
                    print("Error resolving stream embed:", e)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({"success": bool(embed_url), "embed_url": embed_url}).encode("utf-8"))
            return

        if self.path.startswith("/api/status"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "version": "v88",
                "provider": "sahadan",
                "cached_goals": len(MATCH_GOALS_CACHE),
                "cached_cards": len(MATCH_CARDS_CACHE),
                "cached_lineups": len(MATCH_LINEUPS_CACHE),
                "python": sys.version
            }).encode("utf-8"))
            return

        if self.path.startswith("/api/match-goals"):
            from urllib.parse import urlparse, parse_qs
            try:
                raw_path = self.path.encode("iso-8859-1").decode("utf-8")
            except Exception:
                raw_path = self.path
            query = parse_qs(urlparse(raw_path).query)
            uuid = query.get("uuid", [""])[0]
            home = query.get("home", [""])[0]
            away = query.get("away", [""])[0]
            min_goals = int(query.get("min_goals", [0])[0] or 0)
            force_refresh = query.get("force_refresh", ["0"])[0] == "1"
            goals = []
            cards = []
            if uuid or (home and away):
                goals = fetch_match_goals(home, away, uuid, min_goals=min_goals, force_refresh=force_refresh)
                cards = get_cached_match_cards(uuid, home, away)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "goals": goals, "cards": cards}, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/debug-lineup"):
            from urllib.parse import urlparse, parse_qs
            try:
                raw_path = self.path.encode("iso-8859-1").decode("utf-8")
            except Exception:
                raw_path = self.path
            query = parse_qs(urlparse(raw_path).query)
            uuid = query.get("uuid", [""])[0]
            home = query.get("home", [""])[0]
            away = query.get("away", [""])[0]
            scrape_uuid = resolve_match_uuid(uuid, home, away)
            slug = f"{to_sahadan_slug(home)}-vs-{to_sahadan_slug(away)}"
            url = f"https://www.sahadan.com/mac/{slug}/{scrape_uuid}"
            headers_req = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9",
                "Referer": "https://www.sahadan.com/",
            }
            debug_result = {"url": url, "scrape_uuid": scrape_uuid, "slug": slug}
            try:
                req = urllib.request.Request(url, headers=headers_req)
                html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8")
                m_script = re.search(r'<script[^>]*id=\"__NUXT_DATA__\"[^>]*>(.*?)</script>', html, re.DOTALL)
                if not m_script:
                    debug_result["error"] = "No __NUXT_DATA__ script tag found"
                    debug_result["html_snippet"] = html[:2000]
                else:
                    raw_data = json.loads(m_script.group(1))
                    memo = {}
                    def _resolve(val, depth=0):
                        if depth > 25: return val
                        if isinstance(val, int) and 0 <= val < len(raw_data):
                            if val in memo: return memo[val]
                            r = raw_data[val]
                            if isinstance(r, list) and len(r) == 2 and r[0] in ('ShallowReactive', 'Reactive', 'Set', 'Map'):
                                res = _resolve(r[1], depth+1); memo[val] = res; return res
                            if isinstance(r, dict):
                                res = {}; memo[val] = res
                                for k2, v2 in r.items(): res[k2] = _resolve(v2, depth+1)
                                return res
                            if isinstance(r, list):
                                res = []; memo[val] = res
                                for item in r: res.append(_resolve(item, depth+1))
                                return res
                            return r
                        elif isinstance(val, dict):
                            return {k2: _resolve(v2, depth+1) for k2, v2 in val.items()}
                        elif isinstance(val, list):
                            return [_resolve(v2, depth+1) for v2 in val]
                        return val
                    resolved = _resolve(2)
                    # Find keys containing lineup-related fields
                    lineup_search = {}
                    for k2, v2 in resolved.items():
                        if not isinstance(v2, dict): continue
                        d = v2.get("data") or {}
                        if isinstance(d, dict):
                            lineup_search[str(k2)] = {
                                "has_data_key": "data" in v2,
                                "data_keys": list(d.keys())[:20] if isinstance(d, dict) else str(type(d)),
                                "has_lineup": "lineup" in d,
                                "has_lineup_home": "lineup_home" in d,
                                "has_lineupHome": "lineupHome" in d,
                                "has_teams": "teams" in d,
                            }
                    debug_result["top_level_keys"] = list(resolved.keys())[:30]
                    debug_result["data_subkeys_per_top"] = lineup_search
                    # Also search for 'lineup' anywhere in resolved (shallow)
                    any_lineup = {k2: list(v2.keys())[:15] if isinstance(v2, dict) else str(type(v2))
                                   for k2, v2 in resolved.items() if isinstance(v2, dict) and ("lineup" in str(v2)[:500])}
                    debug_result["any_lineup_mention"] = any_lineup
            except Exception as ex:
                debug_result["error"] = str(ex)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(debug_result, ensure_ascii=False, default=str).encode("utf-8"))
            return

        if self.path.startswith("/api/match-lineup"):

            from urllib.parse import urlparse, parse_qs
            try:
                raw_path = self.path.encode("iso-8859-1").decode("utf-8")
            except Exception:
                raw_path = self.path
            query = parse_qs(urlparse(raw_path).query)
            uuid = query.get("uuid", [""])[0]
            home = query.get("home", [""])[0]
            away = query.get("away", [""])[0]
            force_refresh = query.get("force_refresh", ["0"])[0] == "1"
            lineup_res = fetch_match_lineup(home, away, uuid, force_refresh=force_refresh)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(lineup_res, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/live-sync") or self.path.startswith("/api/live-matches"):
            clean_matches = []
            seen_u = set()
            for sm in latest_matches_summary:
                mid_key = str(sm.get("id") or sm.get("match_id") or "").strip()
                uuid_key = str(sm.get("uuid") or sm.get("match_uuid") or "").strip()
                if KNOWN_MATCH_IDS and (mid_key not in KNOWN_MATCH_IDS) and (uuid_key not in KNOWN_MATCH_IDS):
                    # STANDALONE_LIVE_COMPETITIONS (Uluslar Ligi vb.) KNOWN_MATCH_IDS'e girmemiş
                    # olsa bile (örn. Render IP engeli nedeniyle) live-sync'ten geçmesine izin ver
                    _sm_comp = str(sm.get("competition_name") or sm.get("league_name") or "").strip().lower()
                    if _sm_comp not in STANDALONE_LIVE_COMPETITIONS:
                        continue
                h_name = sm.get("home_team_name") or sm.get("home_team") or ""
                a_name = sm.get("away_team_name") or sm.get("away_team") or ""
                if not h_name or not a_name:
                    continue
                dedup_key = uuid_key or mid_key
                if dedup_key and dedup_key in seen_u:
                    continue
                if dedup_key:
                    seen_u.add(dedup_key)
                sm["home_team"] = h_name
                sm["away_team"] = a_name
                sm["home_team_name"] = h_name
                sm["away_team_name"] = a_name

                # Canlı takip objesi varsa (skor, dakika, kırmızı kart, durum) senkronize et
                tracked = live_matches_state.get(mid_key) or (live_matches_state.get(uuid_key) if uuid_key else None)
                if tracked:
                    if tracked.get("home_score") is not None:
                        sm["fts_A"] = tracked["home_score"]
                    if tracked.get("away_score") is not None:
                        sm["fts_B"] = tracked["away_score"]
                    if tracked.get("minute"):
                        t_min = str(tracked["minute"]).strip()
                        s_min = str(sm.get("minute") or "").strip()
                        if t_min.isdigit():
                            if not s_min.isdigit() or int(t_min) > int(s_min):
                                sm["minute"] = int(t_min)
                        elif not sm.get("minute"):
                            sm["minute"] = tracked["minute"]
                    if tracked.get("status"):
                        sm["status"] = tracked["status"]
                    if tracked.get("period"):
                        sm["period"] = tracked["period"]
                    if tracked.get("rc_home"):
                        sm["rc_A"] = max(int(sm.get("rc_A") or 0), int(tracked["rc_home"]))
                        sm["rc_home"] = sm["rc_A"]
                    if tracked.get("rc_away"):
                        sm["rc_B"] = max(int(sm.get("rc_B") or 0), int(tracked["rc_away"]))
                        sm["rc_away"] = sm["rc_B"]

                # Cache fallback for red cards if not in tracked
                norm_pair = f"{normalize_team_name(h_name)}___{normalize_team_name(a_name)}"
                for ck in [mid_key, uuid_key, norm_pair]:
                    if not ck:
                        continue
                    if ck in MATCH_CARDS_CACHE:
                        c_data = MATCH_CARDS_CACHE[ck].get("data", {})
                        if c_data.get("rc_home"):
                            sm["rc_A"] = max(int(sm.get("rc_A") or 0), int(c_data["rc_home"]))
                            sm["rc_home"] = sm["rc_A"]
                        if c_data.get("rc_away"):
                            sm["rc_B"] = max(int(sm.get("rc_B") or 0), int(c_data["rc_away"]))
                            sm["rc_away"] = sm["rc_B"]
                    if ck in MATCH_GOALS_CACHE:
                        g_data = MATCH_GOALS_CACHE[ck]
                        if g_data.get("rc_home"):
                            sm["rc_A"] = max(int(sm.get("rc_A") or 0), int(g_data["rc_home"]))
                            sm["rc_home"] = sm["rc_A"]
                        if g_data.get("rc_away"):
                            sm["rc_B"] = max(int(sm.get("rc_B") or 0), int(g_data["rc_away"]))
                            sm["rc_away"] = sm["rc_B"]

                # Durum ve periyot çözümleme: Başlamış/bitmiş maçların Fixture görünmesini engelle
                raw_st = str(sm.get("status") or "").strip().lower()
                raw_pr = str(sm.get("period") or "").strip().lower()
                is_end_st = raw_st in ("played", "ms", "ft", "finished", "bitti") or raw_pr in ("played", "ms", "ft", "finished", "full time", "fulltime", "maç bitti")

                dt_str = str(sm.get("date_time") or MATCH_DATETIME_MAP.get(mid_key) or MATCH_DATETIME_MAP.get(uuid_key) or "")
                diff_mins = None
                if dt_str:
                    try:
                        m_dt = datetime.datetime.strptime(dt_str[:16], "%Y-%m-%d %H:%M")
                        now_utc = datetime.datetime.utcnow()
                        diff_mins = (now_utc - m_dt).total_seconds() / 60.0
                    except Exception:
                        pass

                # 1. Açıkça bitmiş maçlar
                if is_end_st:
                    sm["status"] = "Played"
                # 2. Maç oynanıyor görünse bile (Playing) başlama saatinden 125+ dk geçmiş ve 2. yarı / 85+ dk ise (veya 150+ dk) bitmiştir
                elif diff_mins is not None and diff_mins >= 125 and (
                    str(sm.get("minute", "")).startswith("90") or 
                    (sm.get("minute") and str(sm.get("minute")).isdigit() and int(sm.get("minute")) >= 85) or 
                    "second" in raw_pr or "2" in raw_pr or diff_mins >= 150
                ):
                    sm["status"] = "Played"
                    sm["period"] = "Full Time"
                    sm["minute"] = None
                    if tracked:
                        tracked["status"] = "Played"
                        tracked["period"] = "Full Time"
                # 3. Başlama saatinden 180+ dk (3 saat) geçmiş herhangi bir maç bitmiştir
                elif diff_mins is not None and diff_mins >= 180:
                    sm["status"] = "Played"
                    sm["period"] = "Full Time"
                    sm["minute"] = None
                    if tracked:
                        tracked["status"] = "Played"
                        tracked["period"] = "Full Time"
                # 4. Fixture görünen maçlar için canlı/bitmiş kontrolü
                elif raw_st in ("fixture", ""):
                    has_min = sm.get("minute") is not None and str(sm.get("minute")).strip() not in ("", "None")
                    is_live_pr = any(k in raw_pr for k in ("half", "yarı", "ht", "iy", "1h", "2h", "et", "pen", "uzatma"))
                    has_score = (sm.get("fts_A") is not None or sm.get("fts_B") is not None)
                    if has_min or is_live_pr:
                        sm["status"] = "Playing"
                    elif has_score:
                        if diff_mins is not None and diff_mins >= 130:
                            sm["status"] = "Played"
                            sm["period"] = "Full Time"
                            sm["minute"] = None
                        else:
                            sm["status"] = "Playing"

                # 5. Canlı maçlarda dakika senkronizasyonu ve 1. Yarı başlama saati doğrulaması
                if str(sm.get("status") or "").lower() == "playing":
                    is_1h = any(k in raw_pr for k in ("first", "1", "1h", "1. yarı"))
                    is_ht = any(k in raw_pr for k in ("half time", "devre arası", "ht", "iy"))
                    if not is_ht and is_1h and diff_mins is not None and 1 <= diff_mins <= 45 and not sm.get("minute_extra"):
                        est_min = int(diff_mins)
                        c_min = sm.get("minute")
                        if c_min is None or (str(c_min).isdigit() and int(c_min) < est_min - 2):
                            sm["minute"] = est_min

                clean_matches.append(sm)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "count": len(clean_matches),
                "matches": clean_matches
            }, ensure_ascii=False).encode("utf-8"))
            return

        if self.path == "/api/vapid-key":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"public_key": vapid_keys["public_key"]}).encode("utf-8"))
            return

        if self.path == "/api/subscriptions":
            subs = load_subscriptions()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"count": len(subs)}).encode("utf-8"))
            return

        if self.path == "/api/diagnose":
            subs = load_subscriptions()
            safe_subs = []
            for s in subs:
                ep = s.get("endpoint", "")
                domain = ep.split("/")[2] if "//" in ep else "unknown"
                safe_subs.append({
                    "domain": domain,
                    "endpoint_preview": ep[:40] + "...",
                    "has_keys": bool(s.get("keys")),
                    "favorites_count": len(s.get("favorites", [])),
                    "favorites": s.get("favorites", [])[:10]
                })
            live_cnt = len([x for x in latest_matches_summary if x.get("status") == "Playing"])
            played_cnt = len([x for x in latest_matches_summary if x.get("status") == "Played"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "subscribers_count": len(subs),
                "sync_total_matches": len(latest_matches_summary),
                "sync_live_matches": live_cnt,
                "sync_played_matches": played_cnt,
                "subscribers": safe_subs,
                "recent_logs": last_push_logs
            }, indent=2, ensure_ascii=False).encode("utf-8"))
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/subscribe":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                sub_data = json.loads(body)
                subs = load_subscriptions()
                endpoint = sub_data.get("endpoint")
                
                favs = [str(f) for f in sub_data.get("favorites", [])]
                existing = next((s for s in subs if s.get("endpoint") == endpoint), None)
                if existing:
                    if "keys" in sub_data:
                        existing["keys"] = sub_data["keys"]
                    existing["favorites"] = favs
                    log_event(f"Abone favorileri güncellendi ({len(favs)} maç): {endpoint[:40]}...")
                else:
                    subs.append({
                        "endpoint": endpoint,
                        "keys": sub_data.get("keys", {}),
                        "favorites": favs
                    })
                    log_event(f"Yeni abone kaydedildi ({len(favs)} favori): {endpoint[:40]}...")
                    # Send welcome push
                    send_push_to_sub(sub_data, {
                        "title": "✅ Bildirimler Aktif!",
                        "body": "Yıldızladığınız (★) maçların gol, devre, maç sonu ve kırmızı kart bildirimleri gelecek.",
                        "icon": "icons/icon-192.png",
                        "tag": "welcome"
                    })

                save_subscriptions(subs)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "subscribers": len(subs)}).encode("utf-8"))
            except Exception as e:
                log_event(f"Subscribe hatası: {e}")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if self.path == "/api/test-push":
            content_length = int(self.headers.get("Content-Length", 0))
            custom_payload = None
            if content_length > 0:
                try:
                    custom_payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                except Exception:
                    pass

            payload = custom_payload or {
                "title": "⭐ Test Bildirimi",
                "body": "Favori maç bildirim sisteminiz kusursuz çalışıyor! 🚀",
                "icon": "icons/icon-192.png",
                "tag": "test-push"
            }
            sent, err = send_push_to_all(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "sent": sent, "error": err}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

def keep_alive_ping():
    time.sleep(60)
    while True:
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL", "https://footflow-6550.onrender.com")
            ping_url = f"{url.rstrip('/')}/api/subscriptions"
            req = urllib.request.Request(ping_url, headers={"User-Agent": "RenderKeepAlive/1.0"})
            with urllib.request.urlopen(req, timeout=15) as res:
                if res.status == 200:
                    pass
        except Exception as e:
            pass
        time.sleep(540)

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.TCPServer.allow_reuse_address = True

    # Start Sahadan real-time HTTP sync thread
    http_sync_thread = threading.Thread(target=sahadan_http_sync_worker, daemon=True)
    http_sync_thread.start()

    # Start live socket listener thread
    sock_thread = threading.Thread(target=start_socket_listener, daemon=True)
    sock_thread.start()

    # Start keep-alive ping thread
    keepalive_thread = threading.Thread(target=keep_alive_ping, daemon=True)
    keepalive_thread.start()

    # Start periodic red card monitor thread (3m period, 5s stagger)
    red_card_thread = threading.Thread(target=red_card_monitor_worker, daemon=True)
    red_card_thread.start()

    log_event(f"🚀 FootFlow Web Push Sunucusu Başlatıldı (Port: {PORT})")

    class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ThreadedTCPServer(("", PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
