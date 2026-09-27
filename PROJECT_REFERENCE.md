# FootFlow — Geliştirici Referans Dökümanı

> Bu döküman tek referans noktasıdır. Yeni özellik eklemeden, hata ayıklamadan veya değişiklik yapmadan önce oku.
> Son güncelleme: 2026-09-28

---

## Hızlı Erişim

| Konu | Bak |
|---|---|
| Canlı URL'ler, servis durumu | [PROJECT_STATE.md](./PROJECT_STATE.md) |
| Mimari, dosya rolleri, thread yapısı | [PROJECT_ARCHITECTURE.md](./PROJECT_ARCHITECTURE.md) |
| Yeni lig/kupa ekleme | [PROJECT_ARCHITECTURE.md → Yeni Özellik](#yeni-ligkupa-eklemek) |
| Hata ayıklama | [PROJECT_ARCHITECTURE.md → Hata Ayıklama](#hata-ayıklama-rehberi) |

---

## Kritik Kurallar

### 1. index.html'i DOĞRUDAN DÜZENLEME (Build Sonrası Ezilme Riski)
`index.html` build script çıktısıdır. `python3 build_desktop.py` çalıştırıldığında sıfırlanır.
Kalıcı frontend değişiklikleri için `build_desktop.py` içindeki şablon fonksiyonlarını düzenle.

### 2. vapid_keys.json'u ASLA SİLME/DEĞİŞTİRME
VAPID anahtarları değişirse tüm mevcut push abonelikleri geçersiz kalır.
Dosyayı git'te tracking etme ama güvenli bir yerde yedekle.

### 3. subscriptions.json Oracle VM Diskinde Kalıcı
Push abonelik kayıtları Oracle VM'nin kalıcı diskinde durur. Git pull onu ezmez.
`.gitignore`'a eklenmemiş olması sorun değil — GitHub Actions deploy.yml'de `all_goals_cache.json`
ve `all_cards_cache.json` git checkout ile sıfırlanmadan önce işleme alınır; subscriptions.json git'te
takip edilmediği için asla dokunulmaz.

### 4. Oracle Cloud Always Free Limitleri
- 1 OCPU (Intel/AMD paylaşımlı)
- 6 GB RAM (mevcut kullanım: ~768 MB — oldukça rahat)
- 45 GB disk (kullanım: %18 — bolca boş)
- Aylık egress: 10 TB (neredeyse hiç harcanmıyor)
- Bu limitleri aşmamak için ikinci bir servis eklememeye dikkat et.

---

## Sistem Mimarisi (Özet)

```
[Kullanıcı Tarayıcı]
      │
      │ HTTPS
      ▼
[Cloudflare Edge]  ← DDoS, WAF, CDN, SSL termination
      │
      │ Proxied HTTPS (gerçek IP gizli)
      ▼
[Oracle VM: 92.5.35.155 — Nginx]
      │
      ├── 80  → 301 Redirect (her şeyi HTTPS'e yönlendir)
      └── 443 → SSL (Let's Encrypt, footflow.duckdns.org cert)
            │
            ├── /              → index.html (3.2 MB PWA)
            ├── /api/*         → 127.0.0.1:8080 (push_server.py)
            ├── /health        → 127.0.0.1:8080/health
            ├── /sw.js         → no-cache
            └── /.git, *.py, *.log, vapid_keys.json... → 444 (bağlantı kesilir)

[push_server.py — Port 8080]
      │
      +-- GET  /api/vapid-key         → VAPID public key
      +-- GET  /api/subscriptions     → Abone sayısı
      +-- GET  /api/diagnose          → Sunucu durum raporu
      +-- GET  /api/match-goals       → Gol olayları
      +-- GET  /api/match-red-cards   → Kırmızı kartlar
      +-- GET  /api/match-lineup      → Kadro/diziliş
      +-- GET  /api/live-stream-player → TV player
      +-- GET  /api/live-summary      → Tüm canlı maç özeti
      +-- GET  /api/live-sync         → Polling endpoint
      +-- GET  /health                → {"status":"ok"}
      +-- POST /api/subscribe         → Push abonelik kaydet
      +-- POST /api/test-push         → Test bildirimi gönder
      │
      +-- [Thread 1] sahadan_http_sync_worker (her 30s full, her 3s delta)
      |     Sahadan API → canlı maç verisi → gol push + dinamik golcü fetch
      +-- [Thread 2] start_socket_listener
      |     Sahadan WebSocket → gerçek zamanlı olaylar
      +-- [Thread 3] keep_alive_ping
      |     Oracle VM systemd servisi — uyuma yok, 7/24 aktif
      +-- [Thread 4] red_card_monitor_worker (her 3 dk)
      |     Favori maçlarda kırmızı kart push
      +-- [Dinamik Thread] _bg_fetch_goals (Semaphore: max 2 eş zamanlı)
            Gol algılanınca 10s bekleme + 5s aralıkla golcüleri Sahadan'dan çeker,
            all_goals_cache.json'a yazar. Sadece 27 lig/kupa maçları taranır.
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
   → GitHub Actions: build.yml tetiklenir
      → GitHub Pages otomatik deploy (~1-2 dk)
   → GitHub Actions: deploy.yml tetiklenir
      → Oracle VM SSH: git pull + systemctl restart footflow (~30s)

Otomatik Build: Günde 4x (09:00, 13:00, 16:00, 19:00 TSİ)
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
Bu sayede statik HTML dosyası tüm veriyi taşır — backend olmadan da offline çalışabilir.

---

## Olay & Bildirim Akışı

### 1. Gol Olayı & Otomatik Golcü Önbellekleme
1. `sahadan_http_sync_worker` veya WebSocket skor artışı tespit eder.
2. Favorilenen maçlar için anında WebPush bildirimi gönderilir (`send_push_for_match()`).
3. Maç `KNOWN_MATCH_IDS` (27 lig/kupa) içindeyse `_bg_fetch_goals` thread'i başlatılır:
   - `_GOALS_BG_SEM` (max 2 eş zamanlı istek) ile korunur.
   - 10 sn bekler (Sahadan'ın işlemesi için), ardından 5 sn arayla max 10 deneme yapar.
   - Golcüler alınınca `all_goals_cache.json` dosyasına yazılır.
   - Uygulama kapalıyken atılan gollerin bilgisi kullanıcı açtığında hazır gelir.

### 2. Kırmızı Kart Bildirimi
1. `red_card_monitor_worker` her 3 dakikada bir favori canlı maçları 5s stagger ile tarar.
2. Sahadan maç detay sayfasından (`/mac/...`) Nuxt SSR verisi parse edilerek kırmızı kartlar tespit edilir.
3. Yeni kart tespit edilirse `send_push_for_match()` ile bildirim gider.

### 3. Gol İptali (VAR) & Jitter Koruması
1. **Jitter Koruması:** Son 90 saniye içinde gol olmuşsa veya polling eski skor getiriyorsa skor düşüşü engellenir.
2. **2s Ertelenmiş Onay:** Soketten ardışık düşüş gelirse 2 saniye beklenir; skor hâlâ düşükse iptal sesi (`playCancelSound`) ve görsel kırmızı flash tetiklenir.
3. **Maç Bitiş Koruması:** Maç `Played` durumuna geçtiğinde bekleyen iptal timer'ı derhal silinir.
4. **90s Karantina:** İptal edilen skor 90 saniye cooldown'a alınır, tekrar bayat paket gelirse çift ses/bildirim çalmaz.

---

## Sahadan API Yapısı

```
Canlı maçlar:
GET https://www.sahadan.com/api/index/soccer-live-e?a=bs&e=sams&add_playing=1&extended_period=1&date=YYYY-MM-DD

Delta canlı olaylar:
GET https://www.sahadan.com/api/index/soccer-sync-data?a=bs&e=sces&u={timestamp}

Maç detayı (Gol olayları, kadrolar, kartlar):
GET https://www.sahadan.com/mac/{home-slug}-vs-{away-slug}/{uuid}
→ HTML içerisindeki <script id="__NUXT_DATA__"> JSON verisi parse edilir.
```

---

## Özellik Etki Haritası

| Değiştirmek İstediğin | Etkilenen Dosyalar | Dikkat Edilecek |
|---|---|---|
| Yeni lig ekle | `build_desktop.py` (LEAGUES), `leagues_cache.json` | Build sonrası index.html de güncellenir |
| Yeni kupa ekle | `build_desktop.py` (LEAGUES + type:"cup"), `leagues_cache.json` | Fikstür URL formatı farklı olabilir |
| Bildirim başlığı/içeriği | `push_server.py` (process_match_update) | sw.js'de tag ile özel davranış tanımlanabilir |
| Yeni API endpoint | `push_server.py` (do_GET/do_POST) | CORS otomatik, başka bir şey gerekmez |
| Frontend UI değişikliği | `build_desktop.py` (şablon fonksiyonları) | index.html'i elle düzenleme! |
| Push sunucu URL'i | `index.html` L4710 & L6977 | Cloudflare DNS + Nginx config da güncellenmeli |
| Cache versiyonu | `sw.js` L1 | Kullanıcıların tarayıcısı eski cache'i temizler |
| PWA adı/ikonu | `manifest.json` | `icons/` klasöründe dosyalar olmalı |
| Nginx config | Oracle VM `/etc/nginx/sites-available/footflow` | `sudo nginx -t && sudo systemctl reload nginx` |

---

## Bilinen Limitler & Teknik Borç

| Konu | Detay | Çözüm/Geçici Çözüm |
|---|---|---|
| Monolitik index.html | 3.2 MB tek dosya, bundle yok | Build script ile yönetiliyor, kabul edilebilir |
| Tek fiziksel sunucu | Oracle VM çökerse hem ana site hem yedek etkilenir | GitHub Pages statik frontend için yedek çalışır |
| Rate limit | Sahadan hızlı isteklerde 429 verir | 30s poll, browser headers, Semaphore(2) ile kısıtlama |
| Socket bağlantı kopması | WebSocket bağlantısı kopabilir | Otomatik yeniden bağlanma var (~30s) |
| 07:00 abone reset | Her gün 07:00'de favoriler temizleniyor | Tasarım gereği (güne özel favoriler) |
| Kupa yeni tur eşleşmeleri | Fikstür belli olmadan leagues_cache boş olabilir | Canlı akışta competition title ile dinamik KNOWN_MATCH_IDS'e eklenir |
| SSL sertifika mismatch | Nginx SSL sertifikası duckdns.org için — Cloudflare bypass edilirse sorun | Cloudflare her zaman arada olduğundan pratikte sorun değil |
| Swap yok | Oracle VM'de swap partition tanımlı değil | 5.1 GB available RAM var, düşük risk |
