# Farklı CLI ajanlarına iş dağıtımı — GitHub araştırması

Tarih: 25 Ekim 2026. Bu belge ilk araştırma kararını ve sonradan yapılan pilotun sonucunu kaydeder.

## Uygulama durumu (25 Ekim 2026)

- AO v0.14.6 Windows uygulaması kuruldu. Resmi GitHub sürüm varlığı SHA256 özeti (`035c27f00604d4b427cd0dfdfb4fdf53ee2696c78cbcebeeba198b69893fa011`) indirmeyle eşleşti.
- `manager/ao.py` AO daemon API'sine bağlanır; LangGraph planlama, bağımlılık ve checkpoint otoritesi olarak kalır. AO Codex Chat işçisini ayrı Git worktree'de açar; gerçek pilot README okuma görevi tamamlandı ve yanıt API üzerinden alındı.
- AGY, AO kaynak kodundaki ajan kayıt listesinde vardır ve `ao agent ls --refresh --json` yetkili gösterir. Fakat bu Windows kurulumunda AO Chat için `antigravity-acp` sunucusu eksik; TUI bitiş raporu izin ekranında bekledi. Bu yüzden AO yürütücüsü şimdilik yalnız Codex'e açıldı. AGY mevcut doğrudan CLI yürütücüsünde çalışır; gerçek `agy --print` pilotu `SUCCESS` ve `4` yanıtı döndürdü.
- AGY'nin `googlecloudtools.datacloud_telemetry` eklentisindeki bozuk Windows ön araç kancası, resmi `agy plugin disable` komutuyla devre dışı bırakıldı. Önceki yapılandırma `work/agy-config.before-ao.json` ve `work/agy-telemetry-hooks.before-ao.json` konumunda yedeklendi.
- Kod çıktısı AO worktree'sinde kalır. Yerel görev `awaiting_review` durumuna geçer; ana dala otomatik birleşme yapılmaz.

Aşağıdaki karşılaştırma ve tasarım ilk araştırma kaydıdır; tamamlanmamış gelecek-zaman maddeleri bugünkü davranışı tarif etmez.

## Karar

**İlk entegrasyon adayı: [Orchestrator.inc / Agent Orchestrator](https://github.com/OrchestratorInc/agent-orchestrator) (Apache-2.0).** Bir işi küçük görevlere ayırıp farklı ajan oturumlarına dağıtabilen hazır, Windows sürümü bulunan bir uygulama ve daemon. Codex ile `agy` (Antigravity) adaptörleri mevcut. İşçileri ayrı Git worktree'lerde çalıştırıyor; oturum, soru/izin bekleme, inceleme ve ilerleme durumunu tutuyor. Kaynaklar: [README](https://github.com/OrchestratorInc/agent-orchestrator), [ajan yetenekleri](https://docs.orchestrator.inc/plugins/agents/), [mimari](https://github.com/OrchestratorInc/agent-orchestrator/blob/main/docs/architecture.md).

Bu seçim, **mevcut LangGraph planlayıcısının yerine doğrudan geçirilmesi** anlamına gelmez. Agent Orchestrator başlı başına ikinci bir planlayıcıdır. İki planlayıcıyı aynı iş üzerinde bağımsız bırakırsak çakışan görev atamaları ve çift yazma oluşabilir. Önerilen sınır: LangGraph plan, bağımlılık ve checkpoint sahibi; AO ise Codex/AGY işçi oturumlarının açılması, yalıtımı ve durum raporu sahibi. AO'nun kendi proje planlayıcısı bu yolda kullanılmaz.

## Karşılaştırma

| Proje | Gerçek görev devri | Codex / Antigravity | Windows durumu | Bu proje için sonuç |
|---|---|---|---|---|
| [Orchestrator.inc](https://github.com/OrchestratorInc/agent-orchestrator) | Proje planlama, işçi oturumları, ayrı worktree, gözlem ve review | İkisi de kayıtlı; `agy` yalnız terminal arayüzünde, Codex terminal ve yapılandırılmış Chat'te | Resmi Windows indirmesi; ConPTY | **Pilot adayı**; hazır daemon ve CLI ile mevcut UI'ye bağlanabilir. |
| [AWS Labs CLI Agent Orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | Supervisor, `assign`/`handoff`, paralel veya sıralı CLI işçileri | İkisi de destekleniyor | `tmux >=3.3` zorunlu; paket macOS/Linux hedefli | Kavram olarak güçlü, bu Windows kurulumunda WSL/tmux yükü var. |
| [Augani Agent Orchestrator](https://github.com/Augani/agent-orchestrator) | Kalıcı plan, sınırlı görev kapsülü, CLI yürütme, soru ve review kapıları | `codex exec` ve `agy` headless profilleri | Windows desteği README'de açık doğrulanmıyor; küçük/yeni repo | Python iş yürütücüsünden yararlanılabilir, fakat çekirdek bağımlılık için önce Windows pilotu ve kod incelemesi gerekli. |
| [Agetor](https://github.com/alamops/agetor) | Görsel pipeline, ajanlar arası yapılandırılmış handoff | İkisi de deneysel adaptör | Yalnız macOS Apple Silicon derlenip test edilmiş | Windows ana altyapısı için bugün uygun değil. |
| [Vibe Kanban](https://github.com/BloopAI/vibe-kanban) | Çoklu ajan panosu/worktree | Codex var; AGY için doğrulanmış yerleşik destek bulunmadı | İkincil | README projenin sonlandırıldığını söylüyor; yeni çekirdek için seçilmedi. |

Kaynaklar: [AO CLI](https://docs.orchestrator.inc/cli/), [AO ajan matrisi](https://docs.orchestrator.inc/plugins/agents/), [AWS README](https://github.com/awslabs/cli-agent-orchestrator), [AWS paket hedefleri](https://github.com/awslabs/cli-agent-orchestrator/blob/main/pyproject.toml), [Augani README](https://github.com/Augani/agent-orchestrator), [Agetor README](https://github.com/alamops/agetor), [Vibe Kanban README](https://github.com/BloopAI/vibe-kanban).

## Mevcut `agent manager` ile bağlama tasarımı

1. `manager/engine.py` içindeki LangGraph her adım için ajan, bağımlılık ve doğrulama koşulunu belirlemeye devam eder.
2. Yeni, **isteğe bağlı AO yürütücü adaptörü** `manager/providers.py` yanına eklenir. Adaptör `ao agent ls --refresh --json` ile ön kontrol yapar; proje ve ajan seçimiyle `ao spawn --name ... --project ... --agent codex|agy --prompt ...` çağırır. AO belgelerine göre `agy` için `--mode tui` gerekir. Komut biçimi: [CLI referansı](https://docs.orchestrator.inc/cli/).
3. AO oturum kimliği yerel adım kaydıyla eşleştirilir. `ao session get <id>` ile durum izlenir; `needs_input`, çıkış veya hata LangGraph adım olayına yansıtılır. Soru/izin kararını otomatik cevaplama yapılmaz. CLI, yerel masaüstü daemon'u çalışırken kullanılabilir: [CLI ve oturum belgeleri](https://docs.orchestrator.inc/cli/), [durum modeli](https://github.com/OrchestratorInc/agent-orchestrator/blob/main/docs/architecture.md).
4. Birbiriyle bağımsız yazma görevleri AO worktree'lerinde paralel yürütülebilir. Sonuçlar test ve diff incelemesinden sonra ana proje dalına alınır; aynı klasöre eşzamanlı doğrudan yazım açılmaz.
5. Yerel Ollama bu AO ajan matrisinde ayrı harness olarak görünmüyor. Mevcut Ollama planlama/metin adaptörü korunur; Ollama'nın AO içinde tam araç kullanan işçi olduğu varsayılmaz. Kaynak: [AO ajan matrisi](https://docs.orchestrator.inc/plugins/agents/).

### Örnek akış

`İstek → LangGraph planı → Codex işçisi (API) + AGY işçisi (UI) → bağımsız worktree sonuçları → test/review → birleştirme`

AO'nun REST/SSE arayüzü mimari dokümanında görülüyor, ancak herkese açık kararlı API sözleşmesi bu araştırmada doğrulanmadı. Bu nedenle pilot önce belgeli `ao` CLI komutlarıyla yapılmalı; HTTP yoluna sürüm ve sözleşme doğrulanmadan bağlanılmamalı.

## Hata ve kısıtlar

- **Garip sorular/izinler:** AO `waiting_input` ve `blocked` durumlarını gösterebilir; soruları veya sağlayıcı izinlerini ortadan kaldırmaz. [Durum modeli](https://github.com/OrchestratorInc/agent-orchestrator/blob/main/docs/architecture.md).
- **Token/kota:** Çoklu ajan aynı anda daha çok kullanım doğurabilir. AO sürüm notlarında kullanım limiti sonrası otomatik devam özelliği var; bu, limitin hiç oluşmayacağı anlamına gelmez. [Sürümler](https://github.com/OrchestratorInc/agent-orchestrator/releases).
- **Windows riski:** v0.14.2 için Windows model seçimi ve geri sürüm uyumluluğuna ilişkin açık hata kaydı var. Bu tek rapor kök nedeni kanıtlamıyor; pilotta kurulu sürümün Codex/AGY model seçimi ve eski AO verisiyle davranışı ölçülmeli. [Hata kaydı #6829](https://github.com/OrchestratorInc/agent-orchestrator/issues/6829).
- **Hazır olma denetimi:** `ao agent ls --refresh --json` kurulum/oturum durumunu gösterir; belgeler bunun başarılı oturum açmayı garanti etmediğini belirtiyor. [Ajan belgeleri](https://docs.orchestrator.inc/plugins/agents/).

## Pilot kabul koşulları

1. Kurulu AO sürümü ve CLI sürümü kaydedilir; `ao doctor` ve `ao agent ls --refresh --json` Codex/AGY için incelenir.
2. İki bağımsız küçük görev Windows'ta ayrı worktree'lerde Codex ve AGY'ye verilir; oturum durumu, soru/izin ve iptal akışı doğrulanır.
3. Bir işçide model/kota hatası ve diğerinde kullanıcı izni bekleme durumu, UI'de anlaşılır görünmelidir; sonsuz bekleme veya sessiz başarı kabul edilmez.
4. Diff ve test geçmeden sonuç ana dala alınmaz; oturum kapatıldığında kirli worktree korunur. Veri ve süreç sahipliği gözden geçirilir.

Codex için 1 ve 2 numaralı koşullar küçük salt-okunur pilotla doğrulandı. AGY'nin AO üstünden otomatik tamamlanması 2 ve 3 numaralı koşulları karşılamadı; yukarıdaki sınırlama bu nedenle uygulanmıştır. Kod değişikliği, diff birleştirme ve performans ölçümü henüz yapılmadı.
