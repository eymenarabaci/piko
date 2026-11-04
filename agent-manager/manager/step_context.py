"""Carry the user contract across program phases without expanding step authority."""
from datetime import datetime, timezone

def original_task_context(run: dict, *, can_write: bool) -> str:
    original = str(run.get("prompt") or "").strip()
    active = str(run.get("active_goal") or "").strip()
    if not original or not active or original == active:
        return ""
    # Preserve both the opening goal and trailing constraints for oversized requests.
    if len(original) > 12000:
        original = original[:8000] + "\n[Uzun isteğin orta bölümü kısaltıldı]\n" + original[-4000:]
    authority = (
        "Ana istekte iş boyunca sürdürülmesi istenen durum/plan/karar kayıtlarını "
        "bu adımın gerçek ilerlemesi ve doğrulanmış kanıtlarıyla güncelle. "
        "Uzun adımlarda anlamlı kontrol noktalarında kaydet; teslimi bekleme. "
        "Başarı veya test sonucu uydurma; eski kayıtları yeni ölçüm gibi sunma."
        if can_write else
        "Salt okunur yetkin değişmez; kayıt dosyalarını değiştirme. "
        "Güncel olmayan durum/plan kayıtlarını ve gerekli güncellemeyi sonuçta bildir."
    )
    runtime = (
        f"\nMotorun gerçek anlık verisi: UTC={datetime.now(timezone.utc).isoformat()}; "
        f"program_index={run.get('program_index', 0)}; "
        f"current_steps={run.get('current_steps', [])}. "
        "Bu saat gözlem zamanıdır; gelecekteki plan saatini gerçekleşmiş olay saati olarak yazma. "
        "Görev dosyalarındaki başarı iddiaları bu motor verisinin yerine geçmez."
    )
    return ("\n\n=== ANA KULLANICI İSTEĞİ VE SÜREKLİ ŞARTLAR ===\n" + original + runtime
            + "\nYalnız mevcut adımı uygula; ana isteğin sonraki dilimlerini üstlenme. " + authority)
