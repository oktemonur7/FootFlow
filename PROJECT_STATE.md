# FootFlow — Proje Durumu (Güncel)

> Son güncelleme: 2026-09-28

## Canlı Ortam Bilgileri

| Özellik | Değer |
|---|---|
| **Uygulama Adı** | FootFlow |
| **Önceki Adlar** | iddaatakip → FootFollow → FootFlow |
| **GitHub Repo** | https://github.com/oktemonur7/FootFlow.git (Branch: main) |
| **Ana URL** | https://footflow.site/ (Cloudflare CDN + WAF) |
| **Yedek URL #1** | https://footflow.duckdns.org/ (Direkt Oracle VM, Let's Encrypt SSL) |
| **Yedek URL #2** | https://oktemonur7.github.io/FootFlow/ (GitHub Pages, sadece frontend) |
| **Push & Sync Sunucusu** | Oracle Cloud Always Free VM — Ubuntu 24.04, Python systemd servisi, Nginx reverse proxy |
| **Sunucu IP** | 92.5.35.155 (Oracle Cloud Frankfurt — EU bölgesi) |
| **Cloudflare** | DNS Proxy (Proxied A kayıtları), SSL Full mod, Always Use HTTPS aktif |
| **Service Worker Önbellek** | `footflow-v88` |
| **PWA Manifest Adı** | FootFlow |
| **Altyapı** | Render tamamen devreden çıkarıldı. Oracle Always Free (7/24) + Cloudflare WAF devrede. |
| **Güvenlik** | Nginx: .git, .py, .sh, .log, vapid_keys.json, subscriptions.json → 444 (erişim yok) |
| **Fail2ban** | SSH brute-force koruması aktif. 14+ IP banlı. |
| **SSL (Yedek)** | Let's Encrypt — certbot systemd timer ile otomatik yenileme |

## Son Commit Geçmişi

| Hash | Mesaj |
|---|---|
| `c8050c8` | ci: ensure clean git pull on oracle vm by checking out cache files |
| `080b2b7` | chore: remove render references, update docs, set duckdns as fallback |
| `d06816d` | feat(domain): switch primary push server URL to Cloudflare-protected footflow.site |
| `a7c6842` | fix(ui): remove inner scrollbar and max-height from notification toggle list |
| `8979630` | fix(ui): resolve unclosed div tag in notification settings modal header |
| `fda88bc` | feat(notifications): add granular 6-option push preferences with toggle switches |

## Lig & Kupa Listesi (27 Toplam)

### Ligler (19)
| No | Ad | Ülke |
|---|---|---|
| 1 | Trendyol Süper Lig | Türkiye |
| 2 | Trendyol 1. Lig | Türkiye |
| 3 | Şampiyonlar Ligi | Avrupa |
| 4 | Avrupa Ligi | Avrupa |
| 5 | Konferans Ligi | Avrupa |
| 6 | Premier Lig | İngiltere |
| 7 | Championship | İngiltere |
| 8 | LaLiga | İspanya |
| 9 | Serie A | İtalya |
| 10 | Bundesliga | Almanya |
| 11 | Ligue 1 | Fransa |
| 12 | Eredivisie | Hollanda |
| 13 | Primeira Liga | Portekiz |
| 14 | Pro Lig | Belçika |
| 15 | Premiership | İskoçya |
| 16 | Superliga | Danimarka |
| 17 | Super League | İsviçre |
| 18 | Eliteserien | Norveç |
| 19 | Chance Liga | Çekya |

### Kupalar (8 — Fikstür Görünümü)
| No | Ad | Ülke |
|---|---|---|
| 1 | FA Cup | İngiltere |
| 2 | Lig Kupası | İngiltere |
| 3 | Kral Kupası | İspanya |
| 4 | İtalya Kupası (Coppa Italia) | İtalya |
| 5 | Fransa Kupası | Fransa |
| 6 | Almanya Kupası | Almanya |
| 7 | Ziraat Türkiye Kupası | Türkiye |
| 8 | Bundesliga (Avusturya) | Avusturya |

> **NOT:** Kupalar fikstür formatında gösterilir (puan durumu yok). `"type": "cup"` alanı leagues_cache.json'da kupaya özel set edilir.

## İsim Değişikliği Geçmişi & Kalan İzler

### Güvenli İzler (Silinmesi Gerekmiyor)
- `index.html` L6966: localStorage fallback zinciri (footfollow → iddaatakip) — geriye dönük uyumluluk için kasıtlı bırakıldı
- `build_desktop.py`: `fetch_iddaa_odds()` fonksiyon adı — fonksiyon tarif eden isim, değiştirilmemeli
- `index.html` meta description: "iddaa oranları" ifadesi — fonksiyonel tanım, marka adı değil

### Güncellendi
- `manifest.json`: name/short_name → "FootFlow"
- `sw.js`: cache → "footflow-v88", bildirim tag/başlık → "footflow-"
- `index.html`: Başlık, apple-title, tüm server URL referansları
- `push_server.py`: Keep-alive URL, User-Agent, startup log
- Git remote: FootFlow repo'ya taşındı
- Tüm dokümantasyon: Render bağımlılıkları temizlendi

## Bilinen Kısıtlamalar

1. **Push Abonelik Sıfırlanması:** Eski iddaatakip.onrender.com'a kayıtlı push aboneleri yeni sunucuda geçersiz. Bu kullanıcıların bildirimleri almak için yeniden abone olması gerekir.
2. **Monolitik Frontend:** index.html 3.2 MB, build script tarafından üretilir. Doğrudan düzenleme build_desktop.py çalıştırıldığında ezilir. Kalıcı değişiklikler build_desktop.py üzerinden yapılmalıdır.
3. **Sahadan Rate Limiting:** Çok hızlı istek 429 hatası verir. Retry logic ve browser başlıkları eklendi. Golcü arka plan fetch için semaphore (max 2 eş zamanlı) eklendi.
4. **Kupa Yeni Tur Gecikmesi:** FA Cup gibi eleme usulü kupalarda yeni tur fikstürü belli olunca `build_desktop.py` çalıştırılıp push yapılana kadar `leagues_cache.json` güncel değildir. Ancak sunucu bu maçları Sahadan live feed'inden dinamik olarak `KNOWN_MATCH_IDS`'e ekler — golcü fetch bu süre zarfında da çalışır.
5. **Tek Fiziksel Sunucu:** Oracle VM çöker veya Oracle politikaları değişirse tüm altyapı etkilenir. DuckDNS yedek olarak aynı VM'yi işaret ettiğinden gerçek coğrafi yedeklilik yoktur.
6. **SSL Sertifikası Domainlere Bağlı:** Nginx SSL sertifikası `footflow.duckdns.org` için verilmiş. footflow.site HTTPS'i Cloudflare üzerinden hallediliyor (Full mod). Cloudflare bypass edilirse sertifika domain uyuşmazlığı olur.
