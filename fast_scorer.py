"""
fast_scorer.py - FootFlow Hızlı Golcü ve Olay Takip Modülü (Flashscore Entegrasyonu)
Bu modül push_server'a paralel ultra-hızlı (5-10 saniye) golcü ve olay verisi sağlar.
Ana kod tabanından tamamen izoledir; hata durumunda ana sistemi asla etkilemez.
"""

import urllib.request
import re
import time
import unicodedata

_FS_FEED_CACHE = {
    "data": {},
    "time": 0
}

def normalize_name(name):
    if not name:
        return ""
    name = str(name).lower().strip()
    replacements = {
        'ı': 'i', 'İ': 'i', 'ş': 's', 'Ş': 's', 'ğ': 'g', 'Ğ': 'g',
        'ü': 'u', 'Ü': 'u', 'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c'
    }
    for tr_char, en_char in replacements.items():
        name = name.replace(tr_char, en_char)
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
    name = re.sub(r'[^a-z0-9]', '', name)
    return name

def _fetch_feed_raw():
    url = "https://www.flashscore.com.tr/x/feed/f_1_0_3_tr_1"
    headers = {
        "X-Fsign": "SW9D1eZo",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.flashscore.com.tr/"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as res:
        return res.read().decode("utf-8")

def get_flashscore_matches(force_refresh=False):
    now = time.time()
    if not force_refresh and (now - _FS_FEED_CACHE["time"] < 25) and _FS_FEED_CACHE["data"]:
        return _FS_FEED_CACHE["data"]

    try:
        raw_text = _fetch_feed_raw()
        matches = {}
        blocks = raw_text.split("~AA÷")
        for b in blocks[1:]:
            parts = b.split("¬")
            mid = parts[0]
            p_dict = {}
            for p in parts[1:]:
                if "÷" in p:
                    k, v = p.split("÷", 1)
                    p_dict[k] = v
            home = p_dict.get("AE") or p_dict.get("FH") or ""
            away = p_dict.get("AF") or p_dict.get("FK") or ""
            if home and away:
                hn = normalize_name(home)
                an = normalize_name(away)
                k1 = f"{hn}___{an}"
                matches[k1] = {
                    "id": mid,
                    "home": home,
                    "away": away,
                    "score_home": p_dict.get("AG"),
                    "score_away": p_dict.get("AH")
                }
        if matches:
            _FS_FEED_CACHE["data"] = matches
            _FS_FEED_CACHE["time"] = now
            return matches
    except Exception:
        pass

    return _FS_FEED_CACHE["data"]

def fetch_match_incidents(mid):
    url = f"https://www.flashscore.com.tr/x/feed/df_sui_1_{mid}"
    headers = {
        "X-Fsign": "SW9D1eZo",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.flashscore.com.tr/"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=4) as res:
        raw = res.read().decode("utf-8")

    goals = []
    cards = []
    events = raw.split("~III÷")
    for ev in events[1:]:
        items = [x.split("÷", 1) for x in ev.split("¬") if "÷" in x]
        is_goal = any(k == "IK" and ("Gol" in v or "kalesine" in v or "Penalt" in v) for k, v in items)
        is_card = any(k == "IK" and ("Kart" in v or "Kırmızı" in v) for k, v in items)

        minute_raw = next((v for k, v in items if k == "IB"), "").replace("'", "").strip()
        side_raw = next((v for k, v in items if k == "IA"), "")
        side = "A" if side_raw == "1" else ("B" if side_raw == "2" else "")

        extra_min = None
        min_val = None
        if minute_raw:
            try:
                if "+" in minute_raw:
                    parts = minute_raw.split("+")
                    min_val = int(parts[0])
                    extra_min = int(parts[1])
                else:
                    min_val = int(minute_raw)
            except Exception:
                pass

        if is_goal:
            inc_val = next((v for k, v in items if k == "IK" and ("Gol" in v or "kalesine" in v or "Penalt" in v)), "")
            g_type = "G"
            if "Penalt" in inc_val:
                g_type = "PG"
            elif "kalesine" in inc_val:
                g_type = "OG"

            sc_a = next((v for k, v in items if k == "INX"), None)
            sc_b = next((v for k, v in items if k == "IOX"), None)

            # İlk IF etiketi golcüdür
            player_tags = [v for k, v in items if k == "IF"]
            scorer = player_tags[0].strip() if player_tags else ""
            assist = player_tags[1].strip() if len(player_tags) > 1 else ""

            if scorer:
                goals.append({
                    "type": g_type,
                    "minute": min_val,
                    "extra_min": extra_min,
                    "team": side,
                    "scorer": scorer,
                    "assist": assist,
                    "score_A": int(sc_a) if sc_a is not None and str(sc_a).isdigit() else None,
                    "score_B": int(sc_b) if sc_b is not None and str(sc_b).isdigit() else None
                })
        elif is_card:
            inc_val = next((v for k, v in items if k == "IK" and ("Kart" in v or "Kırmızı" in v)), "")
            is_red = ("Kırmızı" in inc_val or "İkinci" in inc_val or "red" in inc_val.lower())
            if is_red:
                player_tags = [v for k, v in items if k == "IF"]
                player = player_tags[0].strip() if player_tags else ""
                c_type = "Y2C" if ("İkinci" in inc_val or "y2c" in inc_val.lower()) else "RC"
                cards.append({
                    "type": c_type,
                    "team": side,
                    "player": player,
                    "minute": min_val,
                    "extra_min": extra_min
                })

    return goals, cards

def find_match_id(home, away):
    if not home or not away:
        return None
    matches = get_flashscore_matches(force_refresh=False)
    hn = normalize_name(home)
    an = normalize_name(away)
    key = f"{hn}___{an}"
    if key in matches:
        return matches[key]["id"]

    # Kısmi eşleşme (örn: "İspanya U21" vs "İspanya")
    for mk, mv in matches.items():
        m_h, m_a = mk.split("___")
        if (hn in m_h or m_h in hn) and (an in m_a or m_a in an):
            return mv["id"]

    # Bulunamadıysa taze feed çekip bir daha dene
    matches = get_flashscore_matches(force_refresh=True)
    if key in matches:
        return matches[key]["id"]
    for mk, mv in matches.items():
        m_h, m_a = mk.split("___")
        if (hn in m_h or m_h in hn) and (an in m_a or m_a in an):
            return mv["id"]

    return None

def get_fast_goals(home, away, min_goals=0):
    """
    Belirtilen maç için Flashscore'dan ultra-hızlı golleri çeker.
    Eğer maç bulunamazsa veya gol sayısı yetersizse [] döner (ana akışa bırakır).
    """
    try:
        mid = find_match_id(home, away)
        if not mid:
            return []
        goals, cards = fetch_match_incidents(mid)
        if goals and (min_goals <= 0 or len(goals) >= min_goals):
            has_empty = any(not g.get("scorer") for g in goals)
            if not has_empty:
                return goals
    except Exception:
        pass
    return []

def get_fast_cards(home, away):
    """
    Belirtilen maç için Flashscore'dan ultra-hızlı kırmızı kart olaylarını çeker.
    """
    try:
        mid = find_match_id(home, away)
        if not mid:
            return []
        _, cards = fetch_match_incidents(mid)
        return cards or []
    except Exception:
        pass
    return []

