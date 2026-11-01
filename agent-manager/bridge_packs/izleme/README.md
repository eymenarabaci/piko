# izleme (ağ/servis izleme) alan paketi

Salt gözlem paketi. Seviyeler `read` ve `draft`; `apply` ve `dangerous` bilerek
tanımlı değildir.

## Neden apply/dangerous yok?

İzleme bir **gözlem** alanıdır. Paketin işi hedefi yoklamak, telemetri okumak ve
eşik aşımını yerel bir **olay taslağına** çevirmektir. Taslak hiçbir kanala
gönderilmez; alarm açma, ticket yazma veya otomatik müdahale başka bir paketin
onaylı `apply` seviyesine aittir. Böylece yanlış alarmla dış sisteme yazma riski
bu pakette tanım gereği oluşamaz. `levels = [read, draft]` ve gerekçe
`manifest.json` içindeki `reason` alanındadır.

## read

- `probe_http(target)` — en az 3 örnek HTTP durum kodu + gecikme (httpx).
- `check_port(host, port)` — en az 3 TCP bağlantı denemesi (stdlib socket).
- `read_telemetry(path, limit)` — jsonl telemetri/log kuyruğunun son N kaydı.
- `load_thresholds(path)` — eşik tanımları JSON dosyasından okunur.

## draft

- `build_alert_rule(target, thresholds)` — alarm kuralı taslağı.
- `draft_alert_event(snapshot, thresholds, book=...)` — eşik aşımında olay notu
  ve önerilen aksiyon içeren olay taslağı; `delivery = none`, `sent = false`.
- `EventDraftBook` — aynı arıza (hedef + bulgu + eşik) için mükerrer olay
  üretmez; isteğe bağlı json dosyasıyla kalıcıdır.

## Kanıt

`evidence.py` yoklama anlık görüntüsünü döndürür: hedef, durum, en az 3 gecikme
örneği, zaman aralığı, eşik değeri ve `snapshot_sha256`.

## Hata taksonomisi

- `retryable` — anlık ağ kopması / timeout.
- `external-down` — hedef ulaşılamaz; bu bir **bulgudur**, paket hatası değildir
  ve exception olarak fırlatılmaz (`finding = true`, `error_class` alanı).
- `needs-user` — eksik/geçersiz hedef, eşik veya telemetri tanımı.

## Bütçe

`read` proaktif akış olduğu için bütçesizdir. `draft` saatlik istek tavanına ve
yoklama aralığı alt sınırına tabidir (`BudgetProfile`).

## Fixture'dan gerçek hedefe geçiş

Testlerde hedef, testin kendi içinde açıp kapattığı stdlib `http.server`'dır;
telemetri tmp jsonl dosyasıdır. Gerçek ortama geçiş için yalnızca çağrı
parametreleri değişir:

1. `probe_http` / `check_port` hedef adresini gerçek servis URL'si ve host:port
   ile değiştirin.
2. `read_telemetry` yolunu servisin/log toplayıcının jsonl dosyasına verin.
3. `load_thresholds` yolunu gerçek eşik dosyanıza verin.

Adaptör arayüzü (`read`/`draft`) ve kanıt şeması değişmez; canlı sağlayıcıya
geçiş bir adaptör/konfigürasyon değişimidir, sözleşme değişimi değildir.
