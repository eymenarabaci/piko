
# Faz 5 kabul raporu — İlk referans köprü paketi: `office`

Tarih: 2026-11-01. Karar: **Faz 5 kabul kriteri karşılandı**.

## Paket

`bridge_packs/office/` iskeleti `tools/bridge_pack_scaffold.py` ile üretildi, sonra sözleşmeye göre dolduruldu:

| Alan | Değer |
|---|---|
| Kimlik / sürüm | `office` / `0.1.0` |
| Seviyeler | `read`, `draft`, `apply` |
| Idempotency | `keyed` (`idempotency_key`) |
| Kanıt | `office-evidence` — alanlar: `files`, `sha256`, `summary` |
| Hata taksonomisi | retryable: timeout, rate-limited, partial-write · needs-user: missing-input, ambiguous-request, approval-required, unsafe-target · external-down: connection-refused, service-unavailable, filesystem-unavailable |
| Onay | `apply: per_call_token` (tekil, kalıcı) |
| Adaptör | `manager.packs.office:adapter` (lazy) |

Yetki düzeyleri:
- **read**: çalışma alanı içinde dosya okuma (sha256 + 3 satır özet), metin arama (dosya adı + ilk eşleşen satır), dizin özeti (tür/byte/en büyük). Yol çalışma alanı dışına çıkarsa reddedilir.
- **draft**: md + csv üretimi bellekte; xlsx yalnız `openpyxl` kuruluysa, yoksa not düşüp md/csv ile devam. Diske hiç yazmaz.
- **apply**: onaylı taslağı hedef klasöre yazar; her dosya için `path`, `sha256`, `bytes` döndürür ve 3 satırlık içerik özeti üretir.

Yönetici personası (`scheduler`) yalnız `read + draft` görür; `apply` ayrı onay token'ı ister. Onay token'ı tek kullanımlıktır ve `data/operations.sqlite` içindeki `approvals` tablosunda kalıcıdır (yeniden başlatma sonrası da tüketilmiş sayılır).

## Akış (gerçek çağrı zinciri)

`persona(office) → gate.check → ledger.begin → adaptör → kanıt → work_record.add_deliverable`

- `manager/packs/office.py`: adaptör, kanıt üretici, hata eşleme, slug (Türkçe harf katlama).
- `manager/packs/office_flow.py`: `draft → onay → apply` akışı; `apply` çağrısı `Engine.authorize_bridge` üzerinden gider, bu da `BridgeGate.authorize` + `OperationLedger.begin` kullanır.
- `manager/packs/registry.py`: `register_shipped` ile paket uygulama açılışında registry'ye eklenir (`app.state.registered_packs == ["office"]`).
- Tekrar oynatma: aynı `idempotency_key` ikinci kez yazmaz; defter `replayed` döndürür, yeni teslim sürümü oluşmaz.
- Hata: hedef çalışma alanı dışındaysa defter `failed` olur, teslim kaydı oluşmaz, dürüst hata sınıfı döner.

## API

- `GET /api/bridges` — kayıtlı paketler + `shipped`
- `GET /api/bridges/office/status` — paket kartı: ad, sürüm, seviyeler, onay politikası, kanıt formatı, personalar ve son teslim + kanıt
- `POST /api/bridges/office/draft` — yönetici seviyesi taslak (diske yazmaz)
- `POST /api/bridges/office/approve` — taslak hash'ine bağlı tekil onay
- `POST /api/bridges/office/deliver` — onaylı yazma; kanıt + sürümlü teslim döndürür

## Geçen testler (`tests/test_office_pack.py`, 10 test)

| Test | Kanıt |
|---|---|
| `test_manifest_is_a_complete_bridge_contract` | Altı zorunlu sözleşme alanı, seviyeler, kanıt alanları, `per_call_token`, `keyed` |
| `test_pack_registers_and_exposes_three_levels` | Registry kaydı + adaptör tembelliği (`is_loaded == False`) |
| `test_manager_persona_is_read_and_draft_only` | Yönetici `read`/`draft` geçer, `apply` **reddedilir** |
| `test_apply_requires_a_single_use_approval_token` | Token'sız apply reddedilir; aynı token ikinci kez kullanılamaz |
| `test_read_search_and_directory_summary_stay_inside_workspace` | Okuma/sha256, arama satırı, dizin özeti, `../` reddi |
| `test_draft_writes_nothing_and_falls_back_without_xlsx` | Disk boş kalır, xlsx yoksa md/csv'ye düşer, 3 satır özet |
| `test_draft_requires_a_title` | `needs-user` hata sınıfı |
| `test_office_delivery_flow_end_to_end` | Onaysız apply yazmaz ve deftere yazmaz; onaylı apply yazar, dosya hash'leri disktekiyle eşleşir, `applied` defter kaydı, teslim v1 + kanıt satırları, aynı anahtarla tekrar oynatma yeni sürüm açmaz ve elle değiştirilmiş dosyayı ezmez |
| `test_office_refuses_a_target_outside_the_workspace` | Çalışma alanı dışı hedef reddedilir, defter `failed`, teslim yok |
| `test_error_taxonomy_classes_are_mapped` | retryable / external-down / needs-user eşlemesi |

## Geliştirici görünümü

`İş kaydı` sekmesinin yanında **Köprüler** sekmesi ve tek `office` kartı: paket adı+sürüm, seviyeler (apply için "tek kullanımlık onay" işareti), kanıt formatı, yönetici personasının izinleri, "Son teslim" (dosya listesi + kanıt satırları + tarih). Sekme seçili bir run olmadan da açılır.

## Tam koşu

- `.venv/Scripts/python.exe -m pytest -q` → **244 passed, 5 subtests** (~84 sn).
- `node --check ui/app.js` → başarılı.
- UI smoke (`tests/ui-work-smoke.cjs`, canlı sunucu): `bridge-registry:office`, `office-delivery-evidence`, `bridge-card-developer-view` dahil 12 kontrol; 0 konsol/istek hatası.

## Bilinen sınır

- xlsx üretimi bu venv'de `openpyxl` yoksa devre dışı; sessizce md/csv ile devam edilir (test bunu doğruluyor).
- `office` köprüsü yalnız yerel dosya sisteminde çalışır; dış servis (GitHub/Drive/e-posta) yoktur — bu paketin tasarım gereği.
- Teslim kaydı `work_items` içindeki işe bağlıdır; `work_id` verilmezse yalnız kanıt ve defter kaydı üretilir, teslim kaydı oluşmaz.
