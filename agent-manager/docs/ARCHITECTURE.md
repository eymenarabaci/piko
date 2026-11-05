# Piko mimarisi ve çalışma sözleşmesi

Piko tek yerel servis örneğinde API, web arayüzü, planlama ve CLI yürütme katmanlarını birleştirir. LangGraph ve SQLite kalıcılık altyapısı kullanılır; hazır adımların yürütülmesi özel DAG dağıtıcısıyla yapılır. Bu belge kaynakta bağlı davranışları anlatır; yardımcı bir modülün bulunması veya eski bir test raporu tek başına canlı çalışma kanıtı değildir.

## Ana bileşenler

| Katman | Modüller | Sorumluluk |
|---|---|---|
| API ve arayüz | `manager/app.py`, `ui/` | İstek doğrulama, proje/sohbet, plan, görev durumu ve kullanıcı kararları |
| Program ve plan | `manager/program.py`, `manager/schema.py`, `manager/planning_policy.py`, `manager/parallel_plans.py` | İş dilimleri, geçerli DAG, planlama kapsamı ve isteğe bağlı alt plan derleme |
| Yürütme | `manager/engine.py`, `manager/dag_dispatch.py` | Bağımlılık hazır olma kontrolü, adım yaşam döngüsü, kapasite ve sonuç kalıcılığı |
| Sağlayıcılar | `manager/providers.py`, `manager/model_router.py`, `manager/cli_runtime.py` | Politika uyumlu model seçimi, CLI çağrısı, oturum ve hata yönlendirmesi |
| Denetim | `manager/step_review.py`, `manager/task_record_audit.py` | Yapılandırılmış denetim, sınırlı onarım, görev kaydında belirli zaman/bütçe tutarlılık kontrolleri |
| Kaynaklar | `manager/resource_locks.py`, `manager/processes.py`, `manager/isolated_workspace.py` | Paylaşımlı okuyucu/tekil yazıcı kiralaması, sahipli süreçler ve çatışma korumalı dosya birleştirme |
| İş ve hafıza | `manager/work_record.py`, `manager/project_memory.py` | Kalıcı gereksinimler, karar/teslim kayıtları, proje indeksi ve kaynak bilgisi taşıyan geçmiş |
| Sürücü | `manager/daemon.py`, `manager/driver_hooks.py` | Uygun işleri başlatma, bekleyen işleri takip etme, bağımlılık ve karar akışı |

## Bir görevin yaşam döngüsü

İstek çalışma alanı ve yetkileriyle kaydedilir. Model seçimi katalog, sağlayıcı politikası, rol ve kullanılabilirlik üzerinden yapılır. Plan şeması adım kimliklerini, eksik bağımlılıkları ve döngüleri doğrular; bozuk plan yanıtları için sınırlı düzeltme kullanılır.

Hazır DAG adımları `FIRST_COMPLETED` döngüsüyle çalışır. Görev başına 1–6 adım sınırı ve aynı motor örneğinin ortak altı çalışan havuzu uygulanır. Bağımlı adımlar tamamlanmış öncül sonuçlarıyla başlar. Kalıcı sonuçları tamamlanmış görünen adımlar devam sırasında yeniden çağrılmaz; yarım kalan adımın dış etkisinin hiç gerçekleşmediği varsayılamaz.

Yazma izni olan uygulama adımının çıktısı bağımsız denetime girer. Denetim hatalıysa sınırlı hedefli düzeltme yapılır. Denetim yanıtı ayrıştırılamıyorsa veya kullanıcı testi istiyorsa adım doğrulanmış tamamlanma olarak geçirilmez. Program kabulünde tamamlanmamış bağımlılık, eksik/başarısız yazıcı denetimi ve belirli yanlış görev kayıtları modelin `accept` kararını engeller. Bu kurallar bütün doğal dil iddialarının doğruluğunu otomatik kanıtlamaz.

Tamamlanma bir motor durumudur. Kullanıcının sürümlü teslimi kabul etmesi ayrı kayıttır; fiziksel cihaz, dış servis veya yalnız kullanıcı tarafından gözlenebilen davranışlar ayrıca doğrulanır.

## Devam ve bekleme

OpenCode Direct yolunda oturum kimliği çağrıdan önce saklanır. Eşleşme yürütme, adım ve model bazındadır; uygun devam `--session` ile aynı oturuma gider. Eski kayıtta bulunmayan oturum sonradan uydurulmaz. CLI çağrısında sabit duvar saati timeout'u kapalıdır; iptal ve diğer yetki/kota/bütçe sınırları ayrı işler.

Geçici ağ/sağlayıcı hataları sınırlı dış geri çekilme döngüsüne çıkar. Ağ beklemesi ile kota beklemesi ayrı durum alanlarıyla tutulur. Kota defteri silinerek veya bekleme zamanı sıfırlanarak toparlanma yapılmaz. Başlangıç kurtarması ve sürücü sahipliği, aynı veri alanı üzerinde rakip yürütücü oluşturmamalıdır.

Kalıcı iş sürücüsü bağımlılıkları yalnız ekrandaki son kayıt penceresine göre değerlendirmez: tamamlanmış eski işler tam bağımlılık indeksinde görülür. Eksik, iptal edilmiş veya döngülü bağımlılıklar açık hata kaydı ve bildirim üretir. Plan metnindeki zaman önerisi kendiliğinden gerçek bir zamanlayıcı değildir; yalnız motorun bağlı kalıcı zaman alanları/tetikleyicileri yürütme davranışı oluşturur.

## İzinler ve paralel yazıcılar

`allow_writes` görevin yazma yetkisidir. Kaynak kiralaması bu yetkiyi büyütmez: açık salt okunur görevler ortak okuyucu kiralaması, yazıcı görevler tekil kiralama kullanır. Ayrı kaynak sahibi, token ve süreç kimliği kontrolleri izinsiz kira devrini azaltır.

Normal yazıcı akışında ana çalışma alanı yazma kilidi korunur. İsteğe bağlı izole paralel yazmada Direct adımlar ayrı kopyalarda çalışır, kopyada denetlenir ve ana çalışma alanına hash kontrolüyle birleştirilir. Aynı dosyada farklı sonuçlar çatışmadır; bütün plan grubunun bitmesini bekleyen bir bariyer eklenmez. Ayrıntı ve sınırlar [paralellik belgesindedir](isolated-parallel.md).

Bu mekanizmalar işletim sistemi erişim izolasyonu değildir. Özellikle dış CLI, uzantı ve MCP araçlarının yetkileri ayrıca önemlidir. Bir sağlayıcının read-only/sandbox özelliği diğer adaptörler için varsayılmaz. Uygulama yerel kullanım içindir; çok kiracılı internet servisi olarak tasarlanmamıştır.

## Hafıza, kayıtlar ve kullanım

`data/` altındaki SQLite ve dosya kayıtları görev geçmişi, checkpoint, iş kaydı, bildirim, işlem, metrik ve proje bağlamını saklar. Bunlar özel çalışma verisidir; kaynak dağıtımına veya herkese açık GitHub deposuna eklenmez. Güncellemeden önce tutarlı SQLite yedeği alınmalı, aynı kilit şemasını kullanan tek doğrulanmış servis sahibi çalışmalıdır.

Proje indeksi dosyaların yapısını anlatır; episodik iş hafızası yapılan iş/plan raporlarını ve kaynak bilgisini taşır. Kullanıcı kuralları korunur. Model raporunun doğrulanmamış olması görünür kalır; indeksleme bir kabul testi değildir.

Sağlayıcı bildirimi varsa gerçek token usage çağrı/tur kimliğiyle tekilleştirilir. OpenCode JSONL `step_finish` bildirimleri bu kaynağı sağlar. Bildirilmeyen veya yarım kalan tüketim bilinmiyor olarak kalır; metinden sayı türetilmez. Ledger/kota koruması ile kullanıcıya gösterilen ölçülmüş tüketim toplamı farklı sorumluluklardır. Ön rezervasyon kesin harcama ve kesin bakiye değildir.

## Güncelleme ve kanıt sınırları

Kaynakta yeni kod bulunması, çalışan Python servisinin o kodu yüklediği anlamına gelmez. Kaynak aktarımı, ilgili regresyonlar, bağımlılıklar ve güvenli servis yeniden başlatma ayrı doğrulanır. Etkin yazıcı/planlayıcı/denetçi varken yalnız adım listesinin boş görünmesine dayanarak servis kesilmez.

Testler mock sağlayıcılar, kontrollü yerel süreçler ve dosya deneyleri içerebilir. Gerçek sağlayıcı oturumu, gerçek duvar saati, enjekte saat ve model raporu ayrı kanıt türleridir. Uzun açık kalma süresi, plan yazılması, test kopyasında geliştirilen kod veya dışarıdan yapılan bakım tek başına kesintisiz otonomi kanıtı değildir. Exactly-once dış etki, sert çökmede çok dosyalı atomiklik ve sınırsız müdahalesiz başarı garantisi yoktur.
