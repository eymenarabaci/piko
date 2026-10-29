# Agent Manager

Hazır açık kaynak **[LangGraph](https://github.com/langchain-ai/langgraph)** çalışma zamanı üzerine kurulu yerel CLI ajan yöneticisi. Eski `orchestrator/` motorunu import etmez. Bağımlılık sırası, paralel düğümler, join ve checkpoint/replay işlemlerini gerçek LangGraph `StateGraph` ve `AsyncSqliteSaver` yürütür. API ve arayüz yerel uyarlamadır; tüm upstream uygulamanın fork'u değildir.

## Başlatma

Python 3.12 ve mevcut CLI oturumları gereklidir. Bu bilgisayarda bağımsız `.venv` kurulmuştur.

```powershell
cd 'agent-manager'
.\desktop.cmd
```

### Uzaktan ve Mobil Erişim (Tailscale & Android APK)

Telefondan veya dış ağdan kontrol etmek için:

```powershell
.\remote.cmd
```

Bu komut sunucuyu `0.0.0.0:8765` üzerinde dinleyecek şekilde açar ve ekranda mobil kimlik doğrulama tokenını gösterir. Telefondan bağlanmak için `agent-remote-android/` projesinde üretilen **`AgentRemote.apk`** kullanılır (veya web arayüzündeki **Mobil & Tailscale** sekmesinden indirilir). Yetkili olmayan dış istekler reddedilir; güvenli bağlantı için **Tailscale** IP'si (`100.x.y.z`) ve Bearer Token kullanılır.

Arayüz: **http://127.0.0.1:8765**. Yeni kurulumda önce `setup.cmd` çalıştırılır. Varsayılan tarayıcı kullanıcının uygulamasıdır; servis kapanınca tarayıcı zorla kapatılmaz. Terminalde Ctrl+C sunucuyu ve ona ait CLI işlem gruplarını kapatır.

Tarayıcı açmadan veya başka portla:

```powershell
.\.venv\Scripts\python.exe run.py --port 8765
```

Windows'ta `run.py` kullanın: async subprocess için Proactor döngüsü gerekir. Aynı `data/` klasörüne yalnız **tek sunucu süreci** bağlayın; `--workers` veya `--reload` kullanılmaz.

### Gömülü CAO Windows motoru

`setup.cmd`, bu projenin `vendor/cao-upstream/` klasöründeki CAO kaynağını kendi Windows `.venv` ortamına kurar. **Yapılandırma → Yürütme altyapısı → Gömülü CAO (Windows)** seçildiğinde Agent Manager motoru 127.0.0.1 üzerinde, rastgele yerel port ve oturuma özel bearer token ile açar. CAO verisi `data/cao/` altındadır; servis Agent Manager kapanırken onunla birlikte kapatılır. Harici AO masaüstü uygulaması bu modda kullanılmaz. Codex ve Antigravity CLI araçları Windows PATH içinde bulunmalıdır. Ollama için `127.0.0.1:11434` servisi ve seçilen model yüklü olmalıdır.

LangGraph planı ve adım durumlarını tutar; Codex ve Antigravity adımlarını CAO terminal işçilerine, Ollama adımlarını CAO'nun yerel metin işçisine gönderir. Ollama işçisi dosya araçları kullanmaz ve yazma izinli CAO görevlerinde seçilemez. Motor başlatılamazsa arayüzdeki **Modeller ve CLI** bölümü durumu gösterir; ayrıntı için `data/cao/server.log` dosyasına bakın. `data/cao` klasörünü başka bir CAO sunucusuyla aynı anda paylaşmayın.

Yeni görevlerde yürütücü modeli otomatik seçilir; ayrı bir varsayılan işçi kutusu yoktur. Seçim görev rolü, katalog önceliği, yerel model uygunluğu, gözlenen token kullanımı ve kota/istek sınırı hatasından sonra bekleme süresine dayanır. Sağlayıcı kalan kotasını doğrulanmış biçimde sunmuyorsa sistem bunu **bilinmiyor** olarak gösterir; kesin kalan kota tahmini yapmaz. Kota hatasında adım başka uygun modele en fazla iki kez devredilir. Dosya yazan görevlerde Ollama seçilmez.

CAO içinde 20 uzman rolü bulunur. Ana ajan açık bir işçi çağrısı ürettiğinde en fazla üç alt işçi, tek seviye derinlikte çalışır ve sonuçları ana ajana geri verilir. Resim rolü sınırlı sahneden SVG vektör görsel; video rolü FFmpeg ile kısa soyut MP4 hareketli grafik üretir. Bunlar fotoğraf veya gelişmiş AI video üretimi değildir ve dosya yazma izni gerektirir. Otomatik görevlerde yerel Ollama orkestratörü tamamlanan adımları değerlendirir, en çok iki turda toplam 20 adım sınırı içinde gerekli yeni adımları ekleyebilir ve sonuç özeti verir.

CAO kipinde Codex `reviewer` görevleri `read-only`, `developer` görevleri `workspace-write` sandbox ve `never` onay politikasıyla açılır. Antigravity için sırasıyla `plan` ve `accept-edits` kipleri kullanılır. Antigravity `plan` kipi işletim sistemi düzeyinde dosya sandbox'ı değildir.

## İş akışı

### AO ile Codex işçileri

AO v0.14.6 bu Windows makinesine kuruldu. Masaüstü AO uygulamasını açın; Agent Manager arayüzündeki **Yapılandırma → Yürütme altyapısı → Agent Orchestrator (AO)** seçeneğini kullanın. Yürütücü olarak Codex seçin, dosya yazma iznini açın ve çalışma klasörü olarak en az bir commit'i ve çözülebilen varsayılan dalı olan Git deposu seçin. Yerel ve remotesiz depoda `git config ao.defaultBranch main` kullanılabilir. AO daemon hazır değilse arayüz açıkça uyarır.

LangGraph planı ve checkpoint'i yürütür; AO her adım için Codex Chat oturumu ve ayrı worktree açar. AO oturum kimliği adımın ayrıntısında görünür. Biten AO görevi **İnceleme bekliyor** durumuna geçer: dosya değişiklikleri worktree'de kalır ve ana projeye otomatik aktarılmaz. AO masaüstünde diff'i inceleyip uygun değişiklikleri birleştirin. AGY ve Ollama için **Doğrudan CLI** altyapısı kullanılmaya devam eder; AGY'nin AO Chat kipi bu makinede ek ACP sunucusu olmadan açılmıyor.

AO Codex pilotu ve AGY doğrudan CLI pilotu sonuçları [araştırma notunda](docs/MULTI_AGENT_RESEARCH.md) kayıtlıdır.

- **Önce planla:** Seçilen CLI salt okunur plan üretir. JSON ve DAG doğrulanır. Plan incelenip düzenlenebilir, ardından çalıştırılır.
- **Otomatik:** Planlama ve yürütmeyi art arda çalıştırır.
- **Doğrudan sohbet:** Yürütücüye tek adım gönderir; planlayıcı çağırmaz.
- Başarıyla kaydedilen adımlar devam sırasında tekrar çalıştırılmaz. Başarısız/yarım kalan adım kullanıcı **Devam et** dediğinde yeniden denenir. Otomatik fallback/retry yoktur.
- CLI yan etkileri ile checkpoint yazımı tek işlem değildir. Kesinti anında dosya değiştirmiş ama tamamlanma kaydı yazılmamış adım yeniden çalışabilir. Exactly-once dosya değişikliği garantisi yoktur.
- Aynı görevde yazma izni varsa adımlar seri, salt okunursa seçilen paralellikte çalışır. Ayrı görevler aynı klasörde eşzamanlı yazdırılmamalıdır.
- Adım için 300 sn, bir yürütme/devam denemesi için 1800 sn varsayılan deadline vardır. Sırada bekleme bu süreye dahil değildir. Her devam yeni süre penceresi açar.
- CLI yanıtının tamamlanması, üretilen kodun testlerden geçtiği anlamına gelmez. Gerektiğinde plana açık doğrulama adımı eklenmelidir.

## CLI uyarlamaları

`config/models.json` mevcut kataloğun bağımsız kopyasıdır. Model adları yerel yapılandırma kimlikleridir; canlı sağlayıcı kataloğu olarak sunulmaz.

| Sağlayıcı | Bağlantı | İzin/araç davranışı |
|---|---|---|
| Codex | Native exe veya Node CLI, `exec --json`, prompt stdin | `read-only` / `workspace-write`; kullanıcı araç yapılandırması aktarılmaz |
| Antigravity | `agy --output-format json --print` | `plan` / `accept-edits`; global AGY araçları devralınır; çalışma başına MCP izolasyonu yok |
| Ollama | Yerel `127.0.0.1:11434/api/chat`, NDJSON stream | Metin yanıtı; araç/dosya yürütücüsü eklenmez |

`CLI bulundu` yalnız executable keşfidir; oturum, kota veya model sağlığını garanti etmez. Devre dışı modeller reddedilir. Mevcut CLI oturumları kullanılır; anahtarlar taşınmaz veya yeniden yazılmaz. Antigravity `plan` modu işletim sistemi dosya sandbox'ı değildir; harici araçların kendi izinleri geçerlidir.

## Kapsam

Planlama, model atama, bağımlı/paralel yürütme, plan düzenleme, iptal, kalıcı geçmiş, checkpoint ile devam, olay görünümü ve çıktı indirme yeniden uygulandı. Eski özel Skill/MCP seçim kataloğu, benchmark ekranı, AI denetim/onarım döngüleri, token bütçe sistemi ve artefakt rollback katmanı bu çekirdeğe taşınmadı. CLI'nın kendi araçları sağlayıcının izinleriyle kullanılabilir.

## Kalıcılık ve kaynaklar

- `data/runs.sqlite`: iş/olay indeksi, asenkron SQLite + WAL.
- `data/checkpoints.sqlite`: upstream LangGraph checkpoint kayıtları.
- `work/resource-ledger.json`: sunucu ve CLI yardımcılarının kimlik/kapanma kayıtları; komut satırı ve prompt içermez.
- CLI stdout+stderr toplam sınırı 2 MiB. Beş saniyelik etkinlik olayları geçen süreyi ve alınan bayt sayısını gösterir. Yanıt metni CLI tamamlanınca gösterilir.
- Windows Job Object, askıda başlayan CLI'yı sahipli gruba aldıktan sonra sürdürür; iptal/timeout grubun alt süreçlerini kapatır.
- Yerel API farklı Host/Origin isteklerini reddeder; internet yayını veya çok kullanıcılı kullanım için tasarlanmadı. Prompt ve çıktılar yerel veritabanında saklanır.

## Doğrulama

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp work/test-local
.\.venv\Scripts\python.exe tests/smoke_cli.py
node tests/browser-smoke.cjs
```

CLI smoke gerçek sağlayıcı çağrısı yapar. Tarayıcı testi bu bilgisayardaki Playwright/Chrome yollarını kullanır; `TEST_URL` ile sunucu değiştirilebilir. Çıktılar ve ekran görüntüleri `work/visual/` altındadır. Kurulum sürümleri `requirements.lock.txt` ile sabittir. Araştırma gerekçesi `docs/UPSTREAM.md`, doğrulama sonuçları `docs/VALIDATION.md` içindedir.
