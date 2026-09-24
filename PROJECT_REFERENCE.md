# FootFlow — Geliştirici Referans Dökümanı

> Bu döküman tek referans noktasıdır. Yeni özellik eklemeden, hata ayıklamadan veya değişiklik yapmadan önce oku.
> Son güncelleme: 2026-09-24

---

## Hızlı Erişim

| Konu | Bak |
|---|---|
| Canlı URL'ler, servis durumu | [PROJECT_STATE.md](./PROJECT_STATE.md) |
| Mimari, dosya rolleri, thread yapısı | [PROJECT_ARCHITECTURE.md](./PROJECT_ARCHITECTURE.md) |
| Yeni lig/kupa ekleme | [PROJECT_ARCHITECTURE.md → Yeni Özellik](#) |
| Hata ayıklama | [PROJECT_ARCHITECTURE.md → Hata Ayıklama](#) |
| Potansiyel sorunlar ve risk analizi | [proje_analizi.md](../../.gemini/antigravity/brain/c9defe80-42e0-406e-bea7-314f3cbb07a5/proje_analizi.md) |

---

## Kritik Kurallar

### 1. index.html Düzenleme Kuralları
- **Standart değişiklikler** (UI, JS logic): `index.html`'i doğrudan düzenle. `build_desktop.py` çalıştırılmadan bu değişiklikler korunur.
- **Lig/kupa verisi değişikliği**: `build_desktop.py` içindeki şablon fonksiyonlarını düzenle; betik yalnızca `window.INITIAL_ALL_LEAGUES` bloğunu yeniden yazar.

### 2. vapid_keys.json'u ASLA SİLME/DEĞİŞTİRME
VAPID anahtarları değişirse tüm mevcut push abonelikleri geçersiz kalır.
Dosya git'te tracking edilmemeli bile olsa yedekle.

### 3. subscriptions.json & all_goals_cache.json Render Ephemeral Diskinde
Push abonelik kayıtları ve sunucu taraflı golcü önbelleği Render'ın ephemeral dosya sisteminde durur.
Render servisi yeniden deploy edildiğinde veya yeniden başlatıldığında sıfırlanabilir.

### 4. Render Free Plan: 1 Servis Kuralı
Render Free planında 750 saat/ay hakkı var. 2 servis eş zamanlı çalışırsa aylık hak bitebilir.
Sadece `footflow-6550` aktif olmalı.

---

## Sistem Mimarisi (Özet)

```
[Kullanıcı Tarayıcı]
      |
      | HTTPS (GitHub Pages)
      v
[index.html — 3.2 MB Monolitik PWA]
      |
      | Başlangıçta: window.INITIAL_ALL_LEAGUES (enjekte edilmiş veri)
      | Runtime: fetch() ile API çağrıları
      |
      | HTTPS (Render)
      v
[push_server.py — Python TCP Server]
      |
      +-- GET /api/vapid-key         → VAPID public key
      +-- GET /api/subscriptions     → Abone sayısı (UptimeRobot pinger)
      +-- GET /api/diagnose          → Sunucu durum raporu
      +-- GET /api/match-goals       → Gol olayları
      +-- GET /api/match-red-cards   → Kırmızı kartlar
      +-- GET /api/match-lineup      → Kadro/diziliş
      +-- GET /api/live-stream-player → TV player
      +-- GET /api/live-summary      → Tüm canlı maç özeti
      +-- POST /api/subscribe        → Push abonelik kaydet
      +-- POST /api/test-push        → Test bildirimi gönder
      |
      +-- [Thread 1] sahadan_http_sync_worker (her 30s full, her 3s delta)
      |     Sahadan API → canlı maç verisi → gol push + dinamik golcü fetch
      +-- [Thread 2] start_socket_listener
      |     Mackolik WebSocket → gerçek zamanlı olaylar
      +-- [Thread 3] keep_alive_ping (her 9 dk)
      |     Kendi URL'ine ping → Render uykuya dalmasın
      +-- [Thread 4] red_card_monitor_worker (her 12 sn)
      |     Tüm canlı maçlarda kırmızı kart derin sync — Sahadan match-detail JSON API
      +-- [Dinamik Thread] _bg_fetch_goals (Semaphore: max 2 eş zamanlı)
            Gol algılanınca 10s bekleme + 5s aralıkla golcüleri Sahadan'dan çeker,
            all_goals_cache.json'a yazar. Sadece 26 lig/kupa maçları taranır.
```

---

## Build → Deploy Akışı

```
1. build_desktop.py çalıştır (python3 build_desktop.py)
   → leagues_cache.json güncellenir
   → index.html güncellenir (data enjekte)
   → dist/index.html güncellenir (GitHub Pages için)

2. git add -A && git commit -m "feat: ..."
3. git push origin main
   → GitHub Actions tetiklenir
   → GitHub Pages otomatik deploy (~1-2 dk)
   → Render otomatik deploy (push_server.py değiştiyse, ~2-3 dk)

Render Deploy Tetikleyicisi: push_server.py değişikliği
GitHub Pages Deploy: dist/index.html + index.html değişikliği
```

---

## Veri Enjeksiyon Mekanizması

```python
# build_desktop.py içinde:
injected_js = f"window.INITIAL_ALL_LEAGUES = {json.dumps(payload, ensure_ascii=False)};\n"
modified_html = re.sub(r'window\.INITIAL_ALL_LEAGUES\s*=\s*\{.*?\};\n', lambda _: injected_js, template)
```

`index.html` şablonunda boş bir `window.INITIAL_ALL_LEAGUES = {};` satırı vardır.
Build script bu satırı gerçek veriyle değiştirir.
Bu sayede statik HTML dosyası tüm veriyi taşır — backend olmadan da çalışabilir.

---

## Olay & Bildirim Akışı

### 1. Gol Olayı & Otomatik Golcü Önbellekleme
1. `sahadan_http_sync_worker` veya WebSocket skor artışı tespit eder.
2. Favorilenen maçlar için anında WebPush bildirimi gönderilir (`send_push_for_match()`).
3. Maç `KNOWN_MATCH_IDS` (26 lig/kupa) içindeyse `_bg_fetch_goals` thread'i başlatılır:
   - `_GOALS_BG_SEM` (max 2 eş zamanlı istek) ile korunur.
   - 10 sn bekler (Sahadan'ın işlemesi için), ardından 5 sn arayla max 10 deneme yapar.
   - Golcüler alınınca `all_goals_cache.json` dosyasına yazılır.
   - Uygulama kapalıyken atılan gollerin bilgisi kullanıcı açtığında hazır gelir.

### 2. Kırmızı Kart Bildirimi
1. `red_card_monitor_worker` her 12 saniyede tüm canlı maçları tarar (eski: 3 dk, sadece favoriler).
2. Sahadan `match-detail` JSON API'sinden kırmızı kartlar parse edilir (önce JSON, fallback HTML scraping).
3. Yeni kart tespit edilirse `send_push_for_match()` ile bildirim gider.
4. `live_matches_state[uuid]` güncellenerek skor derin sync yapılır.

### 3. Gol İptali (VAR) & Jitter Koruması
1. **Jitter Koruması:** Son 90 saniye içinde gol olmuşsa veya polling eski skor getiriyorsa skor düşüşü engellenir.
2. **2s Ertelenmiş Onay:** Soketten ardışık düşüş gelirse 2 saniye beklenir; skor hâlâ düşükse iptal sesi (`playCancelSound`) ve görsel kırmızı flash tetiklenir.
3. **Maç Bitiş Koruması:** Maç `Played` durumuna geçtiğinde bekleyen iptal timer'ı derhal silinir (maç sonu bayat paketlerin yanlış iptal sesi çalması önlenir).
4. **90s Karantina:** İptal edilen skor 90 saniye cooldown'a alınır, tekrar bayat paket gelirse çift ses/bildirim çalmaz.

---

## Sahadan API Yapısı

```
Canlı maçlar:
GET https://www.sahadan.com/api/index/soccer-live-e?a=bs&e=sams&add_playing=1&extended_period=1&date=YYYY-MM-DD

Delta canlı olaylar:
GET https://www.sahadan.com/api/index/soccer-sync-data?a=bs&e=sces&u={timestamp}

Maç detayı — JSON API (kırmızı kart + gol derin sync için):
GET https://www.sahadan.com/api/index/match-detail?match_uuid={uuid}
→ JSON yanıt: kart olayları, gol olayları, maç durumu
→ Güvenilirlik: Yüksek, yanıt süresi ~0.15s
→ Kullanım: red_card_monitor_worker (her 12sn), fetch_match_red_cards()

Golcü parse (HTML fallback):
GET https://www.sahadan.com/mac/{home-slug}-vs-{away-slug}/{uuid}
→ HTML içerisindeki <script id="__NUXT_DATA__"> JSON verisi parse edilir.
→ match-detail JSON başarısız olursa fallback olarak kullanılır.
```

---

## Özellik Etki Haritası

| Değiştirmek İstediğin | Etkilenen Dosyalar | Dikkat Edilecek |
|---|---|---|
| Yeni lig ekle | `build_desktop.py` (LEAGUES), `leagues_cache.json` | Build sonrası index.html de güncellenir |
| Yeni kupa ekle | `build_desktop.py` (LEAGUES + type:"cup"), `leagues_cache.json` | Fikstür URL formatı farklı olabilir |
| Fikstürsüz turnuva ekle (Uluslar Ligi gibi) | `push_server.py` (STANDALONE_LIVE_COMPETITIONS, is_goal_tracking_enabled), `index.html` (ensureMatchInLiveList filtresi) | build_desktop.py çalıştırmak gerekmez; maçlar live feed'den gelir |
| Bildirim başlığı/içeriği | `push_server.py` (process_match_update) | sw.js'de tag ile özel davranış tanımlanabilir |
| Yeni API endpoint | `push_server.py` (do_GET/do_POST) | CORS otomatik, başka bir şey gerekmez |
| Frontend UI değişikliği | `index.html` (doğrudan düzenle) | build_desktop.py çalıştırmadan değişiklik korunur |
| Push sunucu URL'i | `index.html` L3831, L5376-5380, L5648 | push_server.py L1513 de güncelle |
| Cache versiyonu | `sw.js` L1 | Kullanıcıların tarayıcısı eski cache'i temizler |
| PWA adı/ikonu | `manifest.json` | `icons/` klasöründe dosyalar olmalı |

---

## Bilinen Limitler & Teknik Borç

| Konu | Detay | Çözüm/Geçici Çözüm |
|---|---|---|
| Monolitik index.html | 3.2 MB tek dosya, bundle yok | Build script ile yönetiliyor, kabul edilebilir |
| Ephemeral subscriptions & cache | Render restart/deploy olunca JSON dosyaları sıfırlanır | Deploy öncesi subscriptions.json yerel yedek al |
| Tek point of failure | Render free plan servisi çökerse her şey durur | Keep-alive + UptimeRobot |
| Rate limit | Sahadan hızlı isteklerde 429 verir | 30s poll, browser headers, Semaphore(2) ile kısıtlama |
| Socket bağlantı kopması | WebSocket bağlantısı kopabilir | Otomatik yeniden bağlanma var (~30s) |
| 07:00 abone reset | Her gün 07:00'de favoriler temizleniyor | Tasarım gereği (güne özel favoriler) |
| Kupa yeni tur eşleşmeleri | Fikstür belli olmadan leagues_cache boş olabilir | Canlı akışta competition title ile dinamik KNOWN_MATCH_IDS'e eklenir |
| FA Cup hardcoded tarih | `push_server.py`'de `2026-11-15` öncesi FA Cup maçları filtreleniyor | 2027 yazında güncellenmesi gerekiyor, unutma riski var |
| red_card_monitor rate limit | Her 12sn tüm canlı maçlar için match-detail çağrısı — Semaphore koruması yok | Maç yoğunluğu çok artarsa 429 riski; izle |
