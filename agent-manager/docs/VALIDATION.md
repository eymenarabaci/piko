# Sürüm 1.1.0 doğrulaması — 2026-11-04

Bu rapor yayımlanan masaüstü kopyasının kaynakları içindir. Geçmiş laboratuvar/PHASE raporlarının test sayıları bu sürümün sonucu değildir.

## Tam regresyon

Çalışma dizini: depo içindeki `agent-manager/`. Yürütücü: bu dizinin **kendi** `.venv/Scripts/python.exe` dosyası.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

Sonuç: **591 passed, 5 subtests passed in 149.04s**, gerçek işlem çıkış kodu **0**. Python3.12.10 / pytest9.1.1. Sabit bağımlılıklar `requirements.lock.txt`; Windows saat dilimi `tzdata2026.6`, test timeout eklentisi2.4.0 dahil.

İlk masaüstü koşusunda üç test yayımlanmayan `work/ownership-fix-staged` adayına bağımlıydı. Geçici kaynak bağımlılığı kaldırılıp gerçek `manager.resource_locks` süreç kimliğiyle test edildi; son tam koşu yukarıdaki sonuçtur. Model yönlendirmesinde korunan eski masaüstü testleri de birleştirildi: yazıcı hold politikası ile cooldown seçimi aynı uygun model havuzunu kullanır, sağlıklı alternatif varsayılan olarak bekletilmez.

## Arayüz ve gerçek API

Yeni masaüstü kaynaklarından ayrı8788portunda, boş ve göreve özel veri diziniyle servis açıldı. Gerçek Chrome/Playwright kontrolü:

- API bootstrap `isolated_parallel_planning=true`.
- Paralel planlama seçeneği etkin ve seçilebilir.
- Mobil390px görünüm yatay taşma üretmiyor.
- Sayfa/konsol hatası0; HTTP400+ yanıt0.

Masaüstü/mobil ekran görüntüleri görsel olarak incelendi. Sahte sabit kilit süresi ve denetim göstergesi kaldırıldı; bağlantı etiketi CAO çalışıyor varsaymıyor. Test sunucusu ve tarayıcı kapatıldı; ortak kullanıcı servisi kapatılmadı. Kanıtlar yerel `work/release-validation/` altında, kullanıcı verisi olduğu için Git'e eklenmez.

## Güvenlik ve yayın incelemesi

Runtime `data/`, `work/`, `.piko/`, sanal ortamlar ve kimlik bilgileri yayın kapsamı dışındadır. Makineye özgü operatör bakım betikleri yerelde korunur, yeni ürün dosyası olarak eklenmez. Yeni/değişen metinlerde GitHub anahtar ve private-key örüntüsü taraması gerçek değer yazdırmadan yapıldı; işaretlenen yeni kimlik bilgisi yok.

`tools/check_public_repo.py` değiştirilmeden çalıştırıldı. İsim kuralı4uyarı verdi: `manager/token_usage.py` tüketim ayrıştırıcı kodu; önceden depoda bulunan iki skill belgesi ve vendor `secret_gate.py` güvenlik kodu. Bu dört uyarı içerik/tarihçe incelemesinde kod/belge olarak değerlendirildi; otomatik tarama tamamen temiz geçti diye sunulmaz. Denetimin isim kuralı gevşetilmedi.

## Kanıt sınırları

- Regresyonlarda model/reviewer fixture'ları kullanılır; gerçek sağlayıcı paralelliği veya gerçek ücret/kota bakiyesi kanıtlanmaz.
- Entegrasyon testleri2ve6gerçek yerel Python subprocess çakışmasını ve dosya birleştirme/çatışma davranışını sınar. Dış servis etkilerinde exactly-once ve çok dosyalı crash atomikliği garantisi yoktur.
- Ayrı çalışma kopyası işletim sistemi sandbox'ı değildir. Üst program fazları hâlâ sıralıdır.
- Kontrolsüz süre boyunca müdahalesiz tamamlanma ve kesintisiz36saat çalışma iddiası verilmez.
- Laboratuvarın eski2işçi motoru, eski lock şeması, serbest metinden karar çıkaran kuralı ve bağlanmamış checkpoint/zamanlama prototipleri topluca aktarılmadı; daha yeni güvenlik ve DAG uygulaması korundu.

## Dağıtım

Masaüstü kod değişiklikleri önce yedeklendi; yerel farklar üç yönlü incelendi ve uygulama öncesi hedef kimliği yeniden doğrulandı. Kullanıcı veritabanları ve kota ledger'ı taşınmadı/silinmedi. Yeni operations/lock şemaları nedeniyle aynı veri dizinini eski ve yeni binary ile eşzamanlı kullanmayın. Mevcut açık servis yeniden başlatılması bu dosya aktarımından ayrıdır.
