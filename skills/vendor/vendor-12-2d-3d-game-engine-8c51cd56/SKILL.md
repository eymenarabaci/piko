---
name: 12-2d-3d-game-engine
description: "Canvas, PixiJS, Phaser ve Three.js ile 2D/3D web oyunları, kule savunma ve performanslı oyun döngüleri geliştir."
---

# 2d 3d game engine

Bu skill ilgili alan işi istendiğinde uygulanır. Çıktıları kullanıcının kapsamına göre üret; dış sistemlere yayınlama veya üretim değişikliği için mevcut yetki sınırlarını koru. Sürüm bağımlı API'leri resmi kaynak ve mevcut araçlarla doğrula.

## Mühendislik kuralları

1. Canvas, PixiJS, Phaser veya Three.js motorunu render/physics/içerik gereksinimine göre seç; küçük oyunda birden çok motoru yığma.
2. Kurulu motor sürümünün resmi API dokümantasyonunu doğrula; Three.js işlerinde context7 kullan.
3. Simülasyonu sabit timestep ile çalıştır; render kare hızından bağımsız hız, cooldown ve hasar hesapla.
4. Tab gizlendiğinde aşırı catch-up oluşmasını önle; bir karede işlenecek adım sayısını sınırla.
5. Input, simülasyon ve render sorumluluklarını ayır; oyun durumunu DOM bileşeni renderlarına bağlama.
6. Tower Defense grid koordinatı, dünya koordinatı ve ekran koordinatı dönüşümlerini tek modülde tut.
7. A* için geçilebilirlik, komşular ve heuristic uyumunu belirle; diagonal hareket varsa corner-cutting kuralını açıkla.
8. A* kapalı kümesini doğru yönet ve daha kısa yol bulunduğunda g-score güncelle; başarısız yol aramasını ayrı sonuçla bildir.
9. Kule yerleşimi öncesinde spawn ile hedef arasında yol kaldığını doğrula; oyuncuyu tüm güzergâhı kilitleyerek soft-lock'a sokma.
10. Birçok düşman aynı hedefe ilerliyorsa flow-field veya ters arama kullan; her düşman için her kare A* çalıştırma.
11. Wave tanımını veri olarak tut; spawn bütçesi, aralık, tür ve tamamlanma koşulunu deterministik uygula.
12. Hedefleme için spatial hash/grid kullan; menzil, LOS ve priority politikasını ayrı değerlendir.
13. Mermi, particle ve hasar metni için object pool kullan; yeniden kullanımda tüm durum alanlarını sıfırla.
14. Pool kapasitesi dolduğunda kontrollü davranış belirle; sınırsız yeni nesne oluşturmayı fallback yapma.
15. RNG seed ve oyun olaylarını saklayarak replay/debug tekrarlanabilirliğini sağla.
16. Sprite atlasları, batching ve instancing ile draw-call bütçesini azalt; transparency sıralamasını görsel olarak denetle.
17. 2D/3D shaderlarda renk uzayı, alpha blending ve precision davranışını kurulu motora göre doğrula.
18. Three.js kaynaklarında ownership belirle; sahne kaldırılınca paylaşılmayan geometry/material/texture nesnelerini dispose et.
19. WebAudio AudioContext'i kullanıcı etkileşimi sonrasında başlat; master gain ve ses sınırlandırmasını uygula.
20. Prosedürel seslerde oscillator/gain envelope ile kısa attack/release kullan; ani amplitude sıçramasını önle.
21. Mobil pointer, çoklu dokunma, devicePixelRatio ve canvas resize davranışını test et.
22. Playwright görsel döngüsünde konsol, pause/resume, wave bitişi, restart ve performans profiler ölçümlerini kontrol et.

## Uygulanabilir şablon

Şablonu proje sürümüne ve gereksinimlerine uyarlayarak doğrula; örnek tek başına üretim onayı değildir.

```javascript
const STEP = 1 / 60;
let previous = performance.now(), accumulator = 0;
function frame(now) {
  accumulator += Math.min((now - previous) / 1000, 0.1);
  previous = now;
  let steps = 0;
  while (accumulator >= STEP && steps < 6) {
    updateSimulation(STEP);
    accumulator -= STEP;
    steps++;
  }
  if (steps === 6) accumulator %= STEP;
  renderWorld(accumulator / STEP);
  requestAnimationFrame(frame);
}
document.addEventListener("visibilitychange", () => {
  previous = performance.now();
  accumulator = 0;
});
requestAnimationFrame(frame);
// updateSimulation ve renderWorld motor adaptörleri olarak uygulanır.
```

## Yasaklı hatalar

deltaTime olmadan hareket, her kare yol arama, canlı entity listesini iterasyon sırasında silme, resetlenmeyen pooled nesneler, kullanıcı etkileşimi olmadan ses başlatma ve dispose edilmeyen GPU kaynakları yasaktır.

## Teslim ve doğrulama

Kaynak dosyaları, gerekli bağımlılık/sürüm bilgilerini, çalıştırılan kontrollerin sonucunu ve çözülemeyen kısıtları birlikte teslim et. İlgili görünür çıktıyı açıp kontrol et; ölçülmeyen veya çalıştırılmayan kontrolü başarılı diye sunma.

