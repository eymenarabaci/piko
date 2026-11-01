# Faz 5 KAPANIŞ kabul raporu — Üç referans köprü paketi

Tarih: 2026-11-01. Karar: **Faz 5 kapandı**. Üç paket de sözleşmeye uygun, kayıtlı, manifest-okur kartlarla listeleniyor ve uçtan uca doğrulandı.

## 1. Sözleşme tablosu (üç paket)

| Alan | `office` | `github` | `communication` |
|---|---|---|---|
| Sürüm | 0.1.0 | 0.1.0 | 0.1.0 |
| Seviyeler | `read`, `draft`, `apply` | `read`, `draft`, `apply` | `read`, `draft`, `apply` |
| `dangerous` | yok | **bilinçli yok** (merge/silme) | **bilinçli yok** (silme/şifre) |
| Idempotency | `keyed` | `keyed` (push başına) | `keyed` (gönderim başına) |
| Onay (`apply`) | `per_call_token` | `per_call_token` | `per_call_token` |
| Kanıt formatı | `office-evidence` | `github-evidence` | `communication-evidence` |
| Kanıt alanları | `files`, `sha256`, `summary` | `branch`, `commit_sha`, `pr_url`, `test_summary`, `source` | `message_id`, `recipients`, `preview_sha256`, `kind` |
| Hata sınıfları | retryable / needs-user / external-down | retryable / needs-user / external-down | retryable / needs-user / external-down |
| Adaptör (lazy) | `manager.packs.office:adapter` | `manager.packs.github:adapter` | `manager.packs.communication:adapter` |
| Persona tavanı | manager: read+draft | manager: read+draft; coder: read | manager: read+draft; proactive: **read** |
| Dış bağımlılık | yok (yerel dosya) | git; `gh` opsiyonel | yok (SQLite fixture) |

Üç paketin ortak akışı: `persona → gate.check → ledger.begin → adaptör → kanıt → work_record.add_deliverable`. `apply` her pakette tekil, kalıcı onay token'ı ve işlem başına defter anahtarı ister; aynı anahtarın tekrarı **ikinci dış etki üretmez**.

## 2. Kanıt örnekleri (gerçek testlerden)

**office**
```json
{"format": "office-evidence", "ok": true,
 "files": [{"path": "work/office-smoke/ui-smoke-ofis-raporu.csv",
            "sha256": "81aff302db1c5f82…", "bytes": 21},
           {"path": "work/office-smoke/ui-smoke-ofis-raporu.md",
            "sha256": "2a83cf915eec65c9…", "bytes": 150}],
 "summary": "# UI smoke ofis raporu / _Üretildi: 2026-11-01 … / ## Özet"}
```

**github** (yerel bare remote, ağ yok)
```json
{"format": "github-evidence", "ok": true, "source": "git-only",
 "branch": "piko/ozellik", "commit_sha": "<40 hane>",
 "pr_url": "", "pr_error": "PR istenmedi (create_pr=False).",
 "test_summary": "5 passed in 0.31s"}
```
Doğrulama: bare remote'taki `piko/ozellik` sha'sı teslim edilenle **aynı**; replay'de değişmiyor.

**communication** (SQLite fixture)
```json
{"format": "communication-evidence", "ok": true, "kind": "mail",
 "message_id": "<outbox id>",
 "recipients": ["ayse@example.test", "mehmet@example.test"],
 "preview_sha256": "<64 hane>", "duplicate": false}
```
Doğrulama: fixture outbox'ında tam **1** satır; aynı anahtarla tekrar denemede yine 1 satır (hem defter hem sağlayıcı düzeyinde).

## 3. Proaktif kapı (yeni sert kural)

`Engine.authorize_bridge(..., proactive=True)` yalnızca paketin `PROACTIVE_LEVELS` kümesindeki seviyeleri geçirir; `communication` için bu `{"read"}`. `draft/apply/dangerous` çağrıları **gate ve deftere dokunmadan** `PermissionError` ile reddedilir. Test: `test_proactive_flow_may_only_read` → reddedilen üç çağrı sonrası `ledger.list() == []`.

Proaktif arka planın gördüğü tek veri: `proactive_read()` → gelen kutusu özeti + bugünkü takvim (3 satır özet).

## 4. Geçen testler

| Dosya | Test sayısı | Kapsam |
|---|---|---|
| `tests/test_office_pack.py` | 10 | sözleşme, registry, yönetici read+draft, tek kullanımlık token, okuma/arama/dizin sınırı, draftsız disk, uçtan uca teslim + replay, alan dışı hedef, hata eşlemesi |
| `tests/test_github_pack.py` | 17 | sözleşme + `dangerous` yokluğu, üç hata sınıfı, manager/coder tavanı, token, `git log/diff`, gh-yok fallback, repo kökü reddi, branch/commit (push yok), güvenli olmayan branch/dosya reddi, uçtan uca push+kanıt+teslim, onaysız apply, remote yok → external-down, boş değişiklik |
| `tests/test_communication_pack.py` | 17 | sözleşme + `dangerous` yokluğu, hata sınıfları, manager read+draft, tekil token, **proaktif yalnız read**, inbox/takvim, mail & toplantı taslağı (outbox'a yazmaz), güvenli alıcı, uçtan uca 3 mail → draft → onay → apply → outbox kanıtı → teslim, sağlayıcı düzeyi mükerrer koruma, onaysız apply, toplantı uçtan uca, HTTP kart listesi + proaktif blok |
| `tests/test_driver_wiring.py` | 9 | sürücü/kilit/köprü-defter bağı |
| `tests/test_parallel_queue.py` | 2 | Faz 3 kabul |
| diğerleri (engine, store, triggers, metrics, operations, ...) | ~223 | önceki fazlar regresyonu |

**Tam koşu:** `.venv/Scripts/python.exe -m pytest -q` → **278 passed, 5 subtests** (~125 sn).
**UI smoke** (canlı sunucu): `bridge-cards-developer-view:3` dahil 12 kontrol, 0 konsol/istek hatası — üç kart da koda dokunulmadan listelendi.

## 5. Manifest-okur kart sistemi

`GET /api/bridges/status` `packs.shipped_catalog()` üzerinden her manifesti okur; UI `bridgeCard()` tek bir jenerik bileşenle **her paketi** basar (ad, sürüm, seviyeler, apply onay işareti, kanıt formatı, yönetici persona izni, son teslim + kanıt). Dördüncü paket eklendiğinde tek yapılacak: manifest + `SHIPPED` kaydı — UI kodu değişmez.

## 6. Kapsam dışı / bilinen sınırlar

- `dangerous` hiçbir pakette tanımlı değil; merge, repo silme, mail silme ve şifre işlemleri yok.
- `gh` bu makinede kurulu değil: PR açma yolu gerçek GitHub'a karşı denenmedi; git-only fallback ve dürüst `pr_error` doğrulandı.
- `communication` sağlayıcısı SQLite fixture'dır. Canlı IMAP/CalDAV geçişi **yalnız adaptör değişimi** (fixture'daki `inbox_summary/today_calendar/send/propose_meeting` sözleşmesini uygulayan sınıf).
- Dış servis kimlik doğrulaması, gerçek gönderim ve gerçek takvim yazımı test edilmedi.
- E-posta gövdesi düz metindir; HTML/ek parçalama kapsam dışı.
