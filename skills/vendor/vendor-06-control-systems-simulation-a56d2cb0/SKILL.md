---
name: 06-control-systems-simulation
description: Ayrık PID, durum uzayı, LQR/LQI, LADRC, Kalman filtresi ve sınırları doğrulanmış hareket yörüngeleri tasarlar.
---

# Kontrol Sistemleri ve Simülasyon

## Mühendislik kuralları
1. Plant modeli, örnekleme Ts, gecikme, birimler, sensör gürültüsü ve aktüatör limitlerini önce tanımla.
2. MATLAB/Python kontrol kütüphanelerinin sürümünü ve işaret konvansiyonlarını resmi dokümandan doğrula.
3. Sürekli model ile ayrık modeli ayır; ZOH/Tustin seçimini açıkça kaydet ve örnekleme kaynaklı faz kaybını analiz et.
4. Ayrık durum uzayını x[k+1]=Ad x[k]+Bd u[k] olarak kur; sürekli A/B matrislerini doğrudan ayrık denklemde kullanma.
5. PID integrali Ts ile ölçekle; türevi tercihen ölçümden alarak setpoint kick'i azalt.
6. Türeve alçak geçiren filtre uygula; cutoff'u sensör bandwidth, gürültü ve phase margin ile seç.
7. Saturation'ı fiziksel çıkış sınırlarıyla modelle; clamping veya back-calculation anti-windup uygula.
8. Manuel/otomatik geçişte bumpless transfer için integral ve filtre durumlarını mevcut çıkışa göre başlat.
9. Çıkış rate limit ve deadband varsa anti-windup feedback'ine gerçek uygulanmış çıkışı dahil et.
10. LQR için controllability, Q/R boyutu, pozitiflik ve durum birimlerini kontrol et; ağırlıkları normalize et.
11. Ayrık LQR'da DARE çöz; K ve kapalı çevrim Ad-BdK özdeğerlerini doğrula.
12. LQI integral augmentation'da izlenebilir çıktı ve controllability koşullarını kontrol et; integratör saturation'ını sınırla.
13. LADRC için relative degree ve nominal input gain b0 işaretini tanımla; sıfıra yakın b0 ile kontrol üretme.
14. LESO bandwidth'i sampling, sensör gürültüsü ve kontrol bandwidth'iyle uyumlu seç; yüksek bandwidth'i evrensel çözüm sayma.
15. LADRC toplam bozucu tahminini çıkış limitleriyle birlikte test et; model belirsizliği altında kestirim hatasını ölç.
16. Kalman Q/R matrislerini ölçüm/plant belirsizliğiyle belirle; covariance symmetry ve pozitif yarı-belirliliğini izle.
17. Kalman güncellemesinde inverse yerine solve/Cholesky tercih et; sayısal dayanıklılık için Joseph covariance formunu değerlendir.
18. Timestamp farklı sensörleri aynı ana göre hizala; missing measurement, outlier gating ve stale data'yı ele al.
19. Quintic smoothstep'in pozisyon/hız/ivme uç koşullarını sağlar fakat jerk uçta sıfır değildir; fiziksel jerk limitini açıkça doğrula.
20. Jerk-limited S-curve gerekiyorsa parça bazlı jerk profili üret; hız, ivme ve jerk tepe sınırlarından süreleri hesapla.
21. Gömülü C++ uygulamasında float precision, overflow, NaN, jitter ve gerçek Ts sapmasını ele al.
22. Step/ramp, yük bozucusu, saturation, model belirsizliği ve sensor dropout simülasyonlarını MATLAB/Python'da çalıştır; HIL sonucu olmadan cihaz performansını iddia etme.

## Kod şablonu
```python
from dataclasses import dataclass
@dataclass
class PID:
    kp: float; ki: float; kd: float; ts: float; tau: float
    lo: float; hi: float; kaw: float
    integ: float = 0.; prev_y: float = 0.; deriv: float = 0.
    def step(self, ref, y):
        error = ref - y
        a = self.tau / (self.tau + self.ts)
        self.deriv = a*self.deriv + (1-a)*(y-self.prev_y)/self.ts
        raw = self.kp*error + self.integ - self.kd*self.deriv
        out = min(self.hi, max(self.lo, raw))
        self.integ += self.ts*(self.ki*error + self.kaw*(out-raw))
        self.prev_y = y
        return out
```
Ts>0, tau>=0 ve limit/gain doğrulamasını ekle; ilk prev_y'yi ölçümden başlat. MATLAB karşılığı için `c2d`, `dlqr`, Python için ilgili sürümdeki `cont2discrete`/DARE API'sini doğrula.

```matlab
% A, B, C, D sürekli plant matrisleri; Q ve R birimleri normalize edilmiş.
Ts = 0.01;
plant_d = c2d(ss(A,B,C,D), Ts, 'zoh');
[K, P, poles] = dlqr(plant_d.A, plant_d.B, Q, R);
assert(all(abs(poles) < 1), 'Ayrık kapalı çevrim kararsız');
```
```cpp
// Quintic interpolasyon; ayrı v/a/j limit doğrulaması gerektirir.
struct Trajectory { double position, velocity, acceleration, jerk; };
Trajectory quintic(double t, double duration, double p0, double p1) {
  // Çağıran duration > 0, 0 <= t <= duration ve sonlu parametreleri doğrulamalı.
  const double u = t / duration;
  const double d = p1 - p0;
  return {p0 + d*(10*u*u*u - 15*u*u*u*u + 6*u*u*u*u*u),
          d*(30*u*u - 60*u*u*u + 30*u*u*u*u)/duration,
          d*(60*u - 180*u*u + 120*u*u*u)/(duration*duration),
          d*(60 - 360*u + 360*u*u)/(duration*duration*duration)};
}
```
Bu yörüngede uç jerk büyüklüğü `60*abs(p1-p0)/duration^3` olur; sıfır jerk'li bekleme segmentine geçiş süreksizdir. Jerk sürekliliği gerekiyorsa başka profil seç.

## Yasaklı hatalar
- Integral gain'i hem katsayıda hem uygulamada Ts ile iki kez çarpma.
- Continuous LQR kazancını aynı örnekleme doğrulaması olmadan discrete firmware'e taşıma.
- Quintic yörüngeyi jerk ölçmeden jerk-limited ilan etme.
- Kalman filtresinde farklı birimleri aynı Q/R değeriyle gerekçesiz ağırlıklandırma.
