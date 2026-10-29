---
name: 04-embedded-rtos-firmware
description: ESP32 ve STM32 için deterministik FSM, RTOS, DMA ve kesme tabanlı firmware geliştirir.
---

# Gömülü RTOS Firmware

## Mühendislik kuralları
1. ESP-IDF, Arduino core, STM32 HAL/LL ve FreeRTOS sürümünü hedef kartla eşleştir; Context7 veya resmi üretici dokümantasyonuyla API doğrula.
2. CPU, RAM, flash, clock tree, pin mux ve periferik sahipliğini kaynak tablosunda tanımla.
3. İşlevleri FSM olayları ve geçişleriyle modelle; her durumda timeout, hata ve güvenli çıkış yolu belirle.
4. Arduino delay veya busy-wait kullanma; bare-metal kodda monotonic tick, RTOS periyodik task'ta vTaskDelayUntil kullan.
5. RTOS task beklemesi CPU'yu serbest bırakır; kontrollü scheduler beklemesini bloklayan delay ile aynı sayma.
6. xTaskCreatePinnedToCore yalnız desteklenen ESP32 hedeflerinde kullan; tek çekirdekli hedef ve SMP sürümü için affinity davranışını doğrula.
7. Task önceliklerini deadline ve worst-case execution time ile seç; yüksek öncelikte uzun işlemler yapma.
8. Queue derinliği, mesaj boyutu ve doluluk politikasını tanımla; overflow'u sayaç ve hata durumuyla görünür yap.
9. Mutex'i kaynak sahipliği için, semaphore/notification'ı sinyal için kullan; priority inversion'ı analiz et.
10. ISR'da yalnız sınırlı süreli kayıt/sinyal işlemi yap; printf, malloc, blocking lock ve ağ çağrısı kullanma.
11. IRAM_ATTR gerektiğinde ISR'ın çağırdığı fonksiyonların ve verilerin bellek yerleşimini de doğrula; attribute tek başına yeterli değildir.
12. ISR-to-task bildiriminde FromISR API ve gerekli yield davranışını kullan; interrupt priority kurallarını denetle.
13. DMA ADC/UART buffer'ını çevresel hizalama ve cache coherency gereksinimleriyle ayır; STM32 M7 cache bakımını değerlendir.
14. UART DMA circular ring'de producer/consumer indekslerini taşma ve wrap ile yönet; IDLE olayını paket sınırı garantisi sayma.
15. ADC kalibrasyonu, örnekleme süresi, referans gerilimi ve kaynak empedansını ölç; sayımı fiziksel birime dönüştür.
16. Paylaşılan değişkenlerde volatile'ı senkronizasyon yerine koyma; atomik/critical section ve sahiplik kullan.
17. Heap'i kritik döngüden uzaklaştır; stack high-water mark, fragmentation ve allocation başarısızlığını ölç.
18. Paket parser'ında uzunluk, CRC, sıra numarası ve sınır kontrolünü payload kullanılmadan önce yap.
19. Watchdog'u sağlıklı görev ilerlemesiyle besle; kilitlenmiş task'ı gizleyen bağımsız feed döngüsü kurma.
20. Boot, brownout, haberleşme kaybı ve reset sırasında aktüatörlerin güvenli varsayılanını donanım ve yazılımla belirle.
21. Kontrol task'ını telemetri ve flash yazımından ayır; jitter ve deadline kaçırma sayaçlarını kaydet.
22. Unit/HIL doğrulamada tick wrap, queue overflow, sensör timeout ve güç kesintisini dene; flash sonucu gerçek cihaz ölçümüyle raporla.

## Kod şablonu
```cpp
// FreeRTOS: control_step() sınırlı süreli olmalı, I/O burada beklememeli.
void control_task(void *) {
  TickType_t wake = xTaskGetTickCount();
  const TickType_t period = pdMS_TO_TICKS(10);
  configASSERT(period > 0);
  for (;;) {
    control_step();
    vTaskDelayUntil(&wake, period);
  }
}
```
Task creation, stack birimi ve ISR priority ayarlarını hedef port dokümanına göre tamamla.

## Yasaklı hatalar
- ISR içinde dosya/ağ I/O, heap allocation veya blocking mutex kullanma.
- volatile ile race condition çözüldü deme.
- Tek task'ın watchdog beslemesini tüm sistemin sağlığı sayma.
- Platform pin/clock/DMA ayarlarını başka karta sorgusuz kopyalama.
