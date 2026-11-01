# veri — veri/bilim köprü paketi

Yerel CSV/TSV/JSON veri kümelerini profiller, analiz raporu taslağı üretir ve
onaylı raporu çalışma alanı içine yazar. Ağ veya gerçek dış servis çağrısı yoktur.

## Sözleşme

| Madde | Bu pakette |
|---|---|
| İzin seviyeleri | `read`, `draft`, `apply` (`dangerous` yok) |
| İdempotency | `keyed` / `idempotency_key` |
| Kanıt formatı | `veri-evidence`: dosya listesi, her dosyanın `sha256`'sı, üç satır özet, okunur rapor önizlemesi |
| Rol tavanı | `reviewer`/`scheduler` en fazla `read` / `read+draft`; `apply` yalnız `implementer` + tekil onay |
| Hata taksonomisi | `retryable` / `needs-user` / `external-down` |
| Onay politikası | `apply` = `per_call_token` |

`dangerous` neden yok: adaptör yalnız **yeni** rapor dosyaları üretir; kaynak
veriyi silmez, taşımaz veya sessizce üzerine yazmaz. Hedefte aynı dosya varsa
varsayılan davranış `needs-user` / `target-exists` hatasıdır.

## Seviyeler

- **read** — çalışma alanı içindeki tek bir CSV/TSV/JSON dosyasını okur; kolon
  türü, dolu/boş/tekil sayıları, sayısal min/max/ortalama ve sha256 döndürür.
  Çalışma alanı dışına çıkan yollar reddedilir.
- **draft** — Markdown rapor + şema CSV'si (+ istenirse gruplama CSV'si) bellekte
  üretir; diske hiçbir şey yazmaz. `group_by`, `metric`, `value_column` desteklenir.
- **apply** — onaylı taslağı hedef klasöre yazar; kanıt nesnesini döndürür.

## Varsayılan bütçe profili

`budget_profile.json` (`veri-default`, kapsam `bridge:veri`):

- `token_limit`: 40000
- `seconds_limit`: 900
- `repairs_limit`: 2

`adapter.configure_budget(ledger)` bu profili `OperationLedger`'a uygular. İş ve
küresel tavanlar motor tarafında ayrıca uygulanır.

## Canlı sağlayıcıya geçiş

Şu anki sağlayıcı **yerel dosya sistemidir**: `adapter.load_rows()` ve
`adapter.read_dataset()` çalışma alanındaki fixture dosyalarını okur. Gerçek bir
veri ambarı/API'ye geçiş **tek dosyalık adaptör değişimidir**:

- yalnız `bridge_packs/veri/adapter.py` içindeki `load_rows` / `_load_bytes`
  fonksiyonlarını canlı bağlayıcıyla değiştirin;
- `manifest.json`, `flow.py`, `evidence.py`, `persona_binding.md` ve
  `tests/test_veri_pack_contract.py` değişmez;
- canlı adaptör `RetryableVeriError`, `ExternalVeriError` ve `VeriError`
  sınıflarına eşleme yapmaya devam etmelidir;
- testler ağ kullanmaz; canlı adaptör için ayrı, kimlik bilgisi gerektirmeyen
  yerel bir fixture sunucusu `fixtures/` altında tutulmalıdır.

## Kayıt

Bu paket kataloğa `adapter.manifest()` çıktısıyla girer. Kayıt defteri/`shipped`
listesi entegratörün işidir; paket kendi manifest'i dışında hiçbir yeri değiştirmez.
