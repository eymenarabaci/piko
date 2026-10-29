"""Conservative request routing without an extra model invocation."""
from __future__ import annotations

import re


def classify_request(message: str) -> str:
    text = message.casefold().translate(str.maketrans('çğıöşü', 'cgiosu'))
    if re.search(r'\bplan(?:i)?\s+(?:yap|hazirla|olustur|cikar)|\b(?:planla|planlayalim)\b', text):
        return 'plan'
    # Questions about the current work stay conversational, even if they mention
    # several technologies or quote a long diagnostic message.
    question = re.search(r'\b(?:nedir|neden|nasil|ne durumda|ne yapildi|bitti mi|acikla|anlat|ne demek)\b', text)
    action = re.search(r'\b(?:ekle|duzelt|uygula|gelistir|yeniden yaz|entegre et|olustur|kur|yapalim)\b', text)
    if question and not action:
        return 'chat'
    broad = re.search(r'\b(?:sifirdan|komple|tamamen|tum sistem|tum proje|hepsini|tumunu|bunlari|uctan uca|mimariyi degistir|yeniden yaz)\b', text)
    domains = ('firebase', 'github', 'google drive', 'android', 'ios', 'electron',
               'kimlik dogrulama', 'odeme', 'veritabani', 'senkronizasyon')
    domain_count = sum(term in text for term in domains)
    list_count = len(re.findall(r'(?:^|\n)\s*(?:[-*]|\d+[.)])\s+', text))
    action_count = len(re.findall(r'\b(?:ekle|duzelt|uygula|gelistir|entegre et|olustur|kur)\b', text))
    if broad or list_count >= 3 or action_count >= 3 or (action and (len(message) > 700 or domain_count >= 2)):
        return 'plan'
    if re.search(r'\b(?:uygulama|sistem|platform|web sitesi)\b.{0,100}\b(?:istiyorum|gelistir|olustur|kur)\b', text):
        return 'plan'
    # Small corrections, file-scoped work and ordinary dialogue need no planner.
    if len(message) <= 400 or question or (action and list_count < 2 and domain_count < 2):
        return 'chat'
    return 'plan'
