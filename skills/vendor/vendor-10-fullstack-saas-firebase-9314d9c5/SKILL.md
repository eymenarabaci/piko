---
name: 10-fullstack-saas-firebase
description: "React/Vite/Next.js ve Firebase ile güvenli SaaS, gerçek zamanlı 3D ürün özelleştirici ve durum yönetimi geliştir."
---

# fullstack saas firebase

Bu skill ilgili alan işi istendiğinde uygulanır. Çıktıları kullanıcının kapsamına göre üret; dış sistemlere yayınlama veya üretim değişikliği için mevcut yetki sınırlarını koru. Sürüm bağımlı API'leri resmi kaynak ve mevcut araçlarla doğrula.

## Mühendislik kuralları

1. React, Next.js, Vite ve Tailwind sürümlerini kilit dosyasından belirle; context7 ile kullanılan API sürümünü doğrula.
2. Vite SPA ve Next.js SSR/SSG seçimini SEO, auth, dağıtım ve veri tazeliğine göre yap; ikisini gereksiz birleştirme.
3. TypeScript strict modunda domain modelleri, DTO ve doğrulama şemalarını ayrı tut.
4. Firebase Auth kullanıcı kimliğini tek yetki kanıtı sayma; tenant üyeliği ve rolü sunucuda doğrula.
5. Firestore kurallarında her koleksiyonun okuma/yazma izinlerini açıkça tanımla; varsayılan erişimi kapat.
6. Custom claims değişiminin token yenilenmesi gerektirebileceğini hesapla; hassas işlemde güncel sunucu yetkisini denetle.
7. Firestore Admin SDK kuralları atladığı için Functions/API erişim kontrolünü ayrıca uygula.
8. Tenant verisini path veya alanla izole et; istemciden gelen tenantId değerini üyelik kontrolü olmadan kullanma.
9. Sorgu biçimlerini planla ve gerekli composite indeksleri sürüm kontrolüne ekle.
10. Gerçek zamanlı listener ömrünü route/component ile sınırla; unsubscribe ve hata durumunu işle.
11. Firestore transaction ve batched write farkını seç; eşzamanlı kota/stok işlemlerinde transaction kullan.
12. Ödeme webhookunu sağlayıcı imzasıyla doğrula; event kimliğine göre idempotency ve tekrar oynatma koruması ekle.
13. Storage kurallarında tenant, dosya boyutu, izinli MIME ve sahiplik kontrolü yap; kritik içerik için sunucu incelemesi uygula.
14. Functions runtime, bölge, retry ve timeout ayarlarını ölçümle seç; üçüncü taraf sırlarını secret store içinde tut.
15. Zustand içinde yerel UI/özelleştirici durumunu, TanStack Query içinde sunucu önbelleğini yönet.
16. Query key içine tenant, filtre ve kaynak kimliğini kat; logout sırasında hassas önbelleği temizle.
17. 3D ürün konfigürasyonunu sürümlü JSON şemasıyla sakla; fiyatı istemci hesaplamasına güvenerek tahsil etme.
18. R3F sahnesindeki GLTF/texture bütçesini mobil cihazda ölç; LOD, lazy loading ve sıkıştırmayı içerik ihtiyacına göre uygula.
19. SSR ortamında window/WebGL erişimini istemci sınırına al; hydration sonucunu konsol ve görselle doğrula.
20. Auth yükleniyor durumunu unauthenticated durumundan ayır; redirect döngülerini ve flash-of-private-content durumunu engelle.
21. Firebase Emulator ile tenant izolasyonu, yetkisiz yazma ve sorgu mantığını doğrula; emulator bileşik indeks gereksinimlerini zorlamadığından indeksleri gerçek Firestore test projesinde ayrıca doğrula. Bkz. https://firebase.google.com/docs/emulator-suite/connect_firestore#how_the_cloud_firestore_emulator_differs_from_production
22. Yerel uygulamayı Playwright ile aç; kayıt, giriş, 3D özelleştirme, kaydetme ve erişim reddi akışlarını doğrula.

## Uygulanabilir şablon

Şablonu proje sürümüne ve gereksinimlerine uyarlayarak doğrula; örnek tek başına üretim onayı değildir.

```text
// firestore.rules: tenant üyelikleri yalnızca güvenilir sunucu tarafından yazılır.
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    function member(tenant) {
      return request.auth != null &&
        exists(/databases/$(database)/documents/tenants/$(tenant)/members/$(request.auth.uid));
    }
    match /tenants/{tenant}/configs/{id} {
      allow read: if member(tenant);
      allow create: if member(tenant) &&
        request.resource.data.keys().hasOnly(['owner', 'schemaVersion', 'options']) &&
        request.resource.data.owner == request.auth.uid &&
        request.resource.data.schemaVersion == 1 &&
        request.resource.data.options is map;
      allow update: if member(tenant) &&
        resource.data.owner == request.auth.uid &&
        request.resource.data.owner == resource.data.owner &&
        request.resource.data.diff(resource.data).affectedKeys().hasOnly(['options']) &&
        request.resource.data.options is map;
      allow delete: if member(tenant) && resource.data.owner == request.auth.uid;
    }
  }
}
// Domain seçenekleri için options alt alanlarını da ayrı doğrula.
```

## Yasaklı hatalar

allow read, write: if true, istemciye admin anahtarı koyma, tenant filtrelemesini yalnız UI'da yapma, auth ile ödeme yetkisini karıştırma ve her render içinde listener oluşturma yasaktır.

## Teslim ve doğrulama

Kaynak dosyaları, gerekli bağımlılık/sürüm bilgilerini, çalıştırılan kontrollerin sonucunu ve çözülemeyen kısıtları birlikte teslim et. İlgili görünür çıktıyı açıp kontrol et; ölçülmeyen veya çalıştırılmayan kontrolü başarılı diye sunma.

