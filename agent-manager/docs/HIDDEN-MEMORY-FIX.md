# Denetçi seçimi ve proje hafızası düzeltmesi — 5 Kasım 2026

Canlı görevde phase_audit:3:0, CAO prompt yankısı ve geçersiz JSON nedeniyle tekrar bekliyor, sonunda başarısız oluyordu. Tercihli denetçi ve yerel yedek seçimi kullanıcı tarafından gizlenen modelleri atlayabiliyordu. Bu yollar görünürlük/sağlayıcı politikasına uyar; plan kataloğu ve sabit yürütücü seçimi de aynı tercihi korur.

Otomatik yürütme hata/başarı metinleri kalıcı proje bilgisine dönüştürülmüyordu denemez: mevcut kod bunları auto_reflection kaydı olarak üretiyordu. Bunlardan biri talimat yankısını başarılı doğrulama diye saklamıştı. Yeni otomatik raporlar episodik plan_notes içinde kalır. Eski otomatik kayıtlar silinmez; karantinada tutulur ve proje bağlamına eklenmez. Kullanıcı kuralı/tercihi ve proje dosya indeksi korunur.

Doğrulama: 91 odaklı regresyon geçti. Tam taramada 604 test ve 5 alt test geçti; 2 hata bulundu. Normal sohbette gereksiz sentez çağrısı kaldırıldı; geçmiş sabit tarih nedeniyle sürücüyle yarışan API test verisi göreli gelecek tarihine alındı. İlgili 21 kontrol yeniden geçti. Son kaynakla tüm paket tekrar çalıştırılmış değildir.

Canlı bakım: görev kendi denetçi hatasıyla durduktan sonra slot=0 ve sağlayıcı çocuklarının kapanması doğrulandı. SQLite tutarlı yedekleri ve hafıza kopyası alındı; yalnız kimliği doğrulanmış eski servis/CAO süreçleri kapatıldı. Mevcut başlatıcıyla yeni servis açıldı. İlk başlatma isteği eski başlatıcı kapanırken geldiğinden Ready durumu beklendi ve mevcut görev yeniden çağrıldı. Kullanıcı uygulamaları kapatılmadı.

Hafızada 11 kayıt özgün kimlikleriyle korundu, aktif proje bilgisi listesinden çıkarıldı; 67 dosyalık indeks korundu. Aynı execution/checkpoint ve sonuçlar korunarak bir resume gönderildi. 39386/39389 olayları açık OpenCode modelinin gerçek çağrısını doğruladı. Bu bakım dış müdahaledir; görev çıktılarının bağımsız kabul edildiği veya görevin tamamlandığı anlamına gelmez.
