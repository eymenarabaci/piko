# Piko Maskot API (Faz 11 — varlık + modül)

Bu belge `ui/mascot.js`, `ui/mascot.css` ve `ui/mascot-sprite.svg` üçlüsünün
sözleşmesini tanımlar. **Bu fazda `ui/app.js` / `ui/index.html` / `manager/**`
değiştirilmez.** Montaj bir sonraki oturumda yapılır.

## Dosyalar

| Dosya | Görev |
| --- | --- |
| `ui/mascot-sprite.svg` | Tek dosyada 9 durum simgesi (`<symbol id="piko-<durum>">`). JS yalnızca `<use href>` değiştirir. |
| `ui/mascot.css` | Durum bazlı renk + keyframe. `prefers-reduced-motion: reduce` altında tüm animasyonlar kapanır, durum ikonu kalır. |
| `ui/mascot.js` | Durum makinesi + DOM. Harici bağımlılık yok, ağ isteği yapmaz. |

Durumlar (9):

```
idle, konusuyor, yuruyor, calisiyor, dagitiyor, teslim, bekliyor, takildi, uyuyor
```

## Giriş noktası

```js
const mascot = initMascot(container, { spriteUrl?: string, autoSleep?: boolean });
// container: HTMLElement (maskotun basılacağı boş kapsayıcı)
```

Dönen nesne:

```js
{
  dispatch(event): Snapshot,        // olayı uygula
  setHeartbeatAlive(alive): Snapshot, // true: nabız attı, false: hemen uyut
  getState(): Snapshot,
  destroy(): void,
  root: HTMLElement
}
```

`Snapshot`:

```json
{ "state": "calisiyor", "label": "Çalışıyor", "work_id": "work-123", "event": "work.active" }
```

### dispatch sözleşmesi

```json
{ "event": "work.active", "work_id": "work-123", "reason": "Rapor yazılıyor", "pack": "github" }
```

- `event` (zorunlu): aşağıdaki olay adlarından biri veya durum adının kendisi.
- `work_id` (isteğe bağlı): kökte `data-work-id` olarak saklanır.
- `reason` (isteğe bağlı): maskotun yanındaki tek cümlelik açıklama. Verilmezse
  `takildi` ve `uyuyor` için varsayılan cümle kullanılır.
- `pack` (isteğe bağlı): yalnızca bağlam bilgisidir; şu an görselde kullanılmaz.
- `dispatch("work.active")` gibi düz string de kabul edilir.

Bilinmeyen olay yok sayılır ve mevcut durum korunur. Her `dispatch` çağrısı
nabız sayılır (`setHeartbeatAlive(true)` ile aynı etki).

## Olay → durum eşlemesi

| Olay | Durum |
| --- | --- |
| `idle`, `ready`, `wake`, `reset`, `run.idle`, `run.completed`, `run.cancelled`, `work.idle`, `work.cancelled`, `deliverable.accepted` | `idle` |
| `konusuyor`, `chat.message`, `chat.reply`, `message.user`, `message.assistant`, `assistant.speaking`, `assistant.message` | `konusuyor` |
| `yuruyor`, `run.queued`, `run.started`, `work.started`, `work.assigned`, `bridge.visit` | `yuruyor` |
| `calisiyor`, `run.planning`, `run.running`, `work.active`, `work.working`, `deliverable.draft`, `deliverable.repair_requested` | `calisiyor` |
| `dagitiyor`, `run.fanout`, `bridge.dispatch`, `pack.dispatch`, `work.distribute`, `work.pack_started` | `dagitiyor` |
| `teslim`, `deliverable.ready`, `deliverable.delivered`, `deliverable.test_passed`, `work.done` | `teslim` |
| `bekliyor`, `work.waiting`, `work.awaiting_review`, `run.waiting_quota`, `deliverable.test_pending`, `deliverable.awaiting_review`, `awaiting_review`, `awaiting_user_test` | `bekliyor` |
| `takildi`, `work.blocked`, `run.failed`, `run.error`, `run.interrupted`, `watchdog.blocked`, `health.blocked`, `deliverable.test_failed` | `takildi` |
| `uyuyor`, `sleep`, `heartbeat.silent`, `heartbeat.stale`, `daemon.stale`, `daemon.offline` | `uyuyor` |

## HealthReport eşlemesi

`GET /api/health` çıktısı doğrudan `dispatch({ "event": "health_report", ...rapor })`
şeklinde verilebilir. Modül şu alanları okur:

| HealthReport alanı | Sonuç |
| --- | --- |
| `daemon_alive === false` | `uyuyor`, sebep: "Bağlantı sessiz." |
| `kill_switch === true` | `takildi`, sebep: "Watchdog durdurdu." |
| `stale_runs` boş değil | `takildi`, sebep: "Bir iş sessiz kaldı." |
| `active_runs` boş değil | `calisiyor` |
| diğer | `idle` |

Nabız için tercih edilen yol: HealthReport'taki `daemon_alive` değerini
`setHeartbeatAlive(report.daemon_alive)` olarak iletmek. `false` anında `uyuyor`
yapar; `true` uykudaysa `idle`'a döndürür.

**120 sn kuralı:** son `dispatch` / `setHeartbeatAlive(true)` çağrısından 120.000 ms
sonra hiçbir canlılık sinyali gelmezse modül kendiliğinden `uyuyor` durumuna geçer.
Kontrol 5 sn'de bir yapılır; ağ veya sunucu başlatılmaz.

## work_record eşlemesi

`GET /api/work` ve `GET /api/work/{work_id}` kayıtlarındaki `status` alanı:

| work_record `status` | Durum |
| --- | --- |
| `open` | `idle` |
| `active` | `calisiyor` |
| `waiting` | `bekliyor` |
| `blocked` | `takildi` |
| `done` | `teslim` |
| `cancelled` | `idle` |

Deliverable `status` alanı (`deliverable.updated` / `work_record` içinde
`deliverable_status` olarak):

| deliverable `status` | Durum |
| --- | --- |
| `draft` | `calisiyor` |
| `delivered` | `teslim` |
| `accepted` | `idle` |
| `repair_requested` | `calisiyor` |
| `test_pending` | `bekliyor` |
| `test_passed` | `teslim` |
| `test_failed` | `takildi` |

Örnek:

```js
mascot.dispatch({ event: 'work_record', work_id: work.id, status: work.status });
mascot.dispatch({ event: 'deliverable.updated', work_id: work.id,
                  deliverable_status: deliverable.status, reason: deliverable.title });
```

## app.js montaj notları (SONRAKİ OTURUM)

1. `ui/index.html` içine **yalnızca** şunları ekle:
   ```html
   <link rel="stylesheet" href="/ui/mascot.css">
   <script src="/ui/mascot.js" defer></script>
   ```
2. Mevcut `#piko-mascot` bloğunun yerine boş bir kapsayıcı koy:
   ```html
   <div id="piko-mascot-stage"></div>
   ```
3. `app.js` başlangıcında:
   ```js
   const mascot = initMascot(document.getElementById('piko-mascot-stage'));
   ```
4. Mevcut `updateMascot(run)` fonksiyonunu kaldırma; içini şu köprüye çevir:
   ```js
   function updateMascot(run) {
     if (!run) return mascot.dispatch({ event: 'idle' });
     return mascot.dispatch({ event: 'work_record', work_id: run.id, status: mapRunStatus(run.status) });
   }
   ```
   `mapRunStatus` yalnızca var olan run durumlarını `work_record.status` evrenine
   çevirir (`running → active`, `awaiting_* → waiting`, `failed → blocked`,
   `completed → done`).
5. `/api/health` yoklaması zaten varsa aynı yerde:
   ```js
   mascot.dispatch({ event: 'health_report', ...health });
   mascot.setHeartbeatAlive(Boolean(health.daemon_alive));
   ```
6. Geliştirici görünümüne **hiçbir** maskot kodu sızmaz. Reduced-motion ve
   erişilebilirlik `mascot.css` / `mascot.js` içinde çözülür.

## Kısıtlar

- Etkileşimli sunucu başlatılmaz; modül tamamen istemci içidir.
- Harici kütüphane, CDN, font veya ağ isteği yoktur.
- Animasyonlar yalnızca durum değişimiyle tetiklenir; `idle` durumunda döngü yoktur.
- `prefers-reduced-motion: reduce` altında figür gizlenir, durum ikonu + etiket kalır.
