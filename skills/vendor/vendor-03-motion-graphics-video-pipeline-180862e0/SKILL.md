---
name: 03-motion-graphics-video-pipeline
description: Remotion ve Motion Canvas ile kod tabanlı hareketli grafikler ve FFmpeg video üretim iş akışları kurar.
---

# Motion Graphics ve Video Boru Hattı

## Mühendislik kuralları
1. Remotion, Motion Canvas ve FFmpeg sürümlerini belirle; sürüme bağlı API ve CLI seçeneklerini Context7/resmi dokümantasyondan doğrula.
2. Kompozisyon fps, genişlik, yükseklik ve durationInFrames değerlerini tek kaynakta tanımla; hedef 60 FPS ise süreyi kareye çevir.
3. Render'ı frame indeksinin saf fonksiyonu yap; Date.now, rastgele seed'siz Math.random ve ağ zamanına bağlı animasyon kullanma.
4. Fiziksel giriş/çıkışlarda spring kullan; damping, stiffness ve mass değerlerini hedef yerleşme süresine göre seç.
5. Spring overshoot'unu bilinçli tasarla; opacity gibi sınırlı özelliklerde aralığı clamp et.
6. Sequence yerel frame ve kompozisyon global frame farkını hesapla; negatif başlangıç karelerini test et.
7. Stagger animasyonlarında indeks başına offset kullan; son öğenin tamamlanması için toplam süreyi kontrol et.
8. 3D CSS perspektifte camera origin, transform-style ve backface-visibility davranışını doğrula.
9. Gerçek 3D kamerada focal length/FOV, near/far ve easing'i tanımla; derinlik sıralaması ile overlay arayüzünü ayır.
10. Font ve görsellerin yüklenmesini render tamamlanmadan bekle; eksik varlıkları sessizce boş geçme.
11. Ses başlangıç offset'i, fps ve sample rate ilişkisini doğrula; klip kesimlerinde click önleyici fade uygula.
12. Uzun sahnelerde motion blur örneklemesini ve render maliyetini ölç; 60 FPS tek başına sinematik kalite kanıtı değildir.
13. Motion Canvas generator zaman çizelgesinde wait/transition sürelerini açıkça yönet; aynı objeyi çakışan task'larla sürme.
14. Renk profilini giriş, kompozisyon ve FFmpeg export boyunca tanımla; SDR/HDR varsayımlarını karıştırma.
15. Web dağıtımı için uyumlu pix_fmt ve codec seç; alpha gerekiyorsa alpha destekli ara format kullan.
16. FFmpeg toplu dönüşümlerde çıktı klasörlerini oluştur, input dosyalarını quote et ve üzerine yazma politikasını seç.
17. Video-to-WebP dönüşümünde fps/scale/quality bütçesini belirle; kısa loop sınırlarında sıçrama kontrolü yap.
18. Kare dizilerinde sıfır dolgulu numaralandırma ve başlangıç indeksini koru; eksik kareyi tespit et.
19. DaVinci Resolve scripting için kurulu sürüm, lisans ve scripting erişimini doğrula; proje kopyasında timeline işlemlerini dene.
20. Resolve tesliminde medya yolları, relink ve render preset'lerini manifestte kaydet; evrensel API adı uydurma.
21. Temsilî başlangıç, orta ve son kareleri render et; taşma, yazım, safe area ve kamera kesimlerini incele.
22. Son videoyu ffprobe ile codec, fps, duration, audio stream ve çözünürlük açısından doğrula; dosyayı oynatıp A/V sync kontrol et.

## Kod şablonu
```tsx
import {AbsoluteFill, spring, useCurrentFrame, useVideoConfig} from 'remotion';
export const Intro = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame, fps, config: {damping: 18, stiffness: 120, mass: 1}});
  return <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center',
    transform: `translateY(${(1-p)*80}px)`}}>Ürün Tanıtımı</AbsoluteFill>;
};
```
```sh
ffmpeg -i input.mp4 -vf "fps=15,scale=640:-2" -loop 0 preview.webp
ffprobe -v error -show_streams -show_format output.mp4
```

## Yasaklı hatalar
- useCurrentFrame yerine duvar saatine bağlı hareket kullanma.
- 60 FPS kompozisyonu 30 FPS export edip ses/süreyi kontrol etmeden teslim etme.
- Görsel preview'ı final render başarısının kanıtı sayma.
- DaVinci Resolve'da kullanıcı projesini kopyalamadan toplu destructive timeline işlemi yapma.
