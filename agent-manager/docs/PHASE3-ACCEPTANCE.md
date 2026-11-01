
# Faz 3 kabul raporu — paralel kuyruk, bekleme ve slot kapasitesi

Tarih: 2026-11-01. Karar: **Faz 3 kabul kriteri karşılandı** (aşağıdaki kanıtlarla).

## Kabul senaryosu

Üç iş, tek sürücü tick'i ve iki run slotu ile çalıştırıldı:

| İş | Durum | Davranış |
|---|---|---|
| A | `waiting` (kullanıcı kararı) | Run açılmaz, sürücü atlar, bekleme bozulmaz |
| B | `open`, çalışma alanı dış sahip tarafından TTL'li kilitli | İlk denemede `queued` + `resource_wait`, TTL dolunca devralır ve biter |
| C | `open`, hızlı ofis işi, kendi çalışma alanı | A beklerken ve B kilidi almadan tamamlanır |

## Geçen testler

1. `tests/test_parallel_queue.py::test_waiting_does_not_block_queue_and_lock_ttl_is_taken_over`
   - Tek tick'te `started == 2`, `skipped == 0` → **bekleyen A slotu çürütmüyor**, sıradaki `open` işler alınıyor.
   - A için run oluşmadı ve durumu `waiting` kaldı.
   - C, A hâlâ `waiting` iken ve B kilidi dışarıdayken `completed` oldu.
   - Kilit TTL'i dolunca B `completed` oldu; olay kaydında `resource_wait` var.
   - `providers.max_active == 2` ve `engine.slot_max_observed <= max_concurrent_runs == 2` → **eşzamanlılık tavanı aşılmadı**.
   - `metrics.runtime()` içinde `max_concurrent_runs=2`, `slot_max_observed=2`, `slot_wait_count >= 2`, ortalama bekleme ≥ 0.
2. `tests/test_parallel_queue.py::test_reminders_follow_one_four_twenty_four_rule`
   - 0 sn: hatırlatma yok; 2 sn: 1; 5 sn: 2; 25 sn: 3; aynı anlarda tekrar tick → yine 3 (tekilleştirme).
   - 80 sn: karar penceresi dolar, iş **bir kez** `blocked` olur, tek `blocker` bildirimi.

## Destekleyen testler

- `tests/test_driver_wiring.py` (9 test): tek run, tamamlanan run'ı yeniden başlatmama, kayıp run, waiting/blocked atlama, hatırlatma tekilliği, kalıcı onay token'ı, `apply`→defter, kilit beklemesi.
- `tests/test_scheduling.py`: sıralama, sessiz saat, kaçırılan çalışma, proaktif tekilleştirme.
- `tests/test_metrics.py` + `tests/test_engine.py` teslim/metrik testi: görev ölçümü ve teslim kaydı.
- `tests/golden_restart.py`: iki sert `taskkill` sonrası `resumed: 1`, tek run, `recovery_questions: 1`, `daemon_alive: true`.

## Tam koşu

- `.venv/Scripts/python.exe -m pytest -q` → **234 passed, 5 subtests**, ~82 sn.
- `node --check ui/app.js` → başarılı.
- UI smoke (`tests/ui-work-smoke.cjs`, canlı sunucu): bootstrap, Piko/Geliştirici geçişi, bildirim diyaloğu + tercihleri, maskot durumu, iş kaydı sekmesi, **slot metrikleri (`slot-metrics-api:2`)**, teslim kabulü, mobil taşma yok; 0 konsol/istek hatası.

## Faz 3 kriterinin tam karşılanma gerekçesi

- "Bekleyen iş kuyruğu durdurmaz" → A `waiting` iken aynı tick'te B ve C başladı; C A beklemesi sürerken bitti.
- "Kaynak çakışması ikinci run'ı bekletir, sonra devralır" → B ilk denemede kilit alamadı (`resource_wait`), TTL sonrası devraldı ve tamamlandı.
- "Eşzamanlılık tavanı korunur" → gözlenen en yüksek eşzamanlılık 2; 2 sınırı hiç aşılmadı.
- "Hatırlatma 1/4/24 sn" → üç aralıkta birer kez, tekilleştirilmiş; 72 sn'de tek blok.
- "Geliştirici ekranı için sayaçlar" → `GET /api/metrics` `slots` ve `runtime` (doluluk %, bekleyen, kabul edilen, ortalama bekleme) döndürüyor; iş kaydı sekmesinde "Sistem kapasitesi" kartı render ediliyor.

## Bilinen sınır

Ölçümler süreç içi sayaçlardır (`data/metrics.sqlite` anlık gauges/counters); süreç yeniden başladığında doluluk gauges sıfırdan başlar, kümülatif sayaçlar kalıcıdır. Ayrı süreçte çalışan CLI sürücüsü aynı veri klasörünü paylaşırsa kaynak kilidi sıraya sokar; metrikler her süreç için ayrı sayılır.
