# Sohbet plan görünümü — 5 Kasım 2026

İlk kullanıcı hedefi önce, ardından geçmiş planlar ve mevcut plan gösterilir. Her plan kendi kutusundadır; alt adımlar kısa sonuçlu, başlangıçta kapalı açılır bölümlerdir. Açık bölümler canlı güncellemede korunur. Plan sonucu aynı kutuda yer alır. Ham çıktılar ve teknik kanıtlar ayrıntıda korunur.

Yeni worker sonuçları kullanıcıya etkisiyle başlar; plan sonucu ajan/model dökümü üretmez. Eski raporlar yeniden yazılmaz. Özet bağımsız test kanıtı değildir.

Doğrulama: 38 Python regresyonu geçti; `node --check ui/app.js` geçti. `tests/chat-plan-ui.cjs` gerçek yerel sayfa üzerinde yalnız tarayıcı fixture verisiyle ilk mesaj sırası, iki kapalı alt adım, teknik kanıtsız kısa önizleme, plan içinde sonuç ve açılma etkileşimini doğruladı. 1280 ve 390 piksel ekran görüntüleri alındı; sayfa JavaScript hatası yok. Gerçek görev veya model çağrısı oluşturulmadı.

Arayüz değişikliği sayfa yenilemeyle okunur. Motor yönergeleri aktif görev kesilmeden, sonraki güvenli servis yeniden başlatmasında yüklenir. Bu çalışma ortak servisi yeniden başlatmadı.
