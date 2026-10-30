"""Cheap fallback for request routing when semantic model classification fails."""
from __future__ import annotations

import re
import json


_TRANSLATION = str.maketrans("çğıöşü", "cgiosu")
_ACTION = re.compile(
    r"\b(?:ekle\w*|eklen\w*|duzelt\w*|uygula\w*|gelistir\w*|"
    r"entegre\w*|olustur\w*|kur\w*|yap\w*|duzenle\w*|"
    r"guncelle\w*|tasarla\w*|paketle\w*|dagit\w*|bagla\w*|"
    r"senkronize\w*|yeniden yaz\w*|build\w*)\b"
)
_INTENT = re.compile(r"\b(?:istiyorum|olsun|olmali|gerekiyor|eklensin|guncellensin|calissin|yapilsin|olusturulsun)\b")
_QUESTION = re.compile(r"\b(?:nedir|neden|nasil|ne durumda|ne yapildi|bitti mi|acikla|anlat|ne demek)\b")
_BROAD = re.compile(
    r"\b(?:sifirdan|komple|tamamen|tum sistem|tum proje|hepsini|tumunu|"
    r"bunlari|uctan uca|mimariyi degistir|yeniden yaz|tam senkronizasyon)\b"
)
_DOMAIN_GROUPS = (
    ("github", "git repo", "commit", "pull request"),
    ("firebase", "firestore", "veritabani", "database"),
    ("google drive", "drive", "app script"),
    ("android", "mobil", "apk", "ios"),
    ("electron", "masaustu", "desktop", "pencereli"),
    ("frontend", "arayuz", "tasarim", "ekran"),
    ("kimlik dogrulama", "yetki", "admin", "rol"),
    ("profil", "kullanici", "calisan", "fotograf"),
    ("senkronizasyon", "otomatik guncelle", "dosya izleme"),
)


def classify_request(message: str, planning_level: str = "fast") -> str:
    """Return ``plan`` for substantial implementation, ``chat`` for dialogue."""
    text = message.casefold().translate(_TRANSLATION)
    if re.search(r"\bplan(?:i)?\s+(?:yap|hazirla|olustur|cikar)|\b(?:planla|planlayalim)\b", text):
        return "plan"

    if _QUESTION.search(text) and not _INTENT.search(text) and not re.search(
        r"\b(?:ekle|duzelt|uygula|gelistir|olustur|kur|yap|duzenle|guncelle|tasarla|paketle|dagit|bagla)\b", text
    ):
        return "chat"

    actions = _ACTION.findall(text)
    wants_work = bool(actions or _INTENT.search(text))
    if _QUESTION.search(text) and not wants_work:
        return "chat"

    broad = bool(_BROAD.search(text))
    domain_count = sum(any(term in text for term in group) for group in _DOMAIN_GROUPS)
    list_count = len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+", text))
    clauses = len(re.findall(r"\b(?:ve|ayrica|bir de|bide|hem|dahil)\b", text))
    file_scoped = bool(re.search(r"\b[\w.-]+\.(?:py|tsx?|jsx?|css|html|json|md)\b", text))

    if broad or list_count >= 3:
        return "plan"
    if not wants_work:
        return "chat"
    if re.search(r"\b(?:uygulama|sistem|platform|web sitesi)\b.{0,110}\b(?:istiyorum|gelistir\w*|olustur\w*|kur\w*)\b", text):
        return "plan"
    if domain_count >= 2 and len(actions) >= 1:
        return "plan"
    if _INTENT.search(text) and (clauses >= 1 or "," in text) and domain_count >= 1:
        return "plan"
    if len(actions) >= 2 and clauses >= 1:
        return "plan"
    if planning_level == "detailed" and len(actions) >= 2 and not file_scoped:
        return "plan"
    return "chat"


def needs_semantic_classification(message: str) -> bool:
    """Only implementation requests need an extra model call."""
    text = message.casefold().translate(_TRANSLATION)
    return bool(_ACTION.search(text) or _INTENT.search(text))


async def classify_with_model(message: str, planning_level: str, engine, workspace: str,
                              preferred_model: str = "") -> tuple[str, str]:
    """Assess required work and dependencies; fall back to the deterministic router."""
    fallback = classify_request(message, planning_level)
    if not needs_semantic_classification(message):
        return fallback, "clear_conversation"
    available = await engine.model_availability()
    eligible = [mid for mid, model in engine.models.items()
                if available.get(mid) and model.get("execution_supported", True)]
    selected = (preferred_model if preferred_model in eligible else
                min(eligible, key=lambda mid: (
                    engine.models[mid].get("priority", {}).get("architect", 20), mid)) if eligible else None)
    if not selected:
        return fallback, "heuristic_no_model"
    prompt = (
        "Görev yönlendirme sınıflandırıcısısın. Yalnız JSON döndür: "
        '{"kind":"chat|plan","reason":"kısa gerekçe"}. '
        "Metnin uzunluğuna göre karar verme. İstenen işin kapsamına, bağımlılıklarına, "
        "birden fazla dosya/sistem gerektirip gerektirmediğine ve doğrulama ihtiyacına bak. "
        "Salt soru/açıklama ve tek küçük düzenleme chat; çok adımlı uygulama plan. "
        f"Plan ayrıntısı: {planning_level}. Kullanıcı isteği:\n{message}"
    )
    async def ignore_event(*_):
        return None
    try:
        result = await engine.providers.run(selected, prompt, workspace=workspace,
                                            allow_writes=False, timeout=45,
                                            on_event=ignore_event)
        output = str(result.get("output", ""))
        start, end = output.find("{"), output.rfind("}")
        decision = json.loads(output[start:end + 1]) if start >= 0 and end >= start else {}
        kind = decision.get("kind")
        if kind in {"chat", "plan"}:
            return kind, f"semantic:{selected}:{str(decision.get('reason', ''))[:120]}"
    except Exception:
        pass
    return fallback, "heuristic_model_failed"
