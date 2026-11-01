
# Faz 5 kabul raporu — İkinci referans köprü paketi: `github`

Tarih: 2026-11-01. Karar: **Faz 5 (github) kabul kriteri karşılandı**.

## Paket

`bridge_packs/github/` iskeleti `tools/bridge_pack_scaffold.py` ile üretildi, sonra dolduruldu.

| Alan | Değer |
|---|---|
| Kimlik / sürüm | `github` / `0.1.0` |
| Seviyeler | `read`, `draft`, `apply` — **`dangerous` bilinçli olarak yok** |
| Idempotency | `keyed` (`idempotency_key`), push başına defter anahtarı |
| Kanıt | `github-evidence` — alanlar: `branch`, `commit_sha`, `pr_url`, `test_summary`, `source` |
| Onay | `apply: per_call_token` (tekil, kalıcı) |
| Adaptör | `manager.packs.github:adapter` (lazy) |

**Hata taksonomisi (üç sınıf ayrı):**
- `retryable`: timeout, rate-limited, network-error, push-rejected-transient
- `needs-user`: missing-input, ambiguous-request, approval-required, no-changes, dirty-worktree, unsafe-target
- `external-down`: gh-unavailable, remote-unavailable, auth-required

Sınıflandırma gerçek git çıktısından yapılır: `Could not resolve host` → retryable; `Authentication failed` → external-down; `[rejected]` → needs-user.

## Yetki düzeyleri

- **read**: `git log` geçmişi, `git diff --stat`/`--name-status` özeti, issue/PR listesi. `gh` kuruluysa `gh issue list` + `gh pr list`, değilse merge commit'leri git-only fallback olarak listelenir; **seçilen kaynak `source: gh | git-only` alanında ve kanıtta açıkça yazılır**.
- **draft**: yerel branch oluşturur/açar, dosyaları yazar, commit eder, PR başlığı+açıklaması taslağı üretir. **Push yapmaz** (testte bare remote'ta branch yokluğu doğrulanıyor). Ana dal, `../`, boşluk içeren adlar reddedilir.
- **apply**: onay token'ı + idempotency anahtarı ile `git push --set-upstream`, ardından `gh pr create` (varsa). Push başarılı olup PR açılamazsa bu dürüstçe `pr_error` olarak raporlanır; `create_pr=False` ise "PR istenmedi" yazılır.

## Akış

`iş kaydı → gate.check → ledger.begin → draft → onay → apply (push) → kanıt → add_deliverable`

- Yönetici personası (`scheduler`) `read+draft` görür; `apply` için ayrı, tek kullanımlık, içerik hash'ine bağlı onay token'ı gerekir (defterde kalıcı).
- Onaysız apply: gate `PermissionError`, **push yok, defter boş**.
- Aynı idempotency anahtarıyla tekrar: defter `applied` kalır, **ikinci push ve ikinci teslim sürümü yok**.
- Remote yoksa: `external-down`, defter `failed`, teslim kaydı oluşmaz.

## API

- `GET /api/bridges/status` — **manifest-okur** kart listesi (her paket için ayrı kod yok)
- `GET /api/bridges/github/status` — paket kartı + son github teslimi
- `GET /api/bridges/github/read?mode=history|diff|issues`
- `POST /api/bridges/github/draft`, `/approve`, `/deliver`

## Geçen testler (`tests/test_github_pack.py`, 17 test — **ağ yok**)

| Test | Kanıt |
|---|---|
| `test_manifest_is_a_complete_contract_without_dangerous` | Sözleşme tam; `levels == read,draft,apply`; `dangerous` yok |
| `test_error_taxonomy_separates_the_three_classes` | Üç sınıf ayrık, kesişim boş |
| `test_error_payloads_map_to_distinct_classes` | git çıktısından gerçek sınıflandırma |
| `test_manager_persona_is_read_and_draft_only` | Yönetici apply'ı reddedilir |
| `test_coder_persona_reads_but_cannot_draft` | Coder yalnız read |
| `test_apply_needs_a_single_use_token` | Token yoksa/tekrarı reddedilir |
| `test_dangerous_level_is_not_defined` | `dangerous` çağrısı reddedilir |
| `test_history_and_diff_are_read_from_git` | git log/diff, `source: git-only` |
| `test_issue_listing_falls_back_to_git_merge_commits` | gh yoksa merge commit fallback ve açıklama |
| `test_read_refuses_a_directory_that_is_not_a_repo` | Repo kökü olmayan dizin reddedilir |
| `test_draft_creates_branch_and_commit_but_never_pushes` | Branch+commit var, bare remote'ta branch **yok** |
| `test_draft_rejects_unsafe_branches_and_empty_commits` | main/master/../boşluk reddi |
| `test_draft_refuses_files_outside_the_workspace` | Path traversal reddi |
| `test_github_delivery_flow_end_to_end` | Draft (push yok, defter boş) → onay → push (bare remote'ta aynı sha) → `github-evidence` (branch, sha, test satırı, source) → teslim v1 (`github-change`, bridge_id=github) → replay tekrar push etmez |
| `test_github_apply_without_approval_token_does_not_push` | Reddedilen apply push etmez |
| `test_github_apply_reports_a_missing_remote_as_external_down` | İstenen sınıf: `external-down` + defter `failed` + teslim yok |
| `test_github_draft_refuses_an_empty_change` | `needs-user` |

## Geliştirici görünümü

Köprü kartı sistemine geçildi: `GET /api/bridges/status` manifestleri okur, UI her paket için kart basar — **yeni paket eklemek için koda dokunulmuyor**. Kartta paket adı+sürüm, seviyeler (apply için "tek kullanımlık onay"), kanıt formatı, yönetici persona izinleri, son teslim + kanıt. Smoke testi iki kartın da (`office`, `github`) göründüğünü doğruluyor.

## Tam koşu

- `.venv/Scripts/python.exe -m pytest -q` → **261 passed, 5 subtests** (~134 sn).
- `node --check ui/app.js` → başarılı.
- UI smoke (canlı sunucu): `bridge-cards-developer-view:2` dahil 12 kontrol; 0 konsol/istek hatası.

## Bilinen sınır

- `gh` bu makinede kurulu değil; PR açma yolu gerçek GitHub'a karşı test edilmedi, git-only fallback ve dürüst `pr_error` doğrulandı.
- `dangerous` bu pakette tanımsız: merge ve repo silme yoktur (bilinçli kapsam dışı).
- Apply hedefi her zaman remote branch + PR'dır; force-push yalnız `force=True` ile ve `--force-with-lease` kullanılarak yapılır.
