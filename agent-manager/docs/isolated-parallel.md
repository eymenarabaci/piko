# İzole paralel plan ve adım yürütme

Paralellik bağımsız işler için kullanılır. Sırf kapasite var diye bir görev alt planlara bölünmez; aynı dosyayı veya ortak dış durumu değiştiren işler gerçek bağımlılıklarla sıralanmalıdır.

## Açma ve sınırlar

Arayüzde **Planlama → Paralel** seçimi Direct backend'i ve bağımsız alt plan seçeneğini açar. Yazma izni ayrıca verilmişse izole paralel yazma açılır; seçim tek başına yazma yetkisi kazandırmaz. Eski backend ilgili özelliği duyurmuyorsa arayüz bu seçeneği etkinleştirmez.

API isteğinin ilgili alanları:

```json
{
  "prompt": "Bağımsız modülleri geliştir ve birleşik çıktıyı test et.",
  "mode": "orchestrate",
  "planning_level": "parallel",
  "allow_writes": true,
  "max_parallel": 6,
  "execution_backend": "direct"
}
```

Alanları ayrı yönetmek için `allow_parallel_plans: true` ve `parallel_writes: true` kullanılabilir. İzole yazma için `allow_writes: true` ve Direct backend gerekir. Bu iki gelişmiş alan varsayılan olarak kapalıdır; mevcut görevler sessizce paralel moda geçirilmez.

| Sınır | Kapsam |
|---|---|
| 1–6 çalışan | Görev başına istenen `max_parallel` |
| 6 ortak çalışan | Aynı motor örneğindeki hazır DAG adımlarının paylaştığı kapasite |
| En fazla 6 alt plan | Bir planlayıcı yanıtındaki `parallel_plans` paketi |
| En fazla 20 adım | Bütün alt planların birleştirilmiş toplamı |

Bu kapasite bütün makineyi veya ayrı servis örneklerini kapsayan model çağrısı kotası değildir. Planlayıcı çağrıları ve motor dışından başlatılan işlemler de bu tabloyla sınırlandırılmış sayılmaz.

## Bağımlılıklar ve zamanlama

Planlayıcı normal tek `steps` planı döndürebilir. Alt plan seçeneği açıkken şu yapı da geçerlidir:

```json
{
  "parallel_plans": [
    {
      "id": "a",
      "plan": {"steps": [{"id": "analyze", "title": "Analiz", "prompt": "Mevcut modülü incele."}]},
      "depends_on": []
    },
    {
      "id": "b",
      "plan": {"steps": [{"id": "verify", "title": "Doğrulama", "prompt": "Analiz sonucunu doğrula."}]},
      "depends_on": ["a"]
    }
  ]
}
```

Bu örnek yalnız paket biçimini gösterir; gerçek görevde uygun model, rol ve yetki ayrıca seçilir. Derleyici adımlara benzersiz kimlikler verir ve özgün plan/adım eşlemesini `parallel_plan_origins` içinde saklar. Eksik veya döngülü bağımlılık çalıştırmadan önce reddedilir.

Her adım kendi bağımlılıkları tamamlanınca hazır olur. `depends_on`, yalnız o alt planın kök adımlarını öncül planın son adımlarına bağlar. Bağımsız planlar birbirini beklemez. Yürütücü `FIRST_COMPLETED` ile boşalan kapasiteyi değerlendirir; bütün paralel grubun bitmesini zorunlu kılan toplu bariyer yoktur. Bağımlılık bekleyen adım ortak çalışan kapasitesini işgal etmez.

Üst programın birbirine bağlı ana dilimleri sırayla kalır. Özellik, aktif hedefin içindeki bağımsız alt planları ve adımları paralelleştirir.

## Yazma kopyası ve birleştirme

1. Motor ana çalışma alanının başlangıç dosya hash'lerini alarak yazıcı adım için ayrı geçici kopya oluşturur.
2. Adım ve bağımsız denetim bu kopyada yürür. Aynı görev/yürütme/adım kimliğiyle devam edildiğinde kayıtlı kopya yeniden kullanılabilir.
3. Denetimden sonra ana çalışma alanı yazma kilidi altında dosya değişiklikleri önceden kontrol edilir. Ana dosya başlangıçtan farklıysa ve aynı sonuca sahip değilse çatışma oluşur.
4. Ayrı dosyalardaki değişiklikler birleştirilir. Aynı dosyada farklı değişiklikler sessizce ezilmez; otomatik satır birleştirme yapılmaz.
5. Birleştirilmiş sonuç kalıcı adım kaydına işlendiğinde başarılı kopya temizlenir. Hata veya çatışma kopyaları inceleme için korunur.

`isolated_step_created` ve `isolated_step_merged` olayları akışı görünür kılar. Kopyalar sistemin geçici dizinindeki `piko-isolated-workspaces` altında tutulur. Etkin iş varken elle silmeyin.

`.git`, sanal ortamlar, `node_modules`, çalışma verileri/önbellekler ve bilinen sır dosyaları kopyalanmaz. Symlink ve junction yolları reddedilir. Kopyalanmayan bağımlılıklar için uygun ortamın hazırlanması gerekir; testin gerçekten kopyadaki kaynakları import ettiği ayrıca kontrol edilmelidir.

## Güvenlik ve doğrulama sınırları

- Dosya kopyası OS sandbox değildir; CLI'nın mutlak dış yollara ve ağa erişimini OS seviyesinde engellemez.
- Dış servis, e-posta, GitHub veya cihaz etkileri dosya kopyasıyla izole olmaz. Exactly-once garantisi yoktur.
- Birleştirme hatasında geri alma koruması vardır; sonradan gelen kullanıcı değişiklikleri üzerine yazılmamalıdır. Sert süreç/donanım çökmesinde çok dosyalı atomiklik garanti edilmez.
- Kopya denetimleri birleşik ürün testinin yerine geçmez. Bağımlı son kabul adımı birleşmiş ana çalışma alanını doğrulamalıdır.
- Testlerde iki ve altı gerçek Python subprocess'inin örtüşen çalışması, dosya çatışması ve iptal/kapasite temizliği sınanır. Bu testlerde model/denetçi fixture olabilir; sonuçlar canlı sağlayıcı paralelliğinin kanıtı değildir.

İlgili testler, `agent-manager/` dizininden:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/test_dag_dispatch.py tests/test_parallel_plans.py tests/test_parallel_planner_integration.py tests/test_parallel_isolated_engine.py tests/test_isolated_workspace.py tests/test_parallel_planning_level.py
```
