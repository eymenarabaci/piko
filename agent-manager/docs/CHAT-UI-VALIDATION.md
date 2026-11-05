# Sohbet plan görünümü — 5 Kasım 2026

İlk kullanıcı hedefi önce, ardından geçmiş planlar ve mevcut plan gösterilir. Her plan kendi kutusundadır; alt adımlar kısa sonuçlu, başlangıçta kapalı açılır bölümlerdir. Açık bölümler canlı güncellemede korunur. Plan sonucu aynı kutuda yer alır. Ham çıktılar ve teknik kanıtlar ayrıntıda korunur.

Yeni worker sonuçları kullanıcıya etkisiyle başlar; plan sonucu ajan/model dökümü üretmez. Eski raporlar yeniden yazılmaz. Özet bağımsız test kanıtı değildir.

Doğrulama: 38 Python regresyonu geçti; `node --check ui/app.js` geçti. `tests/chat-plan-ui.cjs` gerçek yerel sayfa üzerinde yalnız tarayıcı fixture verisiyle ilk mesaj sırası, iki kapalı alt adım, teknik kanıtsız kısa önizleme, plan içinde sonuç ve açılma etkileşimini doğruladı. 1280 ve 390 piksel ekran görüntüleri alındı; sayfa JavaScript hatası yok. Gerçek görev veya model çağrısı oluşturulmadı.

Arayüz değişikliği sayfa yenilemeyle okunur. Motor yönergeleri aktif görev kesilmeden, sonraki güvenli servis yeniden başlatmasında yüklenir. Bu çalışma ortak servisi yeniden başlatmadı.

## Plan hedefi ve bütünsel sentez düzeltmesi

Plan kutusu ilk kullanıcı mesajını hedef olarak tekrar kullanmaz; active_goal, program hedefi veya planın kendi hedefini gösterir. Arşiv hedefi kendi kaydından okunur. İlk kullanıcı mesajı bir kez korunur. Tek adımlı planlarda da hedef ve sonuç görünür. Eski kayıtlı sonuçlar yeniden yazılmaz.

Yeni plan sonuçları salt okunur model çağrısıyla 2–4 cümlelik tek anlatıya sentezlenir; adım raporları peş peşe eklenmez. Açık engeller ve review durumu prompta taşınır. Çağrı yürürlükteki sağlayıcı/kota politikalarına uyar. Sentez hatası görevi başarısız yapmaz ve başarı uydurmaz. Sentez için 60 saniyelik sınırlı bekleme, görev zaman sınırını değiştirmez. Canlı motor bu değişiklik için yeniden başlatılmadı.
