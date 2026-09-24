# FootFlow — Mimari Döküman (Güncel)

> Son güncelleme: 2026-09-13

## Sistemin Genel Yapısı

FootFlow iki ana katmandan oluşur:

1. **Frontend (GitHub Pages):** `index.html` tek sayfalık PWA uygulaması → `https://oktemonur7.github.io/FootFlow/`
2. **Backend (Render):** `push_server.py` Python HTTP sunucusu → `https://footflow-6550.onrender.com`

---

## Dosya Rolleri

| Dosya | Kategori | Açıklama |
|---|---|---|
| `build_desktop.py` | Build Script | Sahadan.com'u kazır, `leagues_cache.json`'u günceller, `index.html`'e data enjekte eder. Manuel/yerel çalıştırılır. |
| `index.html` | Frontend / PWA | 3.2 MB monolitik frontend. Tüm CSS, JS, HTML tek dosyada. Build script tarafından üretilir. |
| `push_server.py` | Backend / Render | 72 KB+ Python TCP sunucusu. 4 thread yönetir. WebPush, gol izleme, golcü cache, kırmızı kart monitörü, keep-alive. |
| `sw.js` | PWA | Service Worker `footflow-v50`. Network-first (2.5s timeout) strateji + WebPush bildirim yakalama. |
| `manifest.json` | PWA | PWA manifest: name="FootFlow", ikon yolları, display=standalone, theme-color=#00ff85. |
| `leagues_cache.json` | Cache | 2.8 MB. 26 lig/kupa verisi. Build script güncelliyor, push_server.py hem maç fikstürü hem KNOWN_MATCH_IDS için okuyor. |
| `vapid_keys.json` | Güvenlik | VAPID özel/genel anahtar çifti. Push bildirimleri için zorunlu. GIT'e commit edilmemeli. |
| `subscriptions.json` | Runtime | Push abonelik kayıtları. Render dosya sisteminde dinamik yazılır. Silinirse aboneler kaybolur. |
| `all_goals_cache.json` | Cache | Maç gol olayları kalıcı cache. Sunucu gol algıladığında otomatik yazılır. Ephemeral — deploy'da sıfırlanır. |
| `all_tv_cache.json` | Cache | TV yayın bilgileri cache. |
| `requirements.txt` | Bağımlılık | pywebpush, python-socketio, websocket-client, requests, cryptography |
| `server.py` | Yerel | Alternatif yerel HTTP sunucusu. Render'da kullanılmıyor. |
| `socket.io.v2.slim.js` | Library | Socket.IO v2 istemci kütüphanesi (gömülü). |

---

## push_server.py — Startup Globals

```
Sunucu başlarken yüklenenler:

MATCH_GOALS_CACHE      → all_goals_cache.json'dan önceki golcüler
KNOWN_MATCH_IDS        → leagues_cache.json'dan 6031+ maç UUID/ID seti
                          (golcü fetch filtresinde kullanılır)
KNOWN_COMPETITION_TITLES → leagues_cache.json'dan 22 competition title
                          (FA Cup gibi kupalar için dinamik match ID ekleme)
_GOALS_BG_SEM          → threading.Semaphore(2) — eş zamanlı max 2 Sahadan scrape
```

## push_server.py — Thread Mimarisi

```
push_server.py başlarken 4 daemon thread çalıştırır:

[Main Thread] HTTP Server (port 8080)
    ├─ GET /api/vapid-key         → VAPID public key döner
    ├─ GET /api/subscriptions     → Aktif abone sayısını döner (UptimeRobot bunu çağırır)
    ├─ GET /api/diagnose          → Sunucu durum raporu
    ├─ GET /api/match-goals       → Maç gol listesi (uuid gerekli)
    ├─ GET /api/match-red-cards   → Kırmızı kart listesi (uuid gerekli)
    ├─ GET /api/match-lineup      → Kadro/diziliş verisi (uuid gerekli)
    ├─ GET /api/live-stream-player → TV canlı yayın player bilgisi
    ├─ GET /api/live-summary      → Anlık tüm maçların özeti
    ├─ POST /api/subscribe        → Push aboneliği kaydet/güncelle
    └─ POST /api/test-push        → Test bildirimi gönder

[Thread 1] sahadan_http_sync_worker()
    → Her 30 saniyede sahadan API'yi çeker (soccer-live-e, tüm dünya)
    → Her 3 saniyede delta sync (soccer-sync-data)
    → Gol/skor değişikliklerinde push bildirimi gönderir
    → Gol algılanınca KNOWN_MATCH_IDS kontrolü → _bg_fetch_goals thread başlatır
    → Competition title eşleşmesinde yeni kupa maçlarını KNOWN_MATCH_IDS'e ekler
    → Her sabah 07:00'de abone favorilerini sıfırlar

[Thread 2] start_socket_listener()
    → Sahadan WebSocket (Socket.IO v2) bağlantısı
    → Gerçek zamanlı skor olaylarını yakalar
    → Kritik olaylarda push bildirimi tetikler

[Thread 3] keep_alive_ping()
    → 60s bekler (sunucu tam açılsın diye)
    → Sonra her 540s (9 dk) kendi URL'ine ping atar
    → Render'ın servisi uyutmasını önler
    → URL: RENDER_EXTERNAL_URL env > hardcoded footflow-6550.onrender.com

[Thread 4] red_card_monitor_worker()
    → 20s bekler (başlangıç)
    → Her 180s (3 dk) çalışır, 5s stagger
    → Favorilenen maçlarda kırmızı kart kontrolü yapar
    → Kırmızı kart bulursa push bildirimi gönderir

[Dinamik — Gol Başına] _bg_fetch_goals(h, a, uuid, expected)
    → Gol algılanınca spawn edilir (sadece KNOWN_MATCH_IDS içindeki maçlar)
    → _GOALS_BG_SEM ile eş zamanlı max 2 aktif scrape
    → 10s bekler (Sahadan'ın golü işlemesi için), sonra 5s aralıklarla max 10 deneme
    → Golcüler tam gelince all_goals_cache.json'a yazar
    → Uygulama kapalı kullanıcılar açtığında golcüler hazır gelir
```

---

## build_desktop.py — Veri Akışı

```
build_desktop.py çalıştırıldığında:

1. LEAGUES listesinden 26 lig/kupa URL'si alınır
2. Her lig için sahadan.com/lig/.../fikstur sayfası HTTP ile çekilir
3. JSON yanıttan maç, hafta, takım bilgileri ayrıştırılır
4. leagues_cache.json güncellenir (2.8 MB)
5. fetch_live_scores_today() → günün canlı skorları çekilir
6. fetch_iddaa_odds() → iddaa.com API'den oranlar çekilir
7. fetch_tv_broadcasts() → TV yayın bilgileri çekilir
8. Tüm data window.INITIAL_ALL_LEAGUES JS objesi olarak derlenir
9. index.html şablonuna enjekte edilir (regex replace)
10. Çıktı dosyaları: index.html, dist/index.html, futbol_ligleri.html, premier_lig.html

Çalıştırma: python3 build_desktop.py
Süresi: ~2-5 dk (network hızına göre)
```

---

## Frontend (index.html) — Kritik Fonksiyonlar

| Fonksiyon | Satır Aralığı | Açıklama |
|---|---|---|
| `getGoalsApiBaseUrl()` | ~L3831 | Push sunucu URL'i döner. Env > hardcoded footflow-6550.onrender.com |
| `getPushServerUrl()` | ~L5376 | Push subscribe URL. localStorage > hardcoded |
| `localStorage fallback` | L5377 | footflow_push_server → footfollow_push_server → iddaatakip_push_server (geriye dönük uyum) |
| `loadScoreGoalTooltip()` | ~L3866 | Skora hover'da golcüleri yükler. 3 kademeli cache: memory → liveScoresList → API fetch |
| `formatGoalsHtml()` | ~L3749 | Golcü tooltip HTML'ini render eder. Canlı izle + kadro butonları dahil |
| `scheduleGoalRetry()` | ~L3612 | Gol algılanınca 3s ilk, sonra 3.5s aralıklarla max 18 deneme. Golcüler tamamlanınca durdurur |
| `populateGoalsClientCacheFromData()` | ~L3679 | Sayfa açılışında localStorage + liveScoresList + INITIAL_ALL_LEAGUES'den golcüleri yükler |
| `playCancelSound()` | ~L2790 | Gol iptali sesi. Sadece maç devam ediyorken ve 90s jitter koruması geçince tetiklenir |
| `initApp()` | — | PWA başlatma. readyState kontrollü. |
| `INITIAL_ALL_LEAGUES` | — | Build script tarafından enjekte edilen global JS objesi |

## Frontend — Gol İptali (VAR) Mekanizması

```
Skor düştüğünde (örn: 5-1 → 5-0):

1. Jitter Koruması:
   - Son 90 saniye içinde gol olduysa (m._lastGoalTime) → bayat paket, skor düşürülmez
   - Polling (HTTP) verisi hiçbir zaman socket'ten alınan yüksek skoru düşüremez

2. Ertelenmiş Onay (2s):
   - goalCancelled = true → m._pendingCancelScore kaydedilir
   - 2 saniye sonra timer ateşlenir, skor hâlâ düşük mü kontrol edilir
   - Hâlâ düşükse → gerçek VAR → playCancelSound() + kırmızı flash

3. Maç Bitti Koruması:
   - Maç Played'e geçince _pendingCancelTimer anında temizlenir
   - Maç son dakika golü + bayat paket = yanlış iptal sesi senaryosu engellenir
   (Leipzig 5-0→5-1 bug fix: 2026-09-13)

4. 90s Karantina (Cooldown):
   - İptal edilen skor 90 saniye boyunca m._cancelledScoresCooldown'a alınır
   - Bu skor tekrar gelirse gol bildirimi tetiklenmez
```

---

## Veri Kaynakları

| Kaynak | Ne için | Rate Limit Riski |
|---|---|---|
| `sahadan.com/api/index/soccer-live-e` | Canlı skor, maç durumu (tüm dünya) | YÜKSEK — 30s aralık ile çekiliyor |
| `sahadan.com/api/index/soccer-sync-data` | Delta güncellemeler | ORTA — 3s aralık, küçük payload |
| `sahadan.com/lig/.../fikstur` | Fikstür, puan durumu | ORTA — sadece build time |
| `sahadan.com/mac/[slug]/[uuid]` | Gol olayları, kırmızı kart, kadro | ORTA — Semaphore(2) ile korumalı, cache var |
| `iddaa.com` API | İddaa oranları | DÜŞÜK — sadece build time |
| Mackolik WebSocket | Gerçek zamanlı skor | DÜŞÜK — tek kalıcı bağlantı |

---

## PWA & Bildirim Sistemi

```
Kullanıcı Akışı:
1. Kullanıcı footflow GitHub Pages URL'ini açar
2. sw.js yüklenir, "footflow-v50" cache oluşturulur
3. Kullanıcı bildirim izni verir
4. Frontend /api/vapid-key endpoint'inden VAPID public key alır
5. Browser push subscription oluşturur (endpoint + keys)
6. Subscription /api/subscribe ile Render sunucusuna kaydedilir
7. Subscriptions.json'a yazılır

Bildirim Tetikleyicileri:
- Gol atıldı → favorilenen maçlar için anlık bildirim
- Kırmızı kart → her 3 dakikada kontrol, favori maçlar
- Test bildirimi → /api/test-push endpoint'i

Önemli Kısıt:
VAPID anahtarları değişirse tüm mevcut abonelikler geçersiz kalır.
vapid_keys.json'u asla silme/değiştirme.
```

---

## İsim Değişikliği Risk Analizi

### Mevcut Durum (Sonuç: DÜŞÜK RİSK)
Tüm kritik kod yolları güncellendi. Aşağıdakiler kasıtlı olarak bırakıldı:

| Konum | İçerik | Risk | Karar |
|---|---|---|---|
| `index.html` L5377 | `iddaatakip_push_server` localStorage fallback | Yok | Bırak (geriye dönük uyum) |
| `build_desktop.py` | `fetch_iddaa_odds()` fonksiyon adı | Yok | Bırak (fonksiyon tarif ediyor) |
| `index.html` meta | "iddaa oranları" metin | Yok | Bırak (fonksiyonel metin) |

### Gerçek Etkiler (Kalıcı, Çözümsüz)
1. **Eski push aboneleri:** `iddaatakip.onrender.com`'a kayıtlı abonelikler yeni sunucuda yok. Kullanıcıların yeniden kaydolması şart.
2. **PWA yüklü kullanıcılar:** `oktemonur7.github.io/iddaatakip/` veya `iddaatakip.onrender.com` bookmark'ları artık çalışmıyor. Yeni URL'i paylaşmak gerekiyor.
3. **GitHub redirect:** Eski `oktemonur7/iddaatakip` repo'su FootFlow'a redirect yapıyor, bu yardımcı oluyor.

---

## Yeni Özellik Ekleme Rehberi

### Yeni Lig/Kupa Eklemek
1. `build_desktop.py` → `LEAGUES` listesine yeni obje ekle
2. Sahadan URL'ini bul (format: `https://www.sahadan.com/lig/[slug]/[id]/fikstur`)
3. Kupa ise: `"type": "cup"` ekle, `"min_date"` eklenebilir
4. `python3 build_desktop.py` çalıştır
5. `leagues_cache.json` ve `index.html` otomatik güncellenir
6. Commit et ve push yap

### Yeni API Endpoint Eklemek (Backend)
1. `push_server.py` → `RequestHandler.do_GET()` veya `do_POST()` içine yeni `if self.path == "/api/..."` bloğu ekle
2. CORS başlığı `end_headers()` tarafından otomatik ekleniyor
3. Render otomatik deploy eder (push sonrası ~2-3 dk)

### Yeni Bildirim Türü Eklemek
1. `push_server.py` → `process_match_update()` içinde yeni koşul ekle
2. Payload formatı: `{"title": "...", "body": "...", "tag": "footflow-...", "data": {...}}`
3. `sw.js` → `push` event listener'da `event.data.json()` parse eder, özel `tag` ile farklı davranış tanımlayabilirsin

### Frontend Değişikliği
> ⚠️ `index.html` doğrudan değiştirme! Build sonrası ezilir.
1. `build_desktop.py` içindeki şablon fonksiyonlarını düzenle
2. Statik içerik: `build_desktop_html()` fonksiyonu içinde
3. Canlı data: `window.INITIAL_ALL_LEAGUES` enjeksiyonu
4. `python3 build_desktop.py` çalıştır, test et, commit et

---

## Hata Ayıklama Rehberi

### "Bildirim gelmiyor"
1. Render servisi ayakta mı? → `https://footflow-6550.onrender.com/api/subscriptions` aç
2. Abone kayıtlı mı? → Aynı endpoint abonelik sayısını döner
3. VAPID key değişti mi? → `vapid_keys.json` kontrol et
4. Test bildirimi gönder: `POST /api/test-push`
5. Browser DevTools → Application → Service Workers → Push test

### "Sahadan verileri güncellenmiyor"
1. Render loglarında 429 hatası var mı? → Rate limit, birkaç dk bekle
2. Socket bağlantısı kesildi mi? → `start_socket_listener()` yeniden bağlanır, bekleme süresi ~30s

### "Build script çalışmıyor"
1. `pip install -r requirements.txt` dene (sadece requests kullanıyor)
2. Sahadan erişilebilir mi? → `curl https://www.sahadan.com` dene
3. 429 hatası → farklı saatte dene

### "GitHub Pages güncellenmedi"
1. GitHub Actions çalıştı mı? → Repo → Actions sekmesi
2. `dist/index.html` değişti mi? → Build sonrası her zaman commit edilmeli
3. Branch: main olmalı

---

## Ortam Değişkenleri (Render)

| Değişken | Nerede Set | Açıklama |
|---|---|---|
| `PORT` | Render otomatik | HTTP sunucu portu (default 8080) |
| `RENDER_EXTERNAL_URL` | Render otomatik | Servisin dış URL'i. Keep-alive ping'de kullanılır |

> Render'da elle set edilmesi gereken bir env var yok. Tüm değerler ya Render tarafından otomatik set edilir ya da kodda hardcoded fallback vardır.
