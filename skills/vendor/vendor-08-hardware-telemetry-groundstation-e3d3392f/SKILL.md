---
name: 08-hardware-telemetry-groundstation
description: 50–500 Hz donanım telemetrisi için tamponlu veri akışı, osiloskop grafikleri ve yer istasyonu arayüzü geliştirir.
---

# Donanım Telemetrisi ve Yer İstasyonu

## Mühendislik kuralları
1. WebSerial, WebSocket, MQTT, uPlot ve ilgili UI kütüphanesi sürüm/API'sini Context7 veya resmi dokümandan doğrula.
2. Veri protokolünde version, message type, payload length, sequence, timestamp, units ve CRC alanlarını belirle.
3. 50–500 Hz veri alma hızını ekran yenileme hızından ayır; React render sayısını packet sayısına bağlama.
4. TypedArray ring buffer ile sabit bellek ayır; kapasiteyi örnekleme hızı ve görünür zaman penceresinden hesapla.
5. Producer/consumer wrap ve overflow politikasını tanımla; dropped sample sayısını kullanıcıya göster.
6. requestAnimationFrame ile çizim yap; latest snapshot/batch yaklaşımıyla aynı karede gelen paketleri birleştir.
7. Binary parsing ve yüksek maliyetli hesapları Web Worker'a taşı; transferable buffer sahipliğini yönet.
8. WebSerial yalnız desteklenen tarayıcı/secure context'te çalışır; permission ve disconnect akışını tasarla.
9. Serial read chunk'ını paket sanma; partial frame, birden fazla frame ve resync parser'ı uygula.
10. Tarayıcıda genel ham UDP socket varsayma; UDP için yetkili backend veya native gateway kullan ve web tarafına WebSocket/MQTT taşı.
11. WebSocket bufferedAmount ve backend queue derinliğine göre backpressure kur; sınırsız mesaj kuyruğu üretme.
12. MQTT QoS/retain davranışını topic türüne göre seç; eski retained hareket komutlarının çalıştırılmasını önle.
13. Sequence wrap/reorder ve kayıp hesaplarını protokol genişliğine göre yap; reconnect sonrası sayaç epoch'unu ayır.
14. Tek yönlü latency için saat senkronizasyonu gerekir; senkronizasyon yoksa RTT veya inter-arrival jitter raporla.
15. Canvas/uPlot çiziminde görünür aralığı downsample et; min/max envelope ile kısa tepe olaylarını koru.
16. Display downsampling'i ham kayıt verisinden ayır; CSV/binary export birim ve timestamp bilgisini korusun.
17. Dark UI'da kontrast, birim, legend, alarma özgü renk ve stale-data göstergesi kullan; yalnız renkle anlam verme.
18. Kritik komutlara command ID, timeout, acknowledgement ve cihaz tarafı state validation ekle.
19. E-Stop panelinde gerçek cihaz acknowledgement ve status göster; web kontrolünü safety-rated fiziksel E-Stop yerine koyma.
20. Haberleşme kaybı, sekmenin arka plana alınması ve reconnect durumunda kontrol yetkisini açık state machine ile yönet.
21. Telemetri kimlik doğrulaması ve yetkiyi gateway'de uygula; komut kanalını salt okunur veri aboneliğinden ayır.
22. Playwright'ta sentetik 500 Hz akış, paket kaybı ve disconnect testi yap; UI gecikmesi, ring overflow ve konsol hatalarını ölç.

## Kod şablonu
```ts
class Samples {
  private values: Float32Array;
  private head = 0;
  count = 0;
  constructor(readonly capacity: number) {
    if (!Number.isInteger(capacity) || capacity < 1) throw new Error('capacity');
    this.values = new Float32Array(capacity);
  }
  push(value: number) {
    this.values[this.head] = value;
    this.head = (this.head + 1) % this.capacity;
    this.count = Math.min(this.count + 1, this.capacity);
  }
  snapshot() {
    const result = new Float32Array(this.count);
    for (let i = 0; i < this.count; i++)
      result[i] = this.values[(this.head - this.count + i + this.capacity) % this.capacity];
    return result;
  }
}
```
Bu tek JS thread şablonudur; worker/shared-memory kullanımında atomik indeks/sahiplik tasarla. Snapshot allocation'ını yüksek frekansta tekrarlama; çizim tamponunu yeniden kullan.

## Yasaklı hatalar
- Her paket için tüm grafik serisini kopyalayıp React state güncelleme.
- Timestamp farkını saat senkronizasyonu olmadan tek yönlü gecikme olarak sunma.
- CRC/length doğrulamadan binary field'ları okuyup komut işleme.
- Web E-Stop düğmesinin bağlantı kaybında da fiziksel güvenliği garanti ettiğini söyleme.
