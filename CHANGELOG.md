# Değişiklikler

## 1.1.0 — 2026-11-04

- İsteğe bağlı Paralel planlama: 6 alt plan, toplam20 adım ve motor başına ortak6 çalışan kapasitesi; yalnız gerçek bağımlılıkları bekleyen DAG yürütümü.
- Ayrı yazıcı kopyaları, bağımsız denetim ve hash tabanlı kontrollü birleştirme; çelişen değişikliklerin korunması.
- Kalıcı CLI oturumları, aynı-session devam, sınırsız Evren çağrı süresi ve kota/geçici hata bekleme ayrımı.
- Bozuk/eksik review ve kullanıcı testi gereksiniminin tamamlanma sayılmasını engelleyen kapılar; faz ve STATE doğrulaması.
- Sağlayıcının bildirdiği gerçek token kullanımı; bilinmeyen tüketimlerin ayrı tutulması ve çift kayıt koruması.
- Kullanıcı kuralları, proje indeksi ve görev kayıtlarında provenance; özgün sözleşmenin worker/reviewer/repair bağlamına taşınması.
- Paylaşımlı okuyucu/exclusive yazıcı kiraları, süreç kimliğiyle sahiplik; eski iş penceresi dışında kalan bağımlılıkların tanınması.
- Belirsiz harici işlem sonucunda doğrulama gereksinimi ve deterministik tekrar anahtarı; onay/kota defterleri korunur.
- Yerel Windows saat dilimi bağımlılığı ve test timeout eklentisi kurulum tanımına alındı.
- Kurulum, mimari, güvenlik ve doğrulama belgeleri mevcut davranışa göre yenilendi.

Laboratuvar raporları ürün garantisi değildir. Gerçek sağlayıcı paralelliği, genel internet güvenliği, dış etkilerde exactly-once ve sınırsız müdahalesiz başarı garantisi verilmez. Geriye giden eski laboratuvar motoru/kilit şeması topluca taşınmadı.
