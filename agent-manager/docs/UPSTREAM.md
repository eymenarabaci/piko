# Hazır GitHub altyapısı seçimi — 25 Ekim 2026

| Proje | İncelenen özellik | Değerlendirme |
|---|---|---|
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | StateGraph, async düğümler, kalıcı yürütme | Seçildi: Python içinde CLI çağrıları doğrudan düğüm olabilir; model API sağlayıcısına bağımlılık zorunlu değildir. |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | Crew/Flow, rol tabanlı ajanlar, persistence | Alternatif; CLI merkezli yapı için rol/LLM soyutlamalarını uyarlamak ek iş olur. Bu bizim mühendislik değerlendirmemizdir. |
| [microsoft/agent-framework](https://github.com/microsoft/agent-framework) | Python/.NET ajan ve workflow örnekleri | Alternatif; bu küçük yerel Python uygulamasına LangGraph checkpoint daha doğrudan eşleşiyor. Performans üstünlüğü ölçülmedi. |
| [microsoft/autogen](https://github.com/microsoft/autogen) | AgentChat/Core ve proje durumu | README yeni projeler için Agent Framework'e yönlendiriyor; temel seçilmedi. |
| [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) | Hazır kodlama ajanı yönetim uygulaması | Tam uygulama alternatifi olarak incelendi; mevcut sohbet/plan arayüzü ve AGY kimliklerine uyarlamak ayrı uygulama fork'u gerektirir. Bu sürümde kullanılmadı. |
| [OrchestratorInc/agent-orchestrator](https://github.com/OrchestratorInc/agent-orchestrator) | Çoklu CLI ve görev/PR akışı | Hazır uygulama alternatifi olarak incelendi; bu sürümde kaynak veya bağımlılık olarak kullanılmadı. |

## Doğrudan kullanılan upstream

- **langgraph 1.2.17**, MIT: graph scheduler, bağımlılık join, async çalışma ve checkpoint replay.
- **langgraph-checkpoint-sqlite 3.1.2**, MIT: `AsyncSqliteSaver`.
- Sürüm sabitlemesi: `requirements.lock.txt`, doğrudan bağımlılıklar `pyproject.toml`.
- Upstream lisansının kurulu paket içinden alınan kopyası: `docs/licenses/LICENSE`.
- API belgeleri Context7 `/langchain-ai/langgraph` ile doğrulandı. [Resmi SQLite örnekleri](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-sqlite/README.md) incelendi; kurulu sürümle gerçek entegrasyon testleri yapıldı.

`manager/engine.py`, doğrudan upstream `StateGraph`, `START`, `END`, `compile(checkpointer=...)`, `aget_state` ve `ainvoke(..., durability='sync')` kullanır. Plan ayrı graph'ta; yürütme adımları bağımlılıkları tanımlı graph düğümlerinde çalışır. Özel DAG scheduler yeniden yazılmadı.

## Yerel uyarlama

`providers.py` ve `processes.py` CLI protokolünü, `store.py` iş/olay indeksini, `app.py` API'yi, `ui/` mevcut tasarıma benzer arayüzü sağlar. Bunlar upstream kodmuş gibi sunulmaz. Büyük bir hazır uygulamanın tümü klonlanıp yeniden markalanmadı; hazır LangGraph altyapısı sürümü sabitlenmiş bağımlılık olarak kullanıldı.

## Eski sistemden çıkarımlar

Senkron olay/snapshot SQLite yazımı, bitişe kadar tamponlanan sınırsız CLI çıktısı ve üst üste özel retry/review katmanları görüldü. Bunlar kod incelemesi bulgularıdır; donmaların ölçülmüş kök nedeni veya yeni sürüm için performans kazancı değildir. Yeni sürüm asenkron DB, deadline ve çıktı sınırı kullanır. Uzun süreli gerçek iş yükü testi ayrıca gereklidir.
