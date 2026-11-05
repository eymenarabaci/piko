"""Outcome-first conversation copy; never infer acceptance from prose."""
import re

STEP_RESULT_GUIDANCE = (
    "Sonuca 'Sonuç:' başlıklı 1-3 kısa cümleyle başla: kullanıcı açısından neyi "
    "planladığını, düzelttiğini, geliştirdiğini veya eklediğini ve davranışın nasıl "
    "değiştiğini anlat. Dosya adlarını veya kod belgelerini bu özetin konusu yapma. "
    "Yalnız plan hazırladıysan bunu belirt; uygulanmış gibi anlatma. "
    "Ardından gerekirse 'Teknik kanıt:' altında değişen dosyaları, gerçekten "
    "çalıştırılan kontrolleri ve sonuçlarını; 'Kalan:' altında engelleri belirt. "
    "Yapmadığın testi geçmiş veya doğrulanmamış işi tamamlanmış gösterme. "
)


def reported_outcome(output):
    text = re.sub(r"```[\s\S]*?```", "", str(output or ""))
    match = re.search(r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?Sonuç\s*:(?:\*\*)?\s*", text)
    if match:
        text = text[match.end():]
    selected = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if selected:
                break
            continue
        if re.match(r"^(?:#{1,6}\s*)?(?:\*\*)?(?:Teknik kanıt|Kalan|Değişen dosyalar|Testler|Dosyalar)\b", line, re.I):
            if selected:
                break
            continue
        if line.startswith(("#", "-", "*", "|", "<")):
            if selected:
                break
            continue
        selected.append(line)
        if len(" ".join(selected)) >= 500:
            break
    return " ".join(selected)[:600].strip()


def plan_result_summary(run, results):
    steps = (run.get("plan") or {}).get("steps", [])
    completed = [s for s in steps if results.get(s["id"], {}).get("status") in {"completed", "success"}]
    failed = [s for s in steps if results.get(s["id"], {}).get("status") == "failed"]
    pending = len(steps) - len(completed) - len(failed)
    outcomes = []
    for step in completed:
        outcome = reported_outcome(results.get(step["id"], {}).get("output"))
        if outcome and outcome not in outcomes:
            outcomes.append(outcome)
    lines = ["### Planın sonucu"]
    if outcomes:
        lines += ["Çalışanların bu plan için bildirdiği toplam sonuç:", "", "\n\n".join(outcomes)]
    else:
        lines += ["Bu plan için kullanıcıya dönük bir sonuç özeti kaydedilmedi. Ayrıntılardaki çıktılar incelenmeli."]
    if failed or pending:
        lines += ["", f"Plan henüz bütünüyle kapanmadı: {len(failed)} adım başarısız, {pending} adım tamamlanmamış durumda."]
    elif not steps:
        lines += ["", "Yürütülmüş plan adımı bulunmuyor."]
    lines += ["", "Bu özet çalışanların raporlarından derlenmiştir; bağımsız denetim ve test kanıtları adım ayrıntılarındadır."]
    return "\n".join(lines)
