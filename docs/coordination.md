# Alt görevler ve kontrollü denetim

`WorkflowRequest.coordination_mode` varsayılan olarak `adaptive` değerindedir.
`direct`, açık alt görevleri yürütür ancak otomatik hazırlık ve ek AI denetimi açmaz.
Yeni davranış yeni planlarda kullanılır; çalışan işin planı değiştirilmez.
Yeni AI planları `substeps` ve `decomposition_reason` alanlarını doldurur.
Birden çok somut çıktı dosyasına sahip frontend/logic/general işi tek bırakılırsa,
uyarlamalı hazırlık aşaması dosya sahiplerine göre güvenli bir ayrıştırma dener.
Mevcut dosyalarda karşılıklı okuma/yazma gerektiren işler otomatik bölünmez.
HTML üretimi aynı üst görevdeki JS/CSS varlıklarını bekler. Gerekli hazırlık
üst görev içinde paylaşılır; her dosya için aynı plan yeniden ürettirilmez.

## Planlama ve yürütme

Bir `WorkflowStep`, en fazla altı `substeps` tanımlayabilir. Alt görevler bir
seviye derindir; toplam yürütülebilir düğüm sayısı en fazla 64'tür. Üst görev
bir kapsayıcı olur. Alt görev kimliği `üst__alt` biçimindedir. Üst görevi bekleyen
işler bütün son alt görevleri bekler. Mevcut bağımlılık, dosya çakışması ve
sağlayıcı izolasyonu kuralları gerçek paralelliği sınırlar.

Alt görevler üst görevin bütün çıktı dosyalarını kapsar. Yazma yolları üst görev
listesinin, okuma yolları üst görevin okuma/yazma listesinin alt kümesidir.
Açık Skill/MCP seçimleri genişletilemez; otomatik seçim mevcut izinli katalog
ve çağıran politikasını kullanmaya devam eder. Her alt görev için ayrı yetkinlik,
karmaşıklık ve model seçimi yapılır. Açık kullanıcı model seçimi korunur.

Planlayıcının adım adım ürettiği görev tanımı, beklenen çıktı ve kabul ölçütleri
uygulama için hazırlık kabul edilir; karmaşık her adım için ikinci bir AI planlama
çağrısı açılmaz. Yalnız paylaşılan arayüz sözleşmesi gerektiren işler ayrı,
salt okunur hazırlık düğümü alır. Açık `research`/`planning` adımları kısa JSON
karar kaydı üretir: `summary`, `plan`, `uncertainties`, `acceptance_criteria`.
Dış araştırma yalnız atanmış ve izinli araçlar varsa yapılabilir; araştırma etiketi
internete erişildiğinin kanıtı değildir.

## Denetim ve geri bildirim

Uyarlamalı modda anlamlı karmaşıklıktaki üretim işleri, üreticiden farklı bir
kayıtlı model tarafından salt okunur incelenir. Denetçi dosya kanıtlarını ve
makine kontrol sonuçlarını alır. Denetim çıktısı `approved`, `changes_requested`
veya `unavailable` olarak kaydedilir. Eksik/kırpılmış dosya kanıtı tam onay
sayılmaz. AI değerlendirmesi çalıştırılmış test yerine geçmez.

`max_review_rounds` düzeltme turu sınırıdır (varsayılan 1, aralık 0–2).
Sıfır ek AI denetimini kapatır. Bir düzeltme turu, ilk incelemeyi ve düzeltme
sonrası ikinci incelemeyi içerebilir. Otomatik onay modunda somut bulgular ilgili
üreticiye gönderilir; açık bulgu yolları varsa yazma kapsamı bu dosyalara daralır.
İkinci düzeltme gerekirse mevcut alternatif-model ve kullanıcı sabitleme
kurallarına göre üretici değiştirilebilir. Aynı bulgular tekrarlanırsa, denetim
tamamlanamazsa veya sınır dolarsa otomatik döngü durur.

Üretim, danışma ve denetim tüketimi aynı adım/iş bütçesine dahildir. Varsayılan
adım sınırı 150.000, iş akışı sınırı 250.000 token'dır. Paralel sağlayıcı
çağrıları başlamadan tahmini bağlamları ortak bütçeden rezerve eder; bildirilen
gerçek kullanım gelince rezervasyon serbest bırakılır. Bütçe kendiliğinden
yükselmez. Devam eden tek sağlayıcı çağrısı sınırı aşabilir; bu uygulama
sağlayıcı tarafında kesin ücret tavanı sağlamaz.
Kurtarma yeni yürütmedir: adım sınırı yeni denemelere uygulanır; önceki bütün
tüketim iş akışının toplam sınırında korunur.

Panel alt görev gruplarını, aşamaları, üretici/denetçi modellerini, bulguları ve
denetim tüketimini gösterir. Mevcut kurtarma uçları aynı plan ve izinlerle
etkilenen düğümleri sürdürür. Checkpoint kayıtları süreç belleğindedir;
dosya sürümü yedekleme veya fiziksel geri alma sağlamaz.

## Doğrulama durumu

2026-10-19 planlama düzeltmesinde 16 odaklı birim testi çalıştırıldı. Yerel
`qwen3.5:4b` ile üç salt planlama denemesi yapıldı; üretim ve denetçi modelleri
çalıştırılmadı, proje dosyalarına yazma kapalıydı. Son deneme 6 ana görevi 10
yürütme birimine genişletti, yinelenen çıktı sahibi üretmedi ve 3.437 girdi /
2.877 çıktı planlama tokeni bildirdi. HTML/CSS/JS uygulama dosyaları, iki test
dosyası, kullanım kılavuzu ve test çalıştırma adımı plana girdi. Bu, plan
doğruluğunun incelendiğini gösterir; uygulama dosyalarının üretildiği veya
testlerin gerçekten çalıştırıldığı anlamına gelmez.

## Planlama hataları

Planlama ana plan, sırayla görev ayrıntıları ve Python birleştirme/atama olarak
ayrılır. En fazla 12 ana görev vardır; basit veri, kurulum ve stil görevlerinin
ayrıntıları ek AI çağrısı olmadan tamamlanır. Model/effort/Skill/MCP seçiminde
ayrı Qwen çağrısı yapılmaz.

Ana plan çağrısı 240 saniyeye, alt plan çağrısı 180 saniyeye kadar sürer. Akışın
90 saniye boyunca ilerlememesi ayrıca zaman aşımıdır. Planlama oturumu toplam
900 saniye ve en fazla 26 çağrıyla sınırlıdır. Üretim akışı ara ilerleme
bildirir; zaman aşımı otomatik tekrarlanmaz. Geçersiz veya kesilmiş bir bölüm
yalnız kalan süre ve bütçe içinde bir kez düzelttirilir.
Bu sınır HTTP isteğinin beklemesini sınırlar; Ollama'nın arka plandaki hesaplama
iptaline ilişkin bir garanti değildir.

Hatalar `error_details` içinde `code`, `stage`, `message`, `details` olarak,
ayrıca sunucu hata günlüğüne güvenli metadata ile yazılır. Plan şeması,
bağımlılık, izin, model seçimi ve bağlam bütçesi hataları ayrı kodlanır.
İstemler, sağlayıcının ham yanıtları ve dosya içerikleri günlüğe eklenmez.
Qwen yalnız aşamaya özel kısa yönerge ve daraltılmış görev şemasını alır. Büyük
örnek paketi ve model kataloğu her çağrıya eklenmez; model/effort/araç seçimi
mevcut Python politikasıyla yapılır. İstek ve alınan çıktı bayt sayıları kaydedilir.

Ana plan ve doğrulanmış ayrıntılar oturum belleğinde checkpoint olarak saklanır.
`POST /api/panel/jobs/{job_id}/retry-planning` kullanıcı isteğiyle kalan bölümleri
sürdürür; kapsam değişmez ve en fazla iki sürdürme turu yapılır. Çağrı/tüketim
sayaçları sıfırlanmaz. Sunucu yeniden başlatılırsa bu checkpoint kaybolur.
Birleştirme çelişkisi belirli 1–2 ayrıntıya bağlanabiliyorsa yalnız o parçalar bir
kez geçersizleştirilir; belirsiz veya tekrarlanan çelişkide aynı plan tekrar edilmez.

Yeni alt görev üretim şeması çıktı sözleşmesini, boş olmayan kabul ölçütlerini,
dosya yollarını ve bağımlılık alanlarını zorunlu tutar. Üretimde yerel kimlikler
`part1`–`part4`, dosya seçenekleri ise üst görevin izinli yollarıyla sınırlıdır.
Üst görevin zaten zorunlu tuttuğu bağımlılıklar alt görevde tekrar edilirse Python
bunları üst seviyede korur; bilinmeyen görevler ve gerçek döngüler reddedilir.
Eksik sözleşme, kapsam ve bağımlılık hataları ilgili üst/alt görev ve alanlarla
raporlanır; hata düzeltmesinde ham yanıt yerine bu yapılandırılmış bulgular kullanılır.

Çalışan uygulama isteyen istemlerde plan doğrulayıcı, yalnız JSON/mimari çıktılarının
uygulama sayılmasını önler. HTML/CSS/JS veya yürütülebilir kaynak yazan ancak yanlış
`architecture` etiketi taşıyan görevleri uygun geliştirme türüne çevirir. Çalışan
uygulama ya da otomatik test kaynağı eksikse, açık istek dizininde eksik dosya sahibi
ve bağımlılıkları ekler; test çalıştırma ve doğrulamayı sonrasına bağlar. Salt tasarım
isteklerinde bu ekleme yapılmaz. Mimari/entegrasyon işleri doğrulama, kullanım
kılavuzu ise genel dokümantasyon işi olarak sınıflandırılır; kılavuz ve doğrulama
uygulama çıktılarının ardından sıralanır.

Planlama tüketim sınırı en fazla 60.000 token ve çağıranın iş bütçesi kadardır.
Raporlanan Qwen tüketimi iş toplamına katılır. Bir çağrı kullanım raporu olmadan
kesilirse sonraki planlama için ihtiyatlı girdi/çıktı rezervi korunur; bu rezerv
gerçek tüketim olarak gösterilmez. Devam eden çağrı kesin ücret tavanı sağlamaz.
