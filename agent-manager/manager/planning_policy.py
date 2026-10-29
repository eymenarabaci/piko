"""Plan depth controls content, not model effort or worker count."""

PLANNING_GUIDANCE = {
    "fast": "Hızlı: En kısa uygulanabilir planı hazırla. Yalnız zorunlu bağımlılıkları ve bitiş kontrolünü belirt; gereksiz araştırma ve ayrı rapor adımları ekleme.",
    "balanced": "Dengeli: Uygulanabilir adımların bağımlılıklarını, önemli riskleri ve kabul ölçütlerini ilgili adımın promptunda belirt. Göreve özgü doğrulama ve entegrasyon kontrolünü kapsa.",
    "detailed": "Ayrıntılı: Kapsamı, bağımlılıkları, kritik riskleri, hata/kurtarma senaryolarını ve ölçülebilir kabul koşullarını ilgili adımın promptunda açıkla. Yalnız anlamlı kararlar için alternatifleri değerlendir; mevcut proje defterini kullan, tüm mimariyi baştan inceleme.",
}


def planning_guidance(level: str = "fast") -> str:
    return (PLANNING_GUIDANCE.get(level, PLANNING_GUIDANCE["fast"])
            + " Düzey, model düşünme yoğunluğunu veya alt ajan sayısını artırma talimatı değildir. "
              "Gereksiz adımlara bölme; güvenlik ve kullanıcının açık doğrulama isteklerini her düzeyde koru.")
