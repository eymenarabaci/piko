# medya köprü paketi

Video / görsel / ses alan paketi. Köprü sözleşmesinin altı maddesini
`manifest.json` taşır; davranış `adapter.py` içindedir ve yalnız çağrıldığında
yüklenir.

## Seviyeler

| Seviye | Davranış | Yazma |
|--------|----------|-------|
| `read` | Medya envanteri: süre, çözünürlük, codec (ffprobe varsa); yoksa dosya boyutu + uzantı + sihirli bayt; WAV için stdlib `wave` | yok |
| `draft` | Kurgu planı: sahne listesi, kırpma noktaları, altyazı taslağı, thumbnail zamanları | yok (yalnız bellekte) |
| `apply` | Onaylı küçük çıktı: thumbnail PNG, kısa preview, kare şeridi | hedef klasör |

`dangerous` yoktur. `apply` her çağrıda tekil onay token'ı ister ve defter
anahtarı çıktı dosyası başına üretilir (`idempotency_key`); aynı anahtar ikinci
kez çalıştığında dosya yeniden yazılmaz, `replayed` döner.

## Yetenek keşfi ve yedek (fallback)

`discover_capabilities()` ffmpeg/ffprobe/Pillow varlığını ölçer ve her biri için
yedek davranışı yazar. Paket hiçbir araç yokken de çökmez:

| Araç yok | Davranış |
|----------|----------|
| `ffprobe` | sihirli bayt + uzantı; WAV için stdlib `wave` |
| `ffmpeg` | thumbnail/kare şeridi stdlib PNG; preview `fallback` olarak atlanır |
| Pillow | kullanılmaz; PNG stdlib `zlib` ile üretilir |

`write_capability_manifest(path)` bu haritayı Geliştirici görünümü için diske
yazar. Manifestteki `budget` bloğu dakikada render süresi tavanını, preview süre
ve bayt tavanını taşır.

## Bütçe ve hata taksonomisi

- Varsayılan tavan: preview `15 sn / 2 MB`; `render_seconds_per_minute = 120`.
- Her ffmpeg çağrısı `DEFAULT_TIMEOUT_SECONDS` (varsayılan 30 sn) ile koşar;
  aşımda süreç öldürülür, yarım dosya silinir ve çıktı `failed` işaretlenir.
- `retryable`: zaman aşımı, codec hatası, render bütçesi aşımı.
- `needs-user`: kaynak dosya yok, onay yok, geçersiz istek.
- `external-down`: ffmpeg yok/çöktü, ffprobe okunamadı.

## Kanıt

`build_evidence()` şu nesneyi üretir: kaynak `sha256`, üretilen her dosyanın
`sha256` + yol + bayt, önizleme karesi PNG yolu (`preview_frame`) ve üç satırlık
özet (süre, boyut, kullanılan capability). Teslim kaydı ve Geliştirici ekranı
aynı nesneyi okur.

## Fixture → gerçek akış geçişi

Testler ağsız ve araçsız koşar:

1. **Fixture:** `tests/test_medya_pack_contract.py` içinde WAV stdlib `wave` ile,
   video ise tmp'ye yazılan minimal MP4 başlığı (`....ftypisom`) ile üretilir;
   `disabled_capabilities()` ffmpeg/ffprobe/Pillow yokmuş gibi davranır.
2. **Gerçek akış:** yalnızca **kaynak dosya yolu** değişir — fixture yerine
   gerçek bir `.mp4/.wav` verilir; `capabilities=...` parametresi kaldırılır ve
   `discover_capabilities()` araçları kendisi bulur.

Yani geçiş **tek dosya farkı**dır: `source` argümanı. Kod, manifest ve kanıt
şeması aynı kalır; araç varsa `capability_used` `ffprobe`/`ffmpeg`, yoksa
`magic-bytes`/`wave`/`stdlib-png` olur.

Bonus test (`test_real_ffmpeg_produces_thumbnail_preview_and_strip`) yalnız
ffmpeg/ffprobe PATH'te varsa koşar ve gerçek render'ı doğrular.

## Kullanım

```python
from bridge_packs.medya import adapter as medya

caps = medya.discover_capabilities()
inv = medya.read_media(workspace, "klip.mp4", capabilities=caps)
plan = medya.build_draft(inv, options={"scene_count": 4})
result = medya.apply_media(
    workspace, "klip.mp4", output_dir="medya-cikti",
    options={"outputs": ["thumbnail", "preview"], "preview_seconds": 10},
    inventory_value=inv, capabilities=caps)
evidence = medya.build_evidence(inv, result)
```

Paket kaydı entegratör tarafından yapılır: `medya.register(registry)` veya
`manifest()` doğrudan `BridgeRegistry.register(...)`'a verilir. Persona bloğu
`persona_binding.md` içindedir (`manager`: read+draft, `approver`: apply).
