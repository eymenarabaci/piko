---
role: implementer
bridges:
  medya:
    levels: [read, draft, apply]
---

# medya persona bağlaması

Bu blok bir persona frontmatter'ına eklenir. Rol tavanı motor kapısında ayrıca
denetlenir; `dangerous` seviyesi personaya yazılmaz, yalnız tekil onay token'ıyla
çağrılır.

Varsayılan dağılım:

| Persona | Rol | Seviyeler |
|---------|-----|-----------|
| `manager` | scheduler | `read`, `draft` — envanter okur, kurgu planı çıkarır; diske yazamaz |
| `approver` | implementer | `read`, `draft`, `apply` — onaylı küçük çıktıyı üretir |

`apply` (thumbnail / preview / kare şeridi) her çağrıda tekil onay token'ı ister;
retryable hata (zaman aşımı, bütçe) yeniden denenebilir, `needs-user` kaynak
dosyanın eksik olduğunu, `external-down` ffmpeg'in yok/çökmüş olduğunu bildirir.
