---
role: implementer
bridges:
  github:
    levels: [read, draft, apply]
---

# github persona bağlaması

Bu blok bir persona frontmatter'ına eklenir. Rol tavanı motor kapısında ayrıca
denetlenir; `dangerous` seviyesi personaya yazılmaz, yalnız tekil onay token'ıyla
çağrılır.
