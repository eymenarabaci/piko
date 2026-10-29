---
name: 01-cinematic-3d-web
description: Sinematik React Three Fiber sahneleri, GLSL materyalleri, scroll animasyonu ve video kare dizisi oynatıcıları üretir.
---

# Sinematik 3D Web

## Mühendislik kuralları
1. Three.js, React Three Fiber, drei, postprocessing, Lenis ve GSAP sürümlerini kilit dosyasından belirle; uygulamadan önce Context7 üzerinden ilgili sürüm API'sini doğrula. Araç yoksa resmi dokümantasyonu kullan ve sınırlamayı bildir.
2. Sahneyi kamera, ışık, varlık, etkileşim ve kalite profili bileşenlerine ayır; dünya birimi, kamera yakın/uzak düzlemleri ve koordinat yönünü tanımla.
3. React state'i kare başına değiştirme; zamanla değişen transformları ref ve useFrame ile sürdür.
4. Delta zamanını kullan; sekmeden dönüşte büyük delta değerlerini sınırla ve deterministik animasyon için zaman tabanını seç.
5. Color management, texture colorSpace ve renderer tone mapping ayarlarını Three.js sürümüne göre doğrula; renk verisi ve normal/roughness verisini ayır.
6. HDRI Environment için çözünürlük ve bellek bütçesi koy; environment intensity ile materyal roughness'ını birlikte ayarla.
7. MeshTransmissionMaterial kullanırken IOR, thickness, chromaticAberration ve anisotropicBlur değerlerini fiziksel niyet ve görsel testle belirle; sürüme bağlı anisotropy isimlerini varsayma.
8. Transmission için örnek sayısı ve buffer çözünürlüğünü cihaz profiline bağla; mobilde daha ucuz materyal seçeneği sağla.
9. GLSL vertex shader değişikliklerinde normal dönüşümünü, model/view/projection uzaylarını ve vertex displacement sınırlarını açıkça tanımla.
10. Fragment shader'da precision, uniform türleri, lineer renk ve alpha blending davranışını kontrol et; derleme hatalarını konsoldan yakala.
11. InstancedMesh parçacıklarında kapasiteyi önceden ayır; matrix/color attribute güncellemesinden sonra needsUpdate işaretle.
12. Instance dönüşümleri değiştiğinde culling bounds'u güncelle veya kontrollü biçimde devre dışı bırak; görünmez parçacık hatasını test et.
13. Geometri, materyal, texture ve render target yaşam döngüsünde paylaşılan kaynak sahipliğini belirle ve yalnızca sahip olunan kaynakları dispose et.
14. Lenis sürümünün GSAP entegrasyonunu doğrula; yalnızca bir RAF/ticker sahibi seç ve unmount sırasında listener/ticker aboneliklerini kaldır.
15. ScrollTrigger scrub: 1 ve pin: true değerlerini talep edilen deneyimin başlangıç ayarı olarak kullan; layout değişiminde refresh ve cleanup uygula.
16. Scroll tabanlı sahne ilerlemesini normalize edilmiş 0..1 parametreye bağla; dokunmatik, klavye ve reduced-motion kullanımını destekle.
17. FFmpeg kare dizisinde sabit fps, ölçek, dosya adlandırma ve renk aralığı seç; toplam indirme boyutunu hesapla.
18. Canvas frame sequence oynatıcıda sınırlı preload penceresi, decode kuyruğu ve eksik kare fallback'i uygula; tüm kareleri aynı anda belleğe alma.
19. Canvas backing store'u CSS boyutu ve sınırlandırılmış devicePixelRatio ile ayarla; resize sonrası son kareyi tekrar çiz.
20. GLB varlıklarında texture boyutu, draw call sayısı, mesh sıkıştırması ve yükleme ilerlemesini ölç; hata ve loading arayüzü sağla.
21. Playwright ile gerçek yerel sayfayı aç; masaüstü/mobil ekran görüntüleri, konsol hataları ve etkileşimleri kontrol et.
22. renderer.info, kare süresi ve GPU kaynak sayısını önce/sonra karşılaştır; performans hedefini ölçümle raporla.

## Kod şablonu
```tsx
import {useRef} from 'react';
import {useFrame} from '@react-three/fiber';
import type {Mesh} from 'three';
export function Rotor() {
  const mesh = useRef<Mesh>(null);
  useFrame((_, dt) => {
    if (mesh.current) mesh.current.rotation.y += Math.min(dt, 0.05) * 0.4;
  });
  return <mesh ref={mesh}><boxGeometry/><meshStandardMaterial roughness={0.3}/></mesh>;
}
```
```sh
ffmpeg -i source.mp4 -vf "fps=30,scale=1280:-2" frames/frame_%05d.webp
```

## Yasaklı hatalar
- Kare başına React setState, render sırasında texture yükleme ve sınırsız parçacık allocation yapma.
- Birden fazla RAF ile aynı Lenis zamanını ilerletme; scroll listener'larını cleanup olmadan bırakma.
- Transmission, bloom ve yüksek DPR'ı cihaz bütçesi olmadan birlikte zorunlu kılma.
- Görüntü üretmeden veya konsol incelemeden görsel doğrulama tamamlandı deme.
