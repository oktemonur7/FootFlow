# FootFlow — Proje Durumu (Güncel)

> Son güncelleme: 2026-09-13

## Canlı Ortam Bilgileri

| Özellik | Değer |
|---|---|
| **Uygulama Adı** | FootFlow |
| **Önceki Adlar** | iddaatakip → FootFollow → FootFlow |
| **GitHub Repo** | https://github.com/oktemonur7/FootFlow.git (Branch: main) |
| **Web Sitesi (GitHub Pages)** | https://oktemonur7.github.io/FootFlow/ |
| **Push & Sync Sunucusu (Render)** | https://footflow-6550.onrender.com (Python 3, Free Plan, Virginia) |
| **UptimeRobot İzleme** | https://footflow-6550.onrender.com/api/subscriptions (Her 10 dk) |
| **Service Worker Önbellek** | `footflow-v50` |
| **PWA Manifest Adı** | FootFlow |
| **Render Plan** | Free (750 saat/ay) — tek servis yeterli |

## Son Commit Geçmişi

| Hash | Mesaj |
|---|---|
| `790e460` | feat: kupa maçlarını dinamik olarak KNOWN_MATCH_IDS'e ekle |
| `6184b63` | fix: golcü fetch sadece uygulamamızdaki 26 lig/kupa maçlarında |
| `166baa4` | fix: maç sonu yanlış iptal sesi — Leipzig 5-0→5-1 senaryosu |
| `e6bf2e9` | feat: gol sonrası otomatik golcü cache — sunucu taraflı arka plan fetch |
| `034d396` | Optimize push dispatch latency, fix cached_names bug, speed up goal fetch retry |
| `03fee44` | Fix false goal cancellations caused by polling cache rollback and socket jitter |
| `a6d5a9c` | feat: Render URL güncellendi (footflow-6550) ve remote repo ayarlandı |

## Lig & Kupa Listesi (26 Toplam)

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

### Kupalar (7 — Fikstür Görünümü)
| No | Ad | Ülke |
|---|---|---|
| 1 | FA Cup | İngiltere |
| 2 | Lig Kupası | İngiltere |
| 3 | Kral Kupası | İspanya |
| 4 | İtalya Kupası (Coppa Italia) | İtalya |
| 5 | Fransa Kupası | Fransa |
| 6 | Almanya Kupası | Almanya |
| 7 | Ziraat Türkiye Kupası | Türkiye |

> **NOT:** Kupalar fikstür formatında gösterilir (puan durumu yok). "type": "cup" alanı leagues_cache.json'da kupaya özel set edilir.

## İsim Değişikliği Geçmişi & Kalan İzler

### Güvenli İzler (Silinmesi Gerekmiyor)
- `index.html` L5377: localStorage fallback zinciri (footfollow → iddaatakip) — geriye dönük uyumluluk için kasıtlı bırakıldı
- `build_desktop.py`: `fetch_iddaa_odds()` fonksiyon adı — fonksiyon tarif eden isim, değiştirilmemeli
- `index.html` meta description: "iddaa oranları" ifadesi — fonksiyonel tanım, marka adı değil

### Güncellendi
- `manifest.json`: name/short_name → "FootFlow"
- `sw.js`: cache → "footflow-v50", bildirim tag/başlık → "footflow-"
- `index.html`: Başlık, apple-title, tüm server URL referansları
- `push_server.py`: Keep-alive URL, startup log
- Git remote: FootFlow repo'ya taşındı

## Bilinen Kısıtlamalar

1. **Push Abonelik Sıfırlanması:** Eski iddaatakip.onrender.com'a kayıtlı push aboneleri yeni sunucuda geçersiz. Bu kullanıcıların bildirimleri almak için yeniden abone olması gerekir.
2. **Render Free Plan Uyku:** 15 dk hareketsizlik sonrası uyur. Keep-alive (9 dk iç ping) + UptimeRobot (5 dk dış ping) çift güvence ile çözülmüş.
3. **Monolitik Frontend:** index.html 3.2 MB, build script tarafından üretilir. Doğrudan düzenleme build_desktop.py çalıştırıldığında ezilir.
4. **Sahadan Rate Limiting:** Çok hızlı istek 429 hatası verir. Retry logic ve browser başlıkları eklendi. Golcü arka plan fetch için semaphore (max 2 eş zamanlı) eklendi.
5. **Ephemeral Disk:** Render free plan'da `all_goals_cache.json` ve `subscriptions.json` her yeni deploy'da sıfırlanır. `subscriptions.json` için kritik — kullanıcıların yeniden abone olması gerekebilir.
6. **Kupa Yeni Tur Gecikmesi:** FA Cup gibi eleme usulü kupalarda yeni tur fikstürü belli olunca `build_desktop.py` çalıştırılıp push yapılana kadar `leagues_cache.json` güncel değildir. Ancak sunucu bu maçları Sahadan live feed'inden dinamik olarak `KNOWN_MATCH_IDS`'e ekler — golcü fetch bu süre zarfında da çalışır.
