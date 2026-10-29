---
name: 11-digital-growth-seo-analytics
description: "GA4, Google Ads, teknik SEO, ölçülebilir dönüşüm hunileri ve landing page deneylerini uygula."
---

# digital growth seo analytics

Bu skill ilgili alan işi istendiğinde uygulanır. Çıktıları kullanıcının kapsamına göre üret; dış sistemlere yayınlama veya üretim değişikliği için mevcut yetki sınırlarını koru. Sürüm bağımlı API'leri resmi kaynak ve mevcut araçlarla doğrula.

## Mühendislik kuralları

1. İş hedefini birincil dönüşüm ve ölçülebilir huni aşamalarıyla tanımla; sayfa görüntülemeyi satış başarısı sayma.
2. GA4 ve Ads kurulumunda güncel resmi dokümantasyonu, property kimliğini ve consent gereksinimlerini doğrula.
3. Olay sözlüğünde event_name, tetikleme, parametre türü, sahiplik ve doğrulama yöntemini kaydet.
4. GA4 önerilen olay adlarını uygun olduklarında kullan; özel olay adlarını kararlı ve küçük harfli tut.
5. E-posta, telefon, ad, hassas serbest metin ve kişisel kimlikleri analytics parametrelerine gönderme.
6. Consent state belirlenmeden izin gerektiren takip başlatma; reddetme ve geri çekme akışını test et.
7. GTM ve doğrudan gtag entegrasyonundan yalnız birini aynı olay için kaynak seç; çift gönderimi engelle.
8. SPA route değişiminde page_view davranışını ölç; otomatik enhanced measurement ile manuel tetiklemeyi çakıştırma.
9. Purchase event için kararlı transaction_id kullan; para birimi/value ve kalem toplamını doğrula.
10. Google Ads conversion label ve ID değerlerini mülkiyeti doğrulanmış hesap ayarlarından al.
11. UTM parametrelerini tutarlı isimlendir; dahili linklere UTM ekleyerek oturumu ve attribution'ı bozma.
12. GA4 DebugView ve tarayıcı network üzerinden olay payloadunu doğrula; panel gecikmesini kurulum hatasıyla karıştırma.
13. Landing page mesajını ziyaretçinin arama niyetiyle eşleştir; birincil CTA ve form hata geri bildirimini açık tut.
14. SEO title, description ve canonical değerlerini her indexlenebilir route için doğru üret.
15. OpenGraph ve sosyal görsel URL'lerini mutlak URL olarak yaz; bot erişimini doğrula.
16. JSON-LD tipini gerçek içerikle eşleştir; sahte yıldız, fiyat veya görünmeyen FAQ üretme.
17. Dinamik sitemap içine yalnız canonical, indexlenebilir ve başarılı URL'leri koy; lastmod değerini gerçek değişimle güncelle.
18. robots.txt ile noindex farkını gözet; tarama yasağı indeks silme garantisi değildir.
19. Core Web Vitals değerlerini saha ve lab ölçümünde ayır; LCP, INP ve CLS darboğazlarını route bazında ele al.
20. Hero görsel boyutlarını sabitle, gerekli fontları sınırlı yükle; ağır 3D sahneyi içerik ve CTA görünürlüğünü engellemeyecek şekilde aç.
21. A/B deneyinde hipotez, örneklem, birincil metrik ve bitirme koşulunu önceden kaydet; sonuçları her gün seçerek erken kesme.
22. Analytics ve SEO tesliminde kurulu etiket, doğrulanan olaylar, bilinmeyen attribution ve erişilemeyen panelleri ayrı raporla.

## Uygulanabilir şablon

Şablonu proje sürümüne ve gereksinimlerine uyarlayarak doğrula; örnek tek başına üretim onayı değildir.

```typescript
type Purchase = {
  transaction_id: string; value: number; currency: string;
  items: Array<{ item_id: string; item_name: string; price: number; quantity: number }>;
};
declare function gtag(command: string, event: string, params: Purchase): void;
export function trackPurchase(p: Purchase, consentGranted: boolean) {
  if (!consentGranted || !Number.isFinite(p.value) || p.value < 0) return;
  // Tetikleme kaynağı ödeme sağlayıcısınca doğrulanmış sipariş olmalı.
  gtag("event", "purchase", p);
}
// Tekrar yüklemede aynı transaction_id korunur; PII eklenmez.
```

## Yasaklı hatalar

Uydurma dönüşüm raporu, consent'i atlayan takip, URL'de PII, aynı purchase olayının iki etiketten gönderilmesi, yanlış canonical ve sahte Schema review verisi yasaktır.

## Teslim ve doğrulama

Kaynak dosyaları, gerekli bağımlılık/sürüm bilgilerini, çalıştırılan kontrollerin sonucunu ve çözülemeyen kısıtları birlikte teslim et. İlgili görünür çıktıyı açıp kontrol et; ölçülmeyen veya çalıştırılmayan kontrolü başarılı diye sunma.

