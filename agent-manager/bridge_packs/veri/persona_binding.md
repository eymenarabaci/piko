---
role: implementer
bridges:
  veri:
    levels: [read, draft, apply]
budget:
  profile: veri-default
  scope: bridge:veri
  token_limit: 40000
  seconds_limit: 900
  repairs_limit: 2
---

# veri persona bağlaması

Bu blok bir persona frontmatter'ına eklenir. Rol tavanı motor kapısında ayrıca
denetlenir; `dangerous` seviyesi personaya yazılmaz, yalnız tekil onay token'ıyla
çağrılır. Bu pakette `dangerous` yoktur.

- `read` + `draft` yönetici/arka plan personasına açıktır.
- `apply` yalnız onaylayıcı personada ve tek kullanımlık onay token'ıyla çalışır.
- Varsayılan bütçe profili `budget_profile.json` ile aynıdır; kapsam `bridge:veri`.
