# FootFlow

Türkiye ve Avrupa futbol liglerini takip etmek için geliştirilmiş açık kaynaklı, PWA destekli canlı skor uygulaması.

## 🌐 Canlı Demo

- **Ana Site:** https://footflow.site/ (Cloudflare CDN)
- **Yedek:** https://footflow.duckdns.org/
- **GitHub Pages:** https://oktemonur7.github.io/FootFlow/ (sadece frontend)

## ✨ Özellikler

- 27 lig ve kupa için **canlı skorlar** ve **puan durumu**
- Maç başına **anlık gol bildirimleri** (Web Push)
- **Golcü detayları** — gol anı ve isimler tooltip olarak gösterilir
- **Kırmızı kart** bildirimleri
- **TV yayın bilgileri** — hangi kanalda yayınlanıyor
- **Kadro/diziliş** görüntüleme
- **VAR / gol iptali** tespiti ve ses uyarısı
- **İddaa oranları** (MS, 2.5 Alt/Üst, KG)
- **PWA** — Ana ekrana eklenebilir, offline çalışır
- **6 opsiyonel bildirim türü** — gol, golcü, iptal, kırmızı kart, devre, maç sonu

## 🏗️ Mimari

```
Kullanıcı → Cloudflare (CDN/WAF) → Oracle Cloud VM (Nginx + Python)
                                  ↕
                             Sahadan.com API (WebSocket + HTTP polling)
```

- **Frontend:** Monolitik HTML/CSS/JS PWA (`index.html`, ~3.2 MB)
- **Backend:** Python HTTP sunucusu (`push_server.py`, Oracle Cloud Always Free VM)
- **CDN/Güvenlik:** Cloudflare (DNS Proxy, WAF, DDoS koruması)
- **Build:** `build_desktop.py` — Sahadan'dan veri çeker, HTML'e enjekte eder
- **CI/CD:** GitHub Actions — otomatik build + Oracle VM deploy

## 📡 Kapsanan Ligler

**Türkiye:** Süper Lig, 1. Lig, Ziraat Kupası  
**Avrupa:** Şampiyonlar Ligi, Avrupa Ligi, Konferans Ligi  
**İngiltere:** Premier Lig, Championship, FA Cup, Lig Kupası  
**Diğer:** LaLiga, Serie A, Bundesliga, Ligue 1, Eredivisie, Primeira Liga, Pro Lig, Premiership, Superliga, Super League, Eliteserien, Chance Liga, Bundesliga (Avusturya), Kral Kupası, İtalya Kupası, Fransa Kupası, Almanya Kupası

## 🛠️ Yerel Geliştirme

```bash
# Bağımlılıkları yükle
pip install -r requirements.txt

# Veriyi güncelle ve HTML oluştur
python3 build_desktop.py

# Backend sunucuyu çalıştır
python3 push_server.py
```

## 📚 Dokümantasyon

- [Proje Durumu](./PROJECT_STATE.md) — Canlı ortam bilgileri ve commit geçmişi
- [Mimari](./PROJECT_ARCHITECTURE.md) — Sistem yapısı, thread mimarisi, API endpointleri
- [Geliştirici Referansı](./PROJECT_REFERENCE.md) — Kritik kurallar, özellik ekleme rehberi
