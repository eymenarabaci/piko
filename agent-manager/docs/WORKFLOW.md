# Piko iş yaşam döngüsü

Bir istekte ayrı sonuçlar isteyen işler varsa Piko bunları en fazla beş iş diliminde toplar. Yalnız sıradaki dilimin uygulama planını üretir. Plan bitince bağımsız salt okunur denetim, istenen sonuç ile dosya/test kanıtını karşılaştırır. Kabulden sonra sonraki dilim planlanır; uyumsuzlukta aynı dilim için yeni bir düzeltme planı açılır. Bir dilimde en fazla iki düzeltme planı vardır. Eski planlar ve sonuçları sohbet geçmişinde saklanır; yeni checkpoint kimliği eski adımların yeniden çalışmasını önler.

Uygulayıcı yalnız atanmış değişikliği yapıp teslim eder. Test, derleme ve öz denetim döngüsü başlatmaması prompt'ta belirtilir. Ayrı denetçi hedefli kontrol yapar; somut bulgu varsa uygulayıcıya bir düzeltme turu verilir ve yeniden denetlenir. Bu prompt tabanlı rol ayrımıdır; CLI'nin iç davranışını teknik olarak tamamen yasaklayan bir araç filtresi değildir. Kullanıcı testi gerekiyorsa görev `awaiting_user_test` durumunda durur; yanıt gelince uygulamayı tekrar etmeden denetim sürer. AO worktree işleri birleşmeden tamamlanmış sayılmaz.

Model seçimi önce sağlayıcı, araç, yazma, girdi modalitesi ve belirtilmiş bağlam gereksinimlerini filtreler. Uygun modeller katalog önceliği, gözlenen kota ve kullanım üzerinden sıralanır. Farklı bir modelin bağımsız denetim geri bildirimi görev türüyle kaydedilir; en az beş gözlemden sonra başarı ve süre puanı etkiler. Bu geri bildirim model değerlendirmesidir; bağımsız benchmark veya deterministik test garantisi değildir. Dış benchmark puanı uydurulmaz; yerel model özel olarak zorlanmaz.

Aktif göreve mesaj gönderirken `now`, `next_step`, `next_plan` seçilebilir. Mesajlar istemci kimliğiyle tekrar gönderime dayanıklı ve sıralı kaydedilir. Tek aktif CAO terminali canlı girdi kabul ederse `now` teslim edilir; destek yoksa gerçek teslim türü `next_step` olarak görünür. `sending` rezervasyonu aynı mesajın eşzamanlı adım başlangıcında ikinci kez alınmasını önler. Terminal durumu izlenir; gerçek yürütme hatalarında mevcut kurtarma/model devri çalışır. Doğrudan CLI akışında canlı stdin protokolü kullanılmaz.

Demo ve zaman tercihleri iş dilimi ve plan bağlamında korunur. Tetikleyiciler sayfasındaki `once`, saat dilimi içeren gelecek bir ISO zamanı UTC olarak kaydeder ve çalışınca kapanır. Tekrar için yeni zaman gerekir. Demo çıktısının üretilmesi ve doğrulanması ilgili planın kabul koşuludur.

## Ortak iş kaydı ve teslim masası

Her görev `data/work.sqlite` içinde kalıcı bir iş kaydına bağlanır: hedef, gereksinimler, kabul koşulları, öncelik, son tarih, bağımlılıklar, çıktılar ve bekleyen kullanıcı kararları. Aynı sohbete gelen düzeltme aynı iş kaydını günceller; sohbet uzasa veya Piko yeniden başlasa da "ne istendi / ne yapıldı / sıradaki adım" kayıttan okunur. `GET /api/work/{id}` bu özeti döndürür.

Tamamlanan her yürütme tek bir teslim sürümü üretir (`deliver:{run}:{execution}` idempotency anahtarı). Teslimde kısa sonuç özeti, karşılanan gereksinimler, doğrulama kanıtları, açık noktalar ve durum bulunur. Kullanıcı "Kabul et", "Düzeltme iste" veya "Test sonucu bildir" der; düzeltme istendiğinde önceki sürüm ve `parent_version` korunur. Yeni sürüm eskisinin üzerine yazılmaz.

## Bildirimler

Teslim, engel, gerekli karar ve kota/gecikme olayları `data/notifications.sqlite` içinde tekilleştirilmiş anahtarla saklanır; aynı olay ikinci kez bildirilmez. Tercihler (açık/kapalı, en düşük önem, sessiz saat aralığı ve saat dilimi) API'den yönetilir; sessiz saatte üretilen bildirim kaybolmaz, pencere bitince bir kez teslim edilir.

## Güvenilir yürütme ve bütçeler

Dış işlemler `data/operations.sqlite` defterinde idempotency anahtarıyla tutulur. Başarılı işlem tekrar çalıştırılmaz; aynı anahtar farklı içerikle gelirse reddedilir; servis kesintisinde `pending` kayıt `unknown` olur ve dış sistem doğrulanmadan yeniden çalıştırılmaz. Token, süre ve düzeltme bütçeleri kapsam bazında tanımlanır ve aşımda iş durur. Rol/araç yetkileri `read`, `draft`, `apply` seviyelerine ayrılır; denetçi ve planlayıcı rolleri yazma izni alamaz.

## Entegrasyon katmanı

Yerel dosyalar, GitHub, takvim, Drive/Docs/Sheets ve e-posta aynı araç katmanından tanımlanır. Okuma, taslak ve uygulama ayrı izinlerdir. Dışarıya gönderim/yayın, taslak içeriği ve hedef birlikte gösterilip onay kimliği verilmeden uygulanmaz. Bağlayıcısı olmayan servis `failed` olarak raporlanır, sessizce başarılı sayılmaz.

## İki görünüm, maskot ve "neredesin?"

Piko görünümü sohbet, iş özeti, teslim kartları ve kısa ilerleme gösterir; teknik ayrıntılar "Ayrıntıları göster" ve Geliştirici sekmesi altındadır. Geliştirici görünümü plan, ajan/model, terminal, etkinlik ve iş kaydı sekmelerini açar. Görünüm değiştirmek seçili sohbeti ve işi korur. Maskot durumu gerçek motor durumundan türetilir: çalışma, bekleme/onay, engel ve tamamlanma. "Neredesin?" sorusu sohbet yerine kayıtlı iş özetinden yanıtlanır.

## Zamanlama ve proaktif çalışma

İş sırası son tarih, öncelik, bağımlılık ve yaşa göre deterministik hesaplanır. Proaktif tetikleyiciler kaçırılan çalışmayı saat dilimi ve sessiz saat kurallarıyla telafi eder; dosya değişikliği olayı sürüm anahtarıyla bir kez işlenir ve neden çalıştığı olay kaydında yazar. Model seçimi; başarı, süre, maliyet ve düzeltme sayısı ayrı kaydedilerek geliştirilir. Denetçi kanaati, çalıştırılmış test ve kullanıcı kabulü ayrı alanlardır.
