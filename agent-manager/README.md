# Piko — ajan ve görev motoru

Piko, yerel web arayüzünden CLI tabanlı yapay zekâ işçilerini yöneten bir FastAPI uygulamasıdır. Görevi plana böler, adımları bağımlılıklarına göre yürütür, sonuçları kaydeder ve uygulama değişikliklerini ayrı bir denetim turuyla değerlendirir. Uzun işler için kalıcı iş kaydı, oturumdan devam, kota beklemesi ve sınırlı hata toparlama mekanizmaları bulunur.

Bu klasör GitHub deposunun `agent-manager/` bileşenidir. Üst dizinin [README](../README.md) dosyası kurulum ve masaüstü başlatıcıları için giriş noktasıdır.

## Kurulum ve başlatma

Windows ve Python 3.12+ gerekir. Depo kökünde:

```powershell
.\setup.cmd
.\desktop.cmd
```

Başlatıcı tarayıcıda yerel adresi açar; varsayılan adres `http://127.0.0.1:8765` olur. İlgili port başka bir kopya tarafından kullanılıyorsa başlatıcının açtığı adresi kullanın. CLI araçları kendi sağlayıcı oturumlarıyla ayrıca kurulmuş ve yetkilendirilmiş olmalıdır. CLI'nın bulunması, modelin erişilebilir olduğu veya kotası bulunduğu anlamına gelmez.

Yalnız bu bileşeni açmak için, kurulmuş ortamla:

```powershell
cd agent-manager
.\.venv\Scripts\python.exe run.py --open
```

Aynı veri dizinini birden fazla sunucuya kullandırmayın. Windows subprocess desteği için `run.py` başlatıcısını kullanın; servis için çoklu `--workers` veya geliştirme `--reload` kipi kullanılmaz.

## Görev nasıl ilerler?

1. Kullanıcı hedefi, çalışma alanını ve yazma iznini belirler. Planı inceleyerek yürütme veya orkestrasyon akışı seçilebilir.
2. Planlayıcı model ve rol atamalarıyla bağımlılık grafiği üretir. Geçersiz şema için sınırlı düzeltme ve uygun model seçimi uygulanır.
3. Hazır adımlar kapasite oldukça başlar. Bir adım yalnız kendi bağımlılıklarını bekler; bağımsız bir kardeş adımın bitmesi bütün akışı durdurmaz.
4. Yazma yetkisi olan uygulama adımları bağımsız denetimden geçirilir. Başarısız denetim hedefli düzeltme ister; bozuk veya kullanılamayan denetim doğrulanmış tamamlanma sayılmaz.
5. İş, adım çıktıları ve kabul kararları kalıcı kayıtlarda tutulur. Bir yürütmenin bitmesi ile kullanıcının teslimi kabul etmesi ayrı kararlardır.

Planlama menüsünde **Hızlı**, **Dengeli**, **Ayrıntılı** ve **Paralel** seçenekleri bulunur. İlk üçü plan içeriğinin kapsamını belirler. Paralel seçeneği yararlı olduğunda bağımsız alt planlar üretmeye izin verir; her görevi zorla paralelleştirmez.

## Paralellik ve kontrollü birleştirme

- Hazır DAG adımları için görev başına `max_parallel` 1–6 arasındadır. Aynı motor örneğindeki yürütücüler ortak altı çalışan kapasitesini paylaşır.
- İsteğe bağlı paralel planlama en fazla altı alt planı, toplam en fazla 20 adımlık bir grafikte birleştirir.
- `parallel_writes` kapalıyken aynı çalışma alanındaki yazıcılar korunarak sırayla yürütülür. Açıldığında Direct backend bağımsız yazıcılara ayrı dosya kopyaları verir.
- Denetimden geçen kopyalar başlangıç hash'leriyle karşılaştırılır. Aynı dosyada uyuşmayan değişiklik sessizce ezilmez; çatışma olur. Birleşik ürün için ayrıca entegrasyon/kabul adımı gerekir.

Bu sınır, motor örneğinin adım kapasitesidir; tüm makinedeki her model çağrısı veya ayrı sunucular için ortak bir limit değildir. Ana programın sıralı iş dilimleri korunur; aktif hedefin bağımsız alt planları paralelleşir. Ayrıntı: [İzole paralel yürütme](docs/isolated-parallel.md).

## Sağlayıcılar ve devam davranışı

`config/models.json` model kataloğunu, `config/cli_config.json` CLI bağlantılarını, `config/execution_policy.json` izin verilen sağlayıcı politikasını tanımlar. Dağıtım politikası katalogda görünen modelleri ayrıca daraltabilir.

| Yol | Davranış |
|---|---|
| Direct / Evren–OpenCode | CLI JSONL çıktısı, kalıcı oturum kimliği ve aynı adım/model eşleşmesiyle `--session` devamı. Bu yolda sabit CLI duvar saati timeout'u kapalıdır. |
| Direct / diğer adaptörler | Kurulu CLI veya yerel sağlayıcı üzerinden çalışır; özellikler sağlayıcının araç ve izin desteğine bağlıdır. |
| AO / CAO | İsteğe bağlı yürütme altyapılarıdır. Kurulum ve uyumlu model gerekir; izole paralel yazma Direct yoluna özeldir. |

Evren CLI çağrısının sınırsız timeout'u, görevin veya kaynaklarının sınırsız yetkili olduğu anlamına gelmez. Kullanıcı iptali, kota/onay kapıları ve yapılandırılmış diğer bütçeler korunur. Ağ hatasında aynı model/oturum için sınırlı geri çekilme; kota durumunda bekleme ve uygun model yönlendirmesi vardır. Her hata otomatik çözülemez. Süreç yeniden başlatılınca kayıtlı iş devam edebilir; dış araç etkileri için exactly-once garantisi verilmez.

## Hafıza ve kullanım ölçümü

Proje dosya indeksi ile iş/plan geçmişi ayrı tutulur. Kullanıcı kuralları ve tercihlerinin kaynağı korunur. Modelin kendi başarı raporu otomatik olarak doğrulanmış bilgiye dönüştürülmez; kayıtların doğrulama durumu bağlamda gösterilir.

Tüketim ekranı sağlayıcının bildirdiği gerçek usage kayıtlarını esas alır. Sağlayıcı sayı vermiyorsa tüketim **unknown / bilinmiyor** kalır; metin uzunluğundan tüketim tahmini yapılmaz. Eski veya ölçülmemiş ledger kayıtları gerçek tüketim toplamı olarak sunulmaz. Model seçimi için ön rezervasyon, fatura veya kesin kalan kota değildir.

## Güvenlik ve çalışma alanı

Piko dosya yazma izinleri, onay kapıları, kaynak kiralamaları ve yerel API erişim kontrolleri uygular. Salt okunur işler ortak okuyucu kiralaması, yazıcı işler tekil kiralama alır. Süreç sahipliği PID ve başlangıç zamanıyla denetlenir; bir işlem yalnız adına bakılarak kapatılmaz.

**Ayrı klasör ve izole kopya işletim sistemi sandbox'ı değildir.** CLI çalıştığı hesabın ve kendi sağlayıcısının yetkilerine sahiptir. Güvenilmeyen görevler için ayrıca kısıtlı kullanıcı/container, dosya ve ağ erişim sınırları gerekir. Uzaktan erişimde `remote.cmd`, Tailscale ve bearer token akışını kullanın; servisi doğrudan internete açmayın. Prompt, araç ve çıktı kayıtları yerel veri dizininde saklanır; bu dizin ve kimlik bilgileri Git'e eklenmez.

## Geliştirme ve doğrulama

Bu bileşen dizininde:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

Test bağımlılıkları için `requirements-dev.txt`, sabitlenmiş çalışma ortamı için `requirements.lock.txt` kullanılır. Test kapsamı sahte sağlayıcılar, birim testleri ve gerçek yerel subprocess deneylerini birlikte içerir. Geçmiş laboratuvar raporları güncel kaynağın geçtiği anlamına gelmez; sürüm doğrulaması ilgili kaynak ve ortamda yeniden yapılmalıdır. Gerçek sağlayıcı çağrıları kota tüketebilir ve ayrıca yetkili ortam gerektirir.

- [Mimari ve veri akışı](docs/ARCHITECTURE.md)
- [İzole paralel yürütme](docs/isolated-parallel.md)
- [İş kaydı ve teslim akışı](docs/WORKFLOW.md)

Piko görev için kendi kaynak kodunu düzenleyebilir; bu, çalışan servisinin otomatik olarak kendini güncellemesi anlamına gelmez. Kod aktarımı, test ve gerektiğinde kontrollü servis yeniden başlatma ayrı adımlardır.
