# Doğrulama — 25 Ekim 2026

## Ortam ve sürümler

Windows, Python 3.12.11, Node 24.11.1. Yeni projenin bağımsız `.venv` ortamında LangGraph 1.2.17, langgraph-checkpoint-sqlite 3.1.2, FastAPI 0.162.0, Uvicorn 0.54.0, aiosqlite 0.22.1, httpx 0.28.1, psutil 7.2.2, pytest 9.1.1 ve pytest-asyncio 1.4.0 kullanıldı. Tarayıcı doğrulaması kurulu Chrome ve Playwright 1.62.1 ile yapıldı. Tam bağımlılık sürümleri `requirements.lock.txt` içindedir.

## AO entegrasyonu (25 Ekim 2026)

| Kontrol | Komut / kanıt | Sonuç |
|---|---|---|
| AO kurulumu | v0.14.6 resmi Windows release; GitHub asset SHA256 ile yerel dosya eşleşti | Kuruldu; daemon `/readyz` hazır |
| AO ajan keşfi | `ao agent ls --refresh --json` | Codex ve AGY authorized; AGY AO Chat için ACP sunucusu ayrıca eksik |
| AO Codex pilotu | `work/ao_real_smoke.py`, yalıtılmış pilot Git deposu | Chat oturumu `ao-pilot-1` açıldı; README yanıtı geçmiş API'sinden alındı |
| LangGraph → AO → API | `work/ao_app_smoke.py` | Görev `awaiting_review`, yanıt ve AO oturum kimliği kaydedildi; oturum kapatıldı |
| Antigravity doğrudan CLI | `agy --model gemini-3.8-flash-medium --mode plan --output-format json --print-timeout 90s --print '2+2 kaçtır? Yalnız sayı yaz.'` | `SUCCESS`, yanıt `4` |
| Yeni testler | `.venv/Scripts/python.exe -m pytest -q --basetemp work/test-ao-final-2` | **23 passed in 13.70s** |
| AO ayarları görseli | Playwright `tests/ao-ui-smoke.cjs`, `work/visual/ao-settings.png` | Ayarlar ve hazır bilgisi göründü, 0 konsol/ağ hatası |
| Varsayılan proje | `git init -b main`, `git config ao.defaultBranch main`, `work/ao_real_smoke.py codex project` | `agent-manager-1` oturumu açıldı, README yanıtı döndü, iş ağacı temiz |

AO Codex terminal kancası doktor kontrolünde uyarı veriyor: kurulu Codex 0.163.0 bazı AO launch flag'lerini reddediyor. Yapılandırılmış Codex Chat pilotu çalıştı; terminal kancasının tüm durum olayları doğrulanmış sayılmaz. Codex yanıtı AO `report` komutunu PATH'te bulamadığını belirtti, ancak Chat geçmişi üzerinden tamamlanma sonucu alındı. AGY TUI pilotunda telemetri ön araç kancası bozuktu; resmi eklenti kapatma komutuyla düzeltildi. Sonra AGY TUI `ao report` komutu için izin ekranında kaldı, AO Chat ise `antigravity-acp` eksikliğini bildirdi. Bu nedenle AGY, AO otomatik yürütücüsünde açılmaz; doğrudan CLI yolu doğrulandı.

Pilot oturumları kapatıldı. Göreve ait AO daemon ve test sunucusu PID/yol doğrulamasından sonra durduruldu; kaynak defteri `work/resource-ledger.json` güncellendi. Yüklü AO uygulaması kalır; AO modunu yeniden kullanmak için masaüstü uygulaması açılmalıdır. İndirilen geçici kurucu ve kaynak arşivi kaldırıldı.

## Önceki çekirdek doğrulaması

| Kontrol | Komut / kanıt | Sonuç |
|---|---|---|
| Graph, API ve süreç entegrasyonu | `.venv/Scripts/python.exe -m pytest -q --basetemp work/test-final-04` | **19 passed in 14.26s** |
| Python sözdizimi | `.venv/Scripts/python.exe -m compileall -q manager run.py` | Geçti |
| JavaScript sözdizimi | `node --check ui/app.js` | Geçti |
| Gerçek Codex CLI | `tests/smoke_cli.py`, `gpt-6.1-sol`, salt okunur kısa marker istemi | completed, beklenen AGENT_MANAGER_OK, 16 bayt |
| Gerçek Antigravity CLI | Aynı script, `gemini-3.8-flash-medium` | completed, beklenen marker, 17 bayt (satır sonu dahil) |
| Ollama erişimi | `127.0.0.1:11434/api/tags`, 2 sn sınırı | Erişilemedi; başarılı gösterilmedi |
| Gerçek tarayıcı akışı | `node tests/browser-smoke.cjs`, test sunucusu port 8766 | Planlandı → düzenlendi → Codex yürüttü → completed |
| Konsol/ağ | `work/visual/report.json` | 0 konsol hatası, 0 ağ hatası |
| Yenileme sonrası kayıt | Aynı browser testi | Görev seçimi ve çıktısı korundu |
| Mobil | 390×844, yatay taşma kontrolü ve ekran görüntüleri | Geçti |

Graph testleri gerçek `StateGraph` ve disk tabanlı `AsyncSqliteSaver` kullanır; sağlayıcılar test içinde sahteleştirilir. Canlı CLI ve tarayıcı testleri ayrı gerçek sağlayıcı çağrılarıdır. Üretim uygulamasında sahte sağlayıcı bulunmaz.

## Kapsanan davranışlar

- Planı salt okunur üretme; düzenleme; yürütme; bağımlılık sonuçlarını sonraki düğüme aktarma.
- Başarılı kardeş adımı yeniden çalıştırmadan başarısız düğümü disk checkpoint üzerinden sürdürme.
- Yeniden açılışta aktif kaydı interrupted yapma; kullanıcı isteği olmadan CLI replay yapmama.
- Aktif ve daha driver başlamadan gelen iptali kalıcı duruma yazma.
- Salt okunur adımlarda paralellik; aynı görevde yazma izni olduğunda serileştirme.
- Döngü, eksik/tekrarlı bağımlılık, geçersiz model ve workspace reddi.
- API Host/Origin ve JSON sınırı kontrolleri.
- CLI stdin/stderr; timeout; iptal; çıktı üst sınırı; timeout sonrası alt süreç kapanışı.
- Süreç oluşturma ortasında iptal; kaynak kaydı yazılırken iki kez iptal; sahipli işlemin ve yazı görevinin tamamlanması.

## Görsel kanıtlar

- `work/visual/desktop-welcome.png`
- `work/visual/desktop-models.png`
- `work/visual/desktop-run.png`
- `work/visual/mobile-welcome.png`
- `work/visual/mobile-run.png`
- `work/visual/report.json`: son canlı görev `afd34975384c4a49906472b3048d37e8`, completed.

Ekran görüntüleri açılarak incelendi. Sol lacivert menü, beyaz sohbet, sağ akış paneli ve mobil dar görünüm doğrulandı. Playwright MCP bu oturumda araç olarak sunulmadığından kurulu Playwright kütüphanesi kullanıldı.

## Test sırasında giderilen sorunlar

Görev driver ilk kez çalışmadan iptal edilince CancelledError kaçması düzeltildi. Kaynak kaydı `to_thread` yazımı iptal edildiğinde thread'in daha sonra yazmayı sürdürmesi ve süreç oluşturma sırasında handle kaybı yarışları shield/join ile giderildi. Bunlar için regresyon testleri eklendi.

İlk testlerde sandbox yerel socket işlemlerini engelledi; Python Proactor döngüsü kurulurken takılma görüldü. Doğrulamalar socket erişimi olan yükseltilmiş yerel çalıştırmada yapıldı. Bir otomatik onay çağrısı kullanım kotası nedeniyle çalışmadı; kullanıcının devam talebinden sonra testler tamamlandı. Nihai sonuçlar bu başarısız denemeleri başarı olarak saymaz.

## Kaynak kapanışı

Önceki çekirdek testinin tarayıcıları Playwright close ile kapatıldı. İki geçici sunucu exec oturumları üzerinden durduruldu. O test sonundaki PID/başlangıç zamanı kontrolünde görev ledger'ındaki **12 kaydın hiçbirine ait canlı süreç kalmadı** (`work/resource-check.json`). Kullanıcının mevcut uygulamaları veya ortak MCP süreçleri kapatılmadı. Bu kontrol tüm sisteme ait RAM azalması ölçümü değildir.

## Sınırlar

Uzun süreli yük testi, tüm katalog modelleri ve gerçek büyük kod üretim görevleri doğrulanmadı. Model/CLI kotası veya ağ nedeniyle ileride hata olmayacağı garanti edilmez. Özel Skill/MCP kataloğu, benchmark, artefakt rollback ve eski AI denetim/bütçe katmanları bu sürüme taşınmadı. CLI'nın kendi araç izinleri geçerlidir. Antigravity global araç yapılandırmasını devralır. Yarım kalmış dosya yan etkilerinin exactly-once garantisi yoktur.

## Açık oturum, Ollama ve model seçimi kontrolü (2026-10-27)

- `33dc2dd471d6` ve `005374edfd8c` görevleri dokuzar adım planladı; hiçbir adım yürütülemeden `CAO motoru açılırken kapandı` durumuna düştü. `data/cao/server.log` kesin nedeni `ModuleNotFoundError: No module named 'httpx'` olarak gösterdi. CAO'nun yeni Ollama yolunda kullanılan `httpx>=0.28.1` çalışma bağımlılığına eklendi; `uv lock --project vendor/cao-upstream` ve `uv sync --frozen --no-dev --project vendor/cao-upstream` tamamlandı. Gerçek `data/cao` ile CAO sağlık kontrolü başarılı oldu. Başarısız kullanıcı görevleri kendiliğinden yürütülmedi.
- Son dört plandaki 38 adımın hepsi `gemini-3.8-flash-low` modelini kullandı. Görev ayarında bu model `executor_model` olarak verilmişti; planlayıcı istemi onu tercih edilen yürütücü diye öne çıkarıyor, zorunlu model atama veya bütçe temelli yönlendirme kuralı uygulamıyordu. İsteme, adımın araç/uzmanlığına göre katalogdan model seçme ve tercih edilen modeli otomatik kopyalamama kuralı eklendi. Arayüz etiketi de `Tercih edilen yürütücü` oldu. Bu değişiklik dengeli model dağılımı garantisi değildir.
- CAO üzerinden yerel `qwen3.5:4b` Ollama metin işçisine `Yalnızca TAMAM yaz.` gönderildi; gerçek yanıt `TAMAM`, Ollama kullanım sayaçları 19 giriş ve 4 çıkış tokenıydı. İşçi dosya aracı sunmadığı için CAO yazma izinli adımlarda reddedilir.
- `manager/engine.py` içindeki soru algılama kodunun eksik `re` import'u da giderildi. Yeni kullanım kaydının etkilediği paralel kardeş adım testindeki yapay hata gecikmesi 0,04 sn'den 0,3 sn'ye çıkarıldı; senaryo korunarak zamanlama yarışı önlendi. `.venv/Scripts/python.exe -m pytest -q tests --basetemp work/test-ollama-final`: **29 passed**.
- Geçici `run.py --port 8767` sunucusunda headless Chrome ile ana ekran ve model seçimleri kontrol edildi. `work/visual-current.png` ve `work/visual-current.json`: Ollama görünür, OpenCode gizli, yeni etiket yüklenmiş, konsol ve ağ hatası yok. Geçici tarayıcı ve sunucu kapatıldı.

## Kota yönlendirme ve uzman işçiler (2026-10-27)

- `manager/model_router.py` uygunluk, katalog önceliği, gözlenen token kullanımı ve doğrulanmış kota sinyalini sıralar. Doğrulanmış sağlayıcı kota API'si bağlanmadığı modellerde kalan kota **bilinmiyor** olarak kalır; `429`/kota hatasında model soğumaya alınır ve en çok iki başka uygun modele devir denenir. Bu, gelecekte kota hatası olmayacağı veya dış sağlayıcıların gerçek kalan yüzdesinin bilindiği anlamına gelmez.
- Yeni görevlerde `executor_model=auto`; yürütücü kutusu kaldırıldı. Plan model atamaları olay kaydına yazılır; gerçek kullanılan model sonuçta gösterilir. Salt okunur basit işler için yerel Ollama yönlendirmeye katılır; dosya yazan adımlardan çıkarılır.
- `manager/worker_catalog.py` 20 rol tanımlar. CAO işçi çağrıları üç çocuk ve tek seviye ile sınırlıdır; `manager/cao.py` çağrıları CAO üzerinden yürütür. SVG/MP4 için `manager/local_media.py` sınırlı JSON sahnesi kullanır; FFmpeg MP4 testinde gerçek dosya ve imza doğrulandı. Fotogerçekçi resim veya üretken AI video modeli bağlı değildir.
- `manager/adaptive.py` tamamlanan sonuçlardan sonra en fazla iki plan genişletmesi ve toplam 20 adım sınırı uygular. Her genişletme ayrı LangGraph checkpoint iş parçacığında çalışır; önceki tamamlanmış adımların sağlayıcı çağrısı tekrarlanmaz. Çıktıları inceleyen orkestratör yalnız yerel Ollama'dır. Dış sağlayıcıya görev planı ve adım çıktısı yeniden gönderecek değişiklik otomatik onay incelemesince, belirli dış hedefe açık yetki bulunmadığı gerekçesiyle reddedildi ve uygulanmadı.
- `.venv/Scripts/python.exe -m pytest -q tests --basetemp work/test-all-feature-5`: **57 passed**. Chrome görsel kontrolünde `work/visual-current.png`, `work/visual-workers.png`, `work/visual-current.json` üretildi; yürütücü seçimi kaldırılmış, işçi bölümü yüklenmiş, konsol/ağ hatası yok.
- Gerçek yerel Ollama ile sentetik tamamlanmış iş kararında `complete` alındı. Açıkça eksik doğrulama içeren sentetik örnekte küçük yerel model çelişkili `complete` verdi; bu belirgin durumu otomatik yeni doğrulama adımına çeviren koruma eklendi. Genel akıl yürütme doğruluğu garanti edilmez. Son canlı CAO medya işçisi denemesi kullanıcı tarafından kesildi; başarılı uçtan uca medya çağrısı olarak sayılmadı. Kaynak ledger'ında canlı görev yardımcısı kalmadı.

## Boş planlayıcı yanıtı incelemesi (2026-10-27)

- Son tam proje isteği `119e9ef24c4847a49ad8a717597ec591`, `mode=plan`, `execution_backend=cao`, `planner_model=gemini-3.8-flash-medium` idi. 2129 karakterlik istemden sonra Antigravity CLI yaklaşık 46 saniye çalıştı; 0 çıktı baytı bildirildi ve `Antigravity boş yanıt döndürdü.` hatasıyla görev `failed` oldu. Bu görev için plan/adım veya dosya değişikliği kaydı oluşmadı. Önceki kısa parça istemler ayrı `planned` kayıtlar olarak görünüyor.
- Planlama çağrısı CAO işçisinden önce doğrudan CLI sağlayıcısına gidiyordu. CAO `server.log` dosyasındaki eski `httpx` traceback'leri bu son hatanın nedeni değil; sonraki sağlık kontrolü başarılıydı. Ham Antigravity JSON yanıtı saklanmadığı için boş `response` alanının sağlayıcı içindeki kesin nedeni belirlenemedi.
- Planlama salt okunur olduğundan, boş/geçersiz plan yanıtında yalnız **hazır yerel Ollama** modeline bir kez devreden yol eklendi. Yerel model hazır değilse açık hata verilir; dış sağlayıcıya otomatik yeniden gönderim yapılmaz. Alternatif dış sağlayıcıya kendiliğinden gönderim otomatik onay incelemesince, özel proje istemi için açık hedef yetkisi bulunmadığı gerekçesiyle reddedildi.
- Kota tüketmeyen `FakeProviders` testi: `.venv/Scripts/python.exe -m pytest -q tests/test_engine.py::test_empty_planner_response_uses_only_ready_local_fallback --basetemp work/test-planner-empty-local` → **1 passed**. Gerçek Antigravity veya Ollama ile yeni canlı planlama denemesi yapılmadı.

## Yerel Windows CAO doğrulaması (2026-10-27)

- CAO 2.5.4 kaynak kopyası, Python 3.12.10 ve pywinpty 3.1.0 ile Windows ConPTY üzerinde açıldı; `/health` Windows backend döndürdü.
- Agent Manager üzerinden gerçek Codex ve Antigravity `run-step` çağrıları `TAMAM` yanıtıyla tamamlandı.
- OpenCode 2.1.4 `mini --standalone` açıldı; otomatik `reviewer` profili kuruldu, gerçek görev çağrısı ücretsiz model kotası dolu hatasını başarısız adım olarak döndürdü. Kota nedeniyle başarılı OpenCode yanıtı doğrulanamadı.
- Çalışan bir Codex çağrısı iptal edildi: istemci yaklaşık 6 saniyede çıktı, `/agent-manager/jobs/{job_id}` 200 döndü ve CAO sağlık kontrolü başarılı kaldı. Bu denemede iptal terminal oluşmadan gerçekleşmiş olabilir; terminal oluşturulduktan sonraki yarış için ayrıca canlı kanıt yok.
- `python -m pytest -q tests --basetemp work/test-native-final` sonucu: **28 passed**. İlk sandbox test denemesi geçici klasör/soket kısıtına takıldı; doğrulama yerel izinli çalıştırmada geçti.
- Headless Chrome/Playwright ile ana ekran ve Yapılandırma açıldı; `work/agent-manager-native-ui.png` ve `work/agent-manager-native-settings.png` üretildi. Tarayıcı konsolunda veya ağ isteklerinde hata görülmedi. Ayrı Playwright MCP aracı mevcut değildi; kurulu Node Playwright kullanıldı.
- Codex `reviewer` yerel `read-only` sandbox ve `never` onay kipiyle yeniden çalıştırıldı; `TAMAM` yanıtı alındı. Antigravity `reviewer` için `plan` kipinde ilk canlı deneme kısa yanıtı ayıklayamadı; düşünme satırı ayracı ve canlı ekran durum kontrolü düzeltildikten sonraki canlı deneme yalnız `TAMAM` döndürdü.
- Son Agent Manager regresyonu: `python -m pytest -q tests --basetemp work/test-native-final-2` sonucu **29 passed**.
- Geçici CAO sunucuları `CAOClient.close()`, görsel kontrol sunucusu Ctrl+C ve Playwright tarayıcıları `browser.close()` ile kapatıldı. Görev ledger'ındaki 2026-10-27 tarihli CAO/Agent Manager PID'leri yürütülebilir dosya ve başlangıç zamanı eşleşmesiyle kontrol edildi; bu göreve ait canlı süreç kalmadı.

## Son oturum hata onarımı (2026-10-28)

- `599e85ef28e14ffc9aceb5fe1335971d` görevinde ilk dört adım tamamlandı; beşinci adımın Codex istemi TUI girişinde kaldı ve gerçekte işe başlamadan 3600 saniye sonra 504 döndü. CAO'nun üç güvenli teslim kurtarma denemesi sonuç vermemişti.
- CAO Antigravity durum izleyicisindeki tanımsız `joined` değişkeni `clean` ile düzeltildi. Bu hata son oturumda yinelenen durum izleme istisnalarına neden oluyordu.
- Teslimi doğrulanamayan CAO adımı artık üç kurtarma denemesi ve son bekleme penceresinden sonra `delivery_unconfirmed` (502) döndürüyor. İstem körlemesine tam olarak yeniden gönderilmiyor. CAO'nun sahip olduğu terminal, hazırlık ve adım hatasında temizleniyor.
- Agent Manager CAO adım süresini en az 3600 saniyeye, alt işçi süresini en az 1800 saniyeye yükseltmeyi bıraktı; verilen süreyi kullanıyor. Yeniden denemedeki mevcut olmayan `GET /terminals` çağrısı çıkarıldı.
- Yerel, model çağrısı yapmayan doğrulama: Agent Manager `tests/test_cao.py` **6 passed**; CAO `tests/test_agent_step_delivery.py` **3 passed**; değişen Python dosyalarında `py_compile` başarılı. Çalışan kullanıcı görevi otomatik olarak yeniden başlatılmadı; canlı uçtan uca model denemesi yapılmadı.

## Antigravity kota hatası tanısı (2026-10-28)

- Aynı görevin yeniden denemesinde terminal `e38bb172` için `CAO HTTP 504: provider is in ERROR state` görüldü. Terminal günlüğünde açık neden `Individual quota reached` ve sıfırlanma süresi; bu bir bekleme süresi aşımı değildi.
- CAO, istem gönderilmeden önce ERROR durumuna geçen sağlayıcıyı artık `provider_exited` türünde 502 ile ve terminal kimliğiyle bildiriyor; kendi oluşturduğu terminali temizliyor. Bu adlandırma işlem çıkışı kesinleştiği anlamına gelmez; ERROR durumunu ve teslim reddini sınıflar.
- Agent Manager, 502/504 hata metnindeki büyük harfli `Terminal` kimliğini de bulup ilgili günlükten kota satırını alıyor. Mevcut kota yönlendiricisi böylece Antigravity modelini beklemeye alıp uygun başka modele devir deneyebilir.
- Model çağrısı olmadan doğrulama: Agent Manager `tests/test_cao.py` **7 passed**; CAO teslim testleri **4 passed**; değişen CAO dosyalarında `py_compile` başarılı. Gerçek kota devri yeni canlı görevle henüz doğrulanmadı.

## Model bazlı kota devri (2026-10-28)

- Kota veya hız sınırı hatası tüm Antigravity sağlayıcısına puan cezası vermiyor. Soğuma yalnız aynı `provider` ve `base_model` ile çalışan kayıtları kapsıyor; örneğin Claude Opus yüksek/düşük düşünme düzeyleri birlikte beklerken Gemini Flash ve Argon aday kalır.
- Görev ve alt işçi devrinde başarısız model dışlanır, diğer modeller rol uygunluğu ve önceliğe göre sıralanır. Gerçek kalan kota sağlayıcı tarafından doğrulanmadıkça bilinmiyor olarak tutulur; diğer modelin kota durumuna dair garanti verilmez.
- Model çağrısı yapmayan doğrulama: `tests/test_model_router.py` **7 passed**, `tests/test_engine.py::test_auto_route_hands_off_on_quota_error` **1 passed**.

## Codex uzun istem teslimi (2026-10-28)

- Terminal `ad48f7b0` için 502 hatasında istem Codex'in canlı giriş alanında `› [Pasted Content 9366 chars]` biçiminde kalmıştı. Eski kurtarma yalnız istemin son metnini aradığı için bunu bekleyen giriş olarak tanımıyordu.
- Windows'ta yeni açılmış, adım sonunda kapatılan Codex terminalinde bu işaretçi canlı giriş alanının tamamını oluşturuyor ve karakter sayısı gönderilen istemle eşleşiyorsa CAO yalnız `Enter` tuşunu yeniden iletiyor. Tam istem tekrar yapıştırılmıyor; değişmiş girişe otomatik `Enter` gönderilmiyor.
- Başarısız veya iptal edilmiş adımın terminali girişe `/exit` yazılmadan kapatılıyor. Başarılı adımın olağan kapanışı sürüyor.
- Model çağrısı yapmayan doğrulama: `python -m unittest discover -s .\vendor\cao-upstream\tests -p test_agent_step_delivery.py -v` sonucu **7 test geçti**; değişen Python dosyalarının sözdizimi kontrolü geçti. Canlı uçtan uca teslim henüz doğrulanmadı.
