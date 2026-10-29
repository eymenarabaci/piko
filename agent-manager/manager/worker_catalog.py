"""Bounded specialist workers for CAO-backed agent delegation.

These are task roles, not claims that a model has a media-generation tool.  CAO
still runs the selected Codex, Antigravity or Ollama model; the role only adds a
focused instruction and a strict one-level delegation protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shutil


MAX_WORKER_CALLS = 2
MAX_WORKER_TASK_CHARS = 6000
MAX_WORKER_DEPTH = 1
_START = "<cao_workers>"
_END = "</cao_workers>"
_BLOCK = re.compile(re.escape(_START) + r"(.*?)" + re.escape(_END), re.DOTALL)


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    role: str
    title: str
    purpose: str
    instruction: str
    available: bool = True
    may_write: bool = False
    output_kind: str = "text"
    unavailable_reason: str = ""

    def public(self) -> dict:
        return {
            "role": self.role,
            "title": self.title,
            "purpose": self.purpose,
            "available": self.available,
            "may_write": self.may_write,
            "output_kind": self.output_kind,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class WorkerCall:
    role: str
    task: str


class WorkerRequestError(ValueError):
    """The assistant emitted an invalid or unsupported worker request."""


_SPECS = (
    WorkerSpec("planner", "Planlayıcı", "Bağımlı işleri küçük adımlara ayırır.",
               "Bir görev planı öner. Bağımlılıkları, bitiş ölçütlerini ve belirsizlikleri belirt."),
    WorkerSpec("reasoner", "Düşünsel işçi", "Zor seçenekleri ve varsayımları değerlendirir.",
               "Seçenekleri, gerekçeleri ve çözülmemiş belirsizlikleri kısa ve denetlenebilir biçimde açıkla."),
    WorkerSpec("researcher", "Araştırmacı", "Yerel kaynaklardan kanıt derler.",
               "Yalnız erişebildiğin kaynakları incele; erişmediğin belgeyi okumuş gibi gösterme. Kanıtı belirt."),
    WorkerSpec("architect", "Mimar", "Sistem sınırlarını ve arayüzleri tasarlar.",
               "Bileşenleri, veri akışını ve hata durumlarını tasarla; mevcut kodla tutarlılığı kontrol et."),
    WorkerSpec("code_writer", "Kod işçisi", "İzin varsa kodu yazar veya düzenler.",
               "İstenen sınırlı kod değişikliğini yap; yapılan dosyaları ve doğrulamayı bildir.", may_write=True),
    WorkerSpec("debugger", "Hata ayıklayıcı", "Log ve koddan kök neden arar.",
               "Belirti, kök neden kanıtı ve en küçük doğrulanabilir düzeltmeyi ayır."),
    WorkerSpec("code_reviewer", "Kod gözden geçirici", "Değişikliklerde somut hata arar.",
               "Satır ve davranış kanıtıyla gerçek hataları bildir; varsayımı olgu gibi sunma."),
    WorkerSpec("tester", "Test işçisi", "Anlamlı doğrulama senaryoları üretir.",
               "Başarı ve hata yollarını kapsayan testleri tasarla; çalıştırmadığın testi geçmiş sayma."),
    WorkerSpec("security_auditor", "Güvenlik denetçisi", "Yetki ve girdi sınırlarını inceler.",
               "Somut saldırı yüzeyi ve olası etkiyi kanıtla; uygulanabilir önlem öner."),
    WorkerSpec("technical_writer", "Yazı işçisi", "Teknik metin ve belge hazırlar.",
               "Hedef okuyucuya uygun, açık, doğrulanabilir teknik metin yaz."),
    WorkerSpec("editor", "Editör", "Metni yapı ve dil yönünden iyileştirir.",
               "Anlamı koruyarak metni netleştir; yeni olgu uydurma."),
    WorkerSpec("summarizer", "Özetleyici", "Uzun çıktıyı karar için özetler.",
               "Kritik sonuçları ve sınırları koruyarak kısa özet üret."),
    WorkerSpec("translator", "Çevirmen", "Metni anlamı koruyarak çevirir.",
               "Teknik terimleri tutarlı çevir; içerik ekleme veya çıkarma."),
    WorkerSpec("data_analyst", "Veri analisti", "Veri kalitesi ve örüntüleri inceler.",
               "Verilen veriyi analiz et; ölçüm ile yorumu ayır, eksik veriyi belirt."),
    WorkerSpec("data_generator", "Veri oluşturucu", "JSON/CSV gibi metin verisi üretir.",
               "İstenen şemaya uygun örnek veri oluştur; sentetik olduğunu açıkça belirt."),
    WorkerSpec("visualizer", "Grafikleştirici", "Mermaid veya grafik tarifi üretir.",
               "Veriyi doğru yansıtan Mermaid kodu veya grafik spesifikasyonu üret; render edilmiş görsel iddia etme.",
               output_kind="diagram_text"),
    WorkerSpec("image_brief_designer", "Resim brief işçisi", "Görsel üretim briefi yazar.",
               "Kompozisyon, stil, boyut ve kısıtları içeren uygulanabilir görsel brief hazırla; resim üretildi deme."),
    WorkerSpec("video_storyboarder", "Video storyboard işçisi", "Sahne planı ve çekim listesi yazar.",
               "Sahne sırası, süre, ses ve geçişleri içeren storyboard oluştur; video üretildi deme."),
    WorkerSpec("image_generator", "Vektör resim oluşturucu", "Sınırlı sahneden SVG vektör görsel üretir.", "",
               may_write=True, output_kind="image"),
    WorkerSpec("video_generator", "Hareketli grafik oluşturucu", "Sınırlı sahneden kısa MP4 üretir.", "",
               may_write=True, output_kind="video"),
)
WORKERS = {spec.role: spec for spec in _SPECS}


def list_workers(*, include_unavailable: bool = True) -> list[dict]:
    rows = []
    for spec in _SPECS:
        row = spec.public()
        if spec.role == "video_generator" and not shutil.which("ffmpeg"):
            row["available"] = False
            row["unavailable_reason"] = "FFmpeg bulunamadı."
        if include_unavailable or row["available"]:
            rows.append(row)
    return rows


def get_worker(role: str) -> WorkerSpec:
    spec = WORKERS.get(role)
    if spec is None:
        raise WorkerRequestError(f"Bilinmeyen CAO işçi rolü: {role}")
    if not spec.available:
        raise WorkerRequestError(f"{spec.title} kullanılamıyor: {spec.unavailable_reason}")
    if role == "video_generator" and not shutil.which("ffmpeg"):
        raise WorkerRequestError("Video işçisi için FFmpeg bulunamadı.")
    return spec


def worker_request_instructions() -> str:
    """Append to a parent agent's prompt when one-level delegation is allowed."""
    available = ", ".join(spec.role for spec in _SPECS if spec.available)
    return (
        f"Varsayılan olarak işi kendin tamamla. Yalnız ayrı uzmanlık gerektiren somut alt iş için en fazla {MAX_WORKER_CALLS} uzman çağır. Roller: "
        f"{available}. Çağrı gerekiyorsa yanıtın yalnız {_START} etiketi içinde calls JSON dizisi ve {_END} kapanışı olsun. "
        "Her çağrı yalnız role ve task alanlarını içersin; task gerçek hedefi, ilgili dosyaları/veriyi ve beklenen çıktıyı belirtmeli. "
        "Şema örneği veya genel 'kısa alt görev' metnini çağrı olarak üretme. Çağırmazsan normal yanıt ver. "
        "İşçi sonuçları sana geri iletilecek; "
        "işçiler başka işçi çağıramaz. image_generator yalnız SVG vektör görsel, "
        "video_generator yalnız kısa soyut MP4 üretir; bu ikisi dosya yazma izni gerektirir."
    )


def extract_worker_calls(output: str, *, depth: int = 0) -> list[WorkerCall]:
    """Parse an explicit, bounded worker request; ordinary output returns []."""
    if depth >= MAX_WORKER_DEPTH:
        return []
    if not isinstance(output, str):
        raise WorkerRequestError("İşçi çağrısı metin olmalı.")
    matches = list(_BLOCK.finditer(output))
    if not matches:
        if _START in output or _END in output:
            raise WorkerRequestError("CAO işçi çağrısı etiketi tamamlanmamış.")
        return []
    if len(matches) != 1 or output[:matches[0].start()].strip() or output[matches[0].end():].strip():
        raise WorkerRequestError("İşçi çağrısı tek başına bir protokol bloğu olmalı; alıntı veya açıklama içinden çalıştırılamaz.")
    raw = matches[0].group(1).strip()
    if len(raw) > MAX_WORKER_CALLS * (MAX_WORKER_TASK_CHARS + 100):
        raise WorkerRequestError("İşçi çağrısı çok büyük.")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise WorkerRequestError("İşçi çağrısı geçerli JSON değil.") from exc
    if not isinstance(data, dict) or set(data) != {"calls"}:
        raise WorkerRequestError("İşçi çağrısı yalnız calls dizisi içermeli.")
    rows = data["calls"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_WORKER_CALLS:
        raise WorkerRequestError(f"Bir seferde 1-{MAX_WORKER_CALLS} işçi çağrısı yapılabilir.")
    calls = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"role", "task"}:
            raise WorkerRequestError("Her işçi çağrısında yalnız role ve task bulunmalı.")
        role, task = row["role"], row["task"]
        if not isinstance(role, str) or not isinstance(task, str) or not task.strip() or len(task) > MAX_WORKER_TASK_CHARS:
            raise WorkerRequestError("İşçi rolü veya görev metni geçersiz.")
        if task.strip().casefold() in {'kısa alt görev', 'alt görev', 'task', '...', 'görev açıklaması'}:
            raise WorkerRequestError("İşçi görevi somut hedef içermeli; şema yer tutucusu çalıştırılamaz.")
        if any(call.role == role and call.task == task.strip() for call in calls):
            raise WorkerRequestError("Aynı işçi görevi birden fazla çağrılamaz.")
        get_worker(role)
        calls.append(WorkerCall(role, task.strip()))
    return calls


def worker_prompt(spec: WorkerSpec, task: str, *, allow_writes: bool) -> str:
    if not spec.available:
        raise WorkerRequestError(spec.unavailable_reason)
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_WORKER_TASK_CHARS:
        raise WorkerRequestError("İşçi görev metni boş veya çok uzun.")
    permission = (
        "Bu işçi için dosya düzenleme izni var; yalnız görev kapsamındaki dosyalara dokun."
        if allow_writes and spec.may_write else
        "Salt okunur çalış; dosya değiştirme."
    )
    return (
        f"CAO uzman rolün: {spec.title} ({spec.role}).\n"
        f"Görev ilkesi: {spec.instruction}\n"
        f"İzin: {permission}\n"
        "Başka işçi çağırma. Sonucu kısa tut: yapılan iş, dosyalar, doğrulama kanıtı ve kalan engel; uzun rapor üretme.\n"
        f"Alt görev:\n{task.strip()}"
    )
