---
name: 07-pcb-power-electronics
description: H-köprü, gate driver ve BMS güç devrelerini hesap, PCB yerleşimi ve SPICE doğrulamasıyla tasarlar.
---

# PCB ve Güç Elektroniği

## Mühendislik kuralları
1. MOSFET/IGBT, driver, izolasyon ve koruma bileşeni için gerçek datasheet revizyonunu kullan; güncel özellikleri resmi üretici kaynağından doğrula.
2. Bus gerilimi, akım, switching frequency, yük endüktansı, transient, ortam sıcaklığı ve fault enerjisini tanımla.
3. H-köprü truth table'ında brake/coast/reverse ve reset durumlarını tanımla; gate varsayılanlarını donanım pull-down ile belirle.
4. MOSFET Vds/Id ve IGBT Vce/Ic sınırlarını transient payıyla değerlendir; yalnız nominal akıma bakma.
5. SOA, avalanche, short-circuit withstand ve junction temperature sınırlarını gerçek pulse koşullarıyla kontrol et.
6. İletim kaybını sıcaklığa bağlı Rds(on) veya Vce(sat) ile, switching kaybını enerji eğrileriyle hesapla.
7. Gate driver peak source/sink akımını gate charge, Miller plateau ve hedef switching süresinden türet.
8. Gate dirençlerini açma/kapama için ayrı seçmeyi değerlendir; ringing, EMI ve switching loss arasında ölçümle denge kur.
9. Bootstrap C için toplam gate charge, driver quiescent/leakage ve maksimum high-side on-time'ın izin verilen droop'a oranını hesapla.
10. Bootstrap diode gerilim/reverse recovery ve capacitor bias derating'ini dahil et; yüksek duty veya DC on-time için refresh/izole beslemeyi değerlendir.
11. Dead-time'ı driver propagation mismatch, turn-off gecikmesi, tolerans ve sıcaklıkla hesapla; yalnız tipik datasheet değerine güvenme.
12. Fazla dead-time body diode kaybını ve reverse recovery'yi artırır; shoot-through ile birlikte ölçerek optimize et.
13. Miller clamp/negatif gate bias ihtiyacını dV/dt, Cgd ve off-state empedansıyla analiz et; SiC gate voltage sınırlarını aşma.
14. Optokuplör/izole sürücü CMTI, propagation delay, skew, izolasyon rating ve creepage/clearance değerlerini kontrol et.
15. Kelvin source/emitter dönüşünü güç dönüş yolundan ayır; driver gate loop alanını minimum tut.
16. Yüksek di/dt commutation loop'a DC-link düşük ESL kapasitörünü yakın yerleştir; snubber'ı ölçülen ringing'e göre tasarla.
17. Star ground'u otomatik çözüm sayma; return current yollarını ve analog/digital/power referanslarını devre bağlamında planla.
18. Akım ölçümünde shunt Kelvin uçları, common-mode, ADC bandwidth ve sense filtre gecikmesini hesapla.
19. BMS overcurrent/overvoltage/undervoltage/temperature korumaları için threshold, hysteresis, debounce ve latch/reset mantığını tanımla.
20. Kritik korumada firmware'e ek bağımsız donanım cutoff/fuse ve gate shutdown yolunu değerlendir; fault energy'yi sınırla.
21. SPICE'ta üretici modeli, parasitic L/R/C, gate drive ve sıcaklık senaryolarıyla switching/fault simüle et; model kapsamını raporla.
22. ERC/DRC, termal ve izolasyon mesafesi incelemesinden sonra sınırlı akım ve uygun diferansiyel probla test planı oluştur; simülasyonu fiziksel güvenlik doğrulaması sayma.

## Kod şablonu
```python
def bootstrap_cap(q_gate_c, i_driver_a, i_leak_a, t_on_s,
                  delta_v, design_factor=2.0):
    if delta_v <= 0 or t_on_s < 0:
        raise ValueError('Geçersiz bootstrap çalışma koşulu')
    charge = q_gate_c + (i_driver_a + i_leak_a) * t_on_s
    return design_factor * charge / delta_v

# Örnek varsayımlar: 80 nC, 0.6 mA toplam tüketim, 100 us, 0.5 V droop.
c_min_f = bootstrap_cap(80e-9, 0.5e-3, 0.1e-3, 100e-6, 0.5)
```
Bu alt sınır hesabına gerçek capacitor DC bias/tolerans, diode loss ve minimum Vgs kontrolü ekle.

## Yasaklı hatalar
- Dead-time'ı tüm MOSFET/IGBT devrelerine sabit evrensel sayı olarak uygula.
- Yüksek güç dönüşünü gate driver Kelvin referansıyla ortak uzun izden taşıma.
- İzolasyonun yalnız optokuplör ekleyerek tamamlandığını varsayma.
- SPICE başarısını gerçek PCB'nin short-circuit veya safety sertifikası olarak sunma.
