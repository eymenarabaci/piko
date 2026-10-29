---
name: 15-security-performance-auditor
description: "WebGL/React performansı, gömülü bellek ve yarış hataları, Firebase/API erişim güvenliği için kanıtlı denetim yap."
---

# security performance auditor

Bu skill ilgili alan işi istendiğinde uygulanır. Çıktıları kullanıcının kapsamına göre üret; dış sistemlere yayınlama veya üretim değişikliği için mevcut yetki sınırlarını koru. Sürüm bağımlı API'leri resmi kaynak ve mevcut araçlarla doğrula.

## Mühendislik kuralları

1. Denetim kapsamını uygulama/sürüm/ortam ve izin verilen erişimlerle belirle; salt denetim talebini üretim değişikliği yetkisi sayma.
2. Her bulgu için tetikleyici, etki, konum, kanıt, öneri ve doğrulama adımını kaydet.
3. WebGL sahnesinde renderer.info.memory ve render çağrılarını giriş/çıkış tekrarları boyunca ölç; tek anlık sayıya dayanma.
4. Geometry, material, texture, render target ve kontrol/listener ownership haritası çıkar.
5. Sahne kaldırılırken yalnız sahip olunan kaynakları dispose et; paylaşılan texture'ı erken silme.
6. Material.dispose() çağrısının texture.dispose() yerine geçmediğini gözet.
7. GLTF loader cache ve React StrictMode etkisini ölçümde ayır; geçici cache büyümesini sızıntı diye raporlama.
8. React Profiler ile commit sürelerini ve yeniden render sebebini ölç; her bileşene körlemesine memo ekleme.
9. Zustand selector veya context granülerliğini ölçüm sonucuyla iyileştir; referans kararlılığını doğrula.
10. CPU long task, GPU draw-call, fill-rate ve ağ gecikmesini ayrı darboğaz olarak değerlendir.
11. Performans karşılaştırmasını aynı cihaz, sahne, yük, warmup ve örneklem koşullarında yap.
12. C/C++ buffer işlemlerinde uzunluk, kapasite, null terminator ve integer overflow birlikte denetlensin.
13. Packet parser için malformed, truncated, aşırı uzun ve sınır değerli girdileri test et.
14. Stack watermark, task stack ve ISR stack kullanımını hedef donanımda ölç; yalnız masaüstü ASan sonucuna güvenme.
15. Paylaşılan state için ISR/task ve çok çekirdek erişimini incele; volatile değişkeni senkronizasyon sanma.
16. Critical section, atomic ve mutex seçimini hedef mimariye göre yap; ISR içinde bloklayan mutex kullanma.
17. DMA buffer ömrü, hizalama ve cache coherency gereksinimlerini MCU referans kılavuzundan doğrula.
18. Firebase Rules ile Admin SDK/API yetkilerini ayrı denetle; tenantlar arası object erişimi için negatif test yap.
19. API'de authentication, authorization, input validation, rate limit ve idempotency rollerini ayrı incele.
20. Browser'a gönderilen env değişkenlerini ve build çıktısını sır sızıntısı için tara; gerçek secret değerlerini rapora kopyalama.
21. Bağımlılık güvenlik bulgusunu kurulu sürüm ve kullanılan kod yolu ile ilişkilendir; erişilmeyen advisory'yi çalışır exploit gibi sunma.
22. Düzeltme sonrasında başlangıç tetikleyicisini tekrar çalıştır; doğrulanmayan bulguyu kapandı olarak işaretleme.

## Uygulanabilir şablon

Şablonu proje sürümüne ve gereksinimlerine uyarlayarak doğrula; örnek tek başına üretim onayı değildir.

```typescript
// Paylaşılmayan kaynaklar için yaşam döngüsü kaydı.
import type { BufferGeometry, Material, Texture } from "three";
export function ownResources() {
  const owned = new Set<BufferGeometry | Material | Texture>();
  return {
    add<T extends BufferGeometry | Material | Texture>(resource: T): T {
      owned.add(resource);
      return resource;
    },
    dispose() {
      for (const resource of owned) resource.dispose();
      owned.clear();
    }
  };
}
// Sahne unmount işleminde listener, animation loop ve render target da temizlenir.
// Paylaşılan asset cache kaynaklarını buraya kaydetme.
```

## Yasaklı hatalar

Kanıtsız kritik etiket, secret değerini loglama, volatile ile race çözme, ISR'da mutex bekleme, tüm texture'ları sahiplik denetlemeden dispose etme ve ölçülmeyen performans artışı iddiası yasaktır.

## Teslim ve doğrulama

Kaynak dosyaları, gerekli bağımlılık/sürüm bilgilerini, çalıştırılan kontrollerin sonucunu ve çözülemeyen kısıtları birlikte teslim et. İlgili görünür çıktıyı açıp kontrol et; ölçülmeyen veya çalıştırılmayan kontrolü başarılı diye sunma.

