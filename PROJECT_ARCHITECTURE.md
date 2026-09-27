# FootFlow — Mimari Döküman (Güncel)

> Son güncelleme: 2026-09-28

## Sistemin Genel Yapısı

FootFlow üç katmandan oluşur:

1. **Frontend (GitHub Pages + Oracle Nginx):** `index.html` tek sayfalık PWA uygulaması
   - Ana erişim: `https://footflow.site/` (Cloudflare → Oracle Nginx → index.html)
   - Statik yedek: `https://oktemonur7.github.io/FootFlow/` (sadece frontend, API yok)
2. **CDN & Güvenlik (Cloudflare):** DNS proxy, WAF, DDoS koruması, SSL termination
3. **Backend (Oracle Cloud Always Free VM):** `push_server.py` Python HTTP sunucusu (port 8080, Nginx arkasında)

```
Kullanıcı
    │
    ▼ HTTPS
Cloudflare Edge (CDN, WAF, DDoS, SSL)
    │
    ▼ Proxied (Cloudflare IP'si görünür, gerçek IP gizli)
Oracle VM 92.5.35.155 — Nginx (80→301, 443 SSL)
    │
    ├── /              → /home/ubuntu/footflow/index.html (3.2 MB PWA)
    ├── /api/*         → 127.0.0.1:8080 (push_server.py)
    ├── /health        → 127.0.0.1:8080/health
    └── /sw.js         → no-cache header (PWA güncellemeleri anında)

Yedek Erişim:
    ▼ HTTPS (Direkt)
footflow.duckdns.org → Aynı Oracle Nginx (Let's Encrypt SSL ile)
```

---

## Dosya Rolleri

| Dosya | Kategori | Açıklama |
|---|---|---|
| `build_desktop.py` | Build Script | Sahadan.com'u kazır, `leagues_cache.json`'u günceller, `index.html`'e data enjekte eder. Yerel + GitHub Actions tarafından çalıştırılır. |
| `index.html` | Frontend / PWA | 3.2 MB monolitik frontend. Tüm CSS, JS, HTML tek dosyada. Build script tarafından üretilir. **Doğrudan düzenleme — build sonrası ezilir.** |
| `push_server.py` | Backend / Oracle | ~167 KB Python HTTP sunucusu. systemd servisi olarak 7/24 çalışır. WebPush, gol izleme, golcü cache, kırmızı kart monitörü, live-sync. |
| `sw.js` | PWA | Service Worker `footflow-v88`. Network-first (2.5s timeout) + WebPush bildirim yakalama. |
| `manifest.json` | PWA | PWA manifest: name="FootFlow", ikon yolları, display=standalone, theme-color=#00ff85. |
| `leagues_cache.json` | Cache | ~2.8 MB. 27 lig/kupa verisi. Build script güncelliyor; push_server.py maç fikstürü ve KNOWN_MATCH_IDS için okuyor. |
| `vapid_keys.json` | Güvenlik | VAPID özel/genel anahtar çifti. Push bildirimleri için zorunlu. **Asla silme, git'e commit etme.** |
| `subscriptions.json` | Runtime | Push abonelik kayıtları. Oracle Cloud VM kalıcı diskinde durur (deploy'dan etkilenmez). |
| `all_goals_cache.json` | Cache | Maç gol olayları kalıcı cache. Sunucu gol algıladığında otomatik yazılır. |
| `all_cards_cache.json` | Cache | Kırmızı kart bilgileri cache. |
| `all_tv_cache.json` | Cache | TV yayın bilgileri cache. |
| `requirements.txt` | Bağımlılık | pywebpush, python-socketio, websocket-client, requests, cryptography |
| `server.py` | Yerel | Alternatif yerel HTTP sunucusu (geliştirme için). |
| `socket.io.v2.slim.js` | Library | Socket.IO v2 istemci kütüphanesi (frontend'e gömülü). |
| `.github/workflows/build.yml` | CI/CD | Her push + günde 4x sahadan verilerini çeker, GitHub Pages'e deploy eder. |
| `.github/workflows/deploy.yml` | CI/CD | Her push'ta Oracle VM'ye SSH ile bağlanır, git pull + footflow servisini yeniden başlatır. |

---

## push_server.py — Startup Globals

```
Sunucu başlarken yüklenenler:

MATCH_GOALS_CACHE      → all_goals_cache.json'dan önceki golcüler (232+ maç)
MATCH_CARDS_CACHE      → all_cards_cache.json'dan kırmızı kart bilgileri
KNOWN_MATCH_IDS        → leagues_cache.json'dan 12.000+ maç UUID/ID seti
                          (golcü fetch filtresinde kullanılır)
KNOWN_COMPETITION_TITLES → leagues_cache.json'dan 22 competition title
                          (FA Cup gibi kupalar için dinamik match ID ekleme)
_GOALS_BG_SEM          → threading.Semaphore(2) — eş zamanlı max 2 Sahadan scrape
```

## push_server.py — Thread Mimarisi

```
push_server.py başlarken 4 daemon thread çalıştırır:

[Main Thread] HTTP Server (port 8080, Nginx arkasında)
    ├─ GET  /api/vapid-key         → VAPID public key döner
    ├─ GET  /api/subscriptions     → Aktif abone sayısını döner
    ├─ GET  /api/diagnose          → Sunucu durum raporu
    ├─ GET  /api/match-goals       → Maç gol listesi (uuid gerekli)
    ├─ GET  /api/match-red-cards   → Kırmızı kart listesi (uuid gerekli)
    ├─ GET  /api/match-lineup      → Kadro/diziliş verisi (uuid gerekli)
    ├─ GET  /api/live-stream-player → TV canlı yayın player bilgisi
    ├─ GET  /api/live-summary      → Anlık tüm maçların özeti
    ├─ GET  /api/live-sync         → Frontend polling endpoint'i
    ├─ GET  /health                → {"status":"ok"} sağlık kontrolü
    ├─ POST /api/subscribe         → Push aboneliği kaydet/güncelle
    └─ POST /api/test-push         → Test bildirimi gönder

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
    → Kopunca ~30s içinde otomatik yeniden bağlanır

[Thread 3] keep_alive_ping()
    → Oracle VM'de systemd 7/24 çalıştırdığı için uyuma sorunu yoktur
    → Periyodik sağlık kontrolü yapar

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

1. LEAGUES listesinden 27 lig/kupa URL'si alınır
2. Her lig için sahadan.com/lig/.../fikstur sayfası HTTP ile çekilir
3. JSON yanıttan maç, hafta, takım bilgileri ayrıştırılır
4. leagues_cache.json güncellenir (~2.8 MB)
5. fetch_live_scores_today() → günün canlı skorları çekilir
6. fetch_iddaa_odds() → iddaa.com API'den oranlar çekilir
7. fetch_tv_broadcasts() → TV yayın bilgileri çekilir
8. Tüm data window.INITIAL_ALL_LEAGUES JS objesi olarak derlenir
9. index.html şablonuna enjekte edilir (regex replace)
10. Çıktı dosyaları: index.html, dist/index.html, futbol_ligleri.html, premier_lig.html

Çalıştırma: python3 build_desktop.py
Süresi: ~2-5 dk (network hızına göre)
GitHub Actions: Her push + günde 4x (09:00, 13:00, 16:00, 19:00 TSİ) otomatik çalışır
```

---

## Frontend (index.html) — Kritik Fonksiyonlar

| Fonksiyon | Satır Aralığı | Açıklama |
|---|---|---|
| `getGoalsApiBaseUrl()` | ~L4709 | API base URL döner: `https://footflow.site` (Fallback: `https://footflow.duckdns.org`) |
| `fetchApiWithFallback()` | ~L4714 | Primary URL başarısızsa fallback'e düşer |
| `getPushServerUrl()` | ~L6964 | Push subscribe URL. localStorage > hardcoded `https://footflow.site` |
| `localStorage fallback` | L6966 | footflow_push_server → footfollow_push_server → iddaatakip_push_server (geriye dönük uyum) |
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
| Sahadan WebSocket (Socket.IO v2) | Gerçek zamanlı skor | DÜŞÜK — tek kalıcı bağlantı |

---

## PWA & Bildirim Sistemi

```
Kullanıcı Akışı:
1. Kullanıcı https://footflow.site/ açar (veya GitHub Pages URL'ini)
2. sw.js yüklenir, "footflow-v88" cache oluşturulur
3. Kullanıcı bildirim izni verir
4. Frontend /api/vapid-key endpoint'inden VAPID public key alır
5. Browser push subscription oluşturur (endpoint + keys)
6. Subscription /api/subscribe ile Oracle VM'deki push_server.py'a kaydedilir
7. subscriptions.json'a kalıcı olarak yazılır (Oracle disk = kalıcı)

Bildirim Tetikleyicileri:
- Gol atıldı → favorilenen maçlar için anlık bildirim
- Kırmızı kart → her 3 dakikada kontrol, favori maçlar
- Yarı sonu / Maç sonu → opsiyonel (kullanıcı ayarına göre)
- Test bildirimi → /api/test-push endpoint'i

Bildirim Tercihleri (6 opsiyonel toggle):
- goal, scorer, cancel (gol/golcü/iptal)
- red_card, half_time, match_end

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
| `index.html` L6966 | `iddaatakip_push_server` localStorage fallback | Yok | Bırak (geriye dönük uyum) |
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
2. Sahadan URL'ini bul (format: `https://www.sahadan.com/lig/[slug]/[id]`)
3. Kupa ise: `"type": "cup"` ekle, `"min_date"` eklenebilir
4. `python3 build_desktop.py` çalıştır
5. `leagues_cache.json` ve `index.html` otomatik güncellenir
6. Commit et ve push yap → GitHub Actions otomatik deploy eder

### Yeni API Endpoint Eklemek (Backend)
1. `push_server.py` → `RequestHandler.do_GET()` veya `do_POST()` içine yeni `if self.path == "/api/..."` bloğu ekle
2. CORS başlığı `end_headers()` tarafından otomatik ekleniyor
3. Git push → GitHub Actions Oracle VM'ye deploy eder (~1-2 dk)

### Yeni Bildirim Türü Eklemek
1. `push_server.py` → `process_match_update()` içinde yeni koşul ekle
2. Payload formatı: `{"title": "...", "body": "...", "tag": "footflow-...", "data": {...}}`
3. `sw.js` → `push` event listener'da `event.data.json()` parse eder

### Frontend Değişikliği
> ⚠️ `index.html` doğrudan değiştirme! Build sonrası ezilir.
1. `build_desktop.py` içindeki şablon fonksiyonlarını düzenle
2. Statik içerik: `build_desktop_html()` fonksiyonu içinde
3. Canlı data: `window.INITIAL_ALL_LEAGUES` enjeksiyonu
4. `python3 build_desktop.py` çalıştır, test et, commit et

---

## Hata Ayıklama Rehberi

### "Bildirim gelmiyor"
1. Sunucu ayakta mı? → `https://footflow.site/health` veya `https://footflow.duckdns.org/health`
2. Abone kayıtlı mı? → `https://footflow.site/api/subscriptions` (abone sayısını döner)
3. VAPID key değişti mi? → `vapid_keys.json` kontrol et
4. Test bildirimi gönder: `POST /api/test-push`
5. Browser DevTools → Application → Service Workers → Push test

### "Sahadan verileri güncellenmiyor"
1. Nginx loglarında 429 hatası var mı? → Rate limit, birkaç dk bekle
2. Socket bağlantısı kesildi mi? → `start_socket_listener()` yeniden bağlanır, bekleme süresi ~30s
3. Oracle VM'e SSH: `sudo journalctl -u footflow -f`

### "Build script çalışmıyor"
1. `pip install -r requirements.txt` dene
2. Sahadan erişilebilir mi? → `curl https://www.sahadan.com` dene
3. 429 hatası → farklı saatte dene

### "GitHub Pages güncellenmedi"
1. GitHub Actions çalıştı mı? → Repo → Actions sekmesi
2. `dist/index.html` değişti mi? → Build sonrası her zaman commit edilmeli
3. Branch: main olmalı

### "Oracle VM'e erişilemiyor"
```bash
ssh -i /Users/onur/Downloads/ssh-key-2026-09-01.key ubuntu@92.5.35.155
sudo systemctl status footflow nginx
sudo journalctl -u footflow --since "1 hour ago"
sudo nginx -t && sudo systemctl reload nginx
```

---

## Ortam Değişkenleri (Oracle VM)

| Değişken | Nerede Set | Açıklama |
|---|---|---|
| `PORT` | systemd ortam dosyası | HTTP sunucu portu (default 8080) |
| `SERVER_EXTERNAL_URL` | Opsiyonel | Keep-alive ping URL'i (default: `https://footflow.site`) |

> Oracle VM'de elle set edilmesi gereken bir env var yok. Tüm değerler ya systemd tarafından yönetilir ya da kodda hardcoded fallback vardır.

## CI/CD Akışı

```
git push origin main
    │
    ├── GitHub Actions: build.yml
    │   ├── python3 build_desktop.py (sahadan.com'dan veri çeker)
    │   └── peaceiris/actions-gh-pages → dist/ → gh-pages branch
    │                                              → https://oktemonur7.github.io/FootFlow/
    │
    └── GitHub Actions: deploy.yml
        └── appleboy/ssh-action → Oracle VM
            ├── git checkout -- all_goals_cache.json all_cards_cache.json
            ├── git pull origin main
            └── sudo systemctl restart footflow

Zamanlanmış build: Günde 4x — 09:00, 13:00, 16:00, 19:00 TSİ (06:00, 10:00, 13:00, 16:00 UTC)
```
