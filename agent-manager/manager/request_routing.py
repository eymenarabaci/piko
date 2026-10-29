"""Route ordinary conversation and multi-part work without a model call."""
from __future__ import annotations

import re


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
    if domain_count >= 2 and (len(text) >= 75 or len(actions) >= 1):
        return "plan"
    if len(actions) >= 2 and (clauses >= 1 or len(text) >= 150):
        return "plan"
    if len(text) >= 80 and (clauses >= 1 or "," in text) and (domain_count >= 1 or _INTENT.search(text)):
        return "plan"
    if planning_level == "detailed" and len(text) >= 65 and not file_scoped:
        return "plan"
    if planning_level == "balanced" and len(text) >= 160 and not file_scoped:
        return "plan"
    if len(text) > 400 and not file_scoped:
        return "plan"
    return "chat"
