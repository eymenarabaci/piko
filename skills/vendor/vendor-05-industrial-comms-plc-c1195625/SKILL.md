---
name: 05-industrial-comms-plc
description: Modbus, RS-485, CAN, Ethernet ve PLC haberleşmesini hata kurtarma ve güvenli durum mantığıyla uygular.
---

# Endüstriyel Haberleşme ve PLC

## Mühendislik kuralları
1. Modbus standardı, ESP32 TWAI, STM32 bxCAN/FDCAN, W5500/LWIP ve GMT PLC model/sürüm dokümanını doğrula.
2. Her link için hız, framing, addressing, timeout, retry, kablo ve hata bütçesini tanımla.
3. RS-485 DE/RE kontrolünde UART transfer complete'i bekle; TX buffer empty'nin son stop bitinin bittiği anlamına gelmediğini dikkate al.
4. Hattın iki fiziksel ucunu uygun terminasyonla sonlandır; biasing, ortak-mode aralığı ve izolasyon ihtiyacını değerlendir.
5. Modbus RTU karakter zamanını baud/parity/stop bit'e göre hesapla; frame arası sessizlik kuralını standarda göre uygula.
6. Modbus CRC16'yı frame üzerinde hesapla ve low-byte/high-byte gönderim sırasını doğrula.
7. Register haritasında adres tabanı, erişim türü, ölçek, signedness ve 32-bit word order'u belgeleyip test et.
8. Modbus exception response'u transport timeout'tan ayır; write retry'da işlemin idempotent olup olmadığını değerlendir.
9. Modbus TCP MBAP transaction ID, protocol ID, length ve unit ID alanlarını denetle; TCP parçalanmasına karşı akış parser'ı kullan.
10. CAN standard/extended ID, DLC, payload endian ve mesaj periyodunu sözleşmede tanımla.
11. CAN arbitration ID önceliğini kontrol deadline'larına göre seç; bus yükünü bit stuffing ve hata frame'lerini hesaba katarak ölç.
12. TWAI classic CAN ile CAN FD özelliklerini karıştırma; hedef çipin gerçek desteklediği controller'ı doğrula.
13. bxCAN/FDCAN acceptance filter'larıyla gereksiz frame'leri azalt; bus-off recovery ve error counter olaylarını işle.
14. W5500 donanım socket'leri ve LWIP yazılım stack kullanımını ayrı mimariler olarak ele al; ikisini eşdeğer API sayma.
15. Ethernet link down, DHCP yenileme, reconnect backoff ve socket kaynaklarını yönet; sınırsız reconnect döngüsü kurma.
16. I2C bus stuck durumunda cihaz reseti ve desteklenen bus recovery uygula; SCL darbeleri/STOP işlemini elektriksel şartlara göre değerlendir.
17. SPI CS, mode, hız ve DMA completion sahipliğini tanımla; birden fazla task'ın transaction'larını serialize et.
18. PLC Ladder/Structured Text'te scan-cycle semantiği, edge detection, timer ve retentive değişken davranışını belgeye göre seç.
19. GMT modelinde IEC 61131-3 dil/API desteğini doğrula; genel Structured Text örneğini üretici komutu diye sunma.
20. Heartbeat'e sequence ve timeout ekle; kayıpta komut kabulünü kes, çıkışları risk analizine göre güvenli duruma geçir.
21. Yazılım heartbeat'i safety-rated acil durdurmanın yerine koyma; gerçek E-Stop zincirini uygun donanım ve standartlarla ayrı doğrula.
22. Testte CRC hatası, kopuk kablo, duplicate frame, stale command, bus-off ve PLC restart senaryolarını kapsa.

## Kod şablonu
```python
def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

request = bytes.fromhex('01 03 00 00 00 0A')
assert modbus_crc(request) == 0xCDC5
wire = request + modbus_crc(request).to_bytes(2, 'little')
```

## Yasaklı hatalar
- CRC'yi payload kullanıldıktan sonra kontrol etme.
- PLC scan cycle içinde ağ timeout'u kadar bekleme.
- 40001 referans adresini otomatik olarak wire adresi 40001 kabul etme.
- Heartbeat veya web düğmesini sertifikalı E-Stop diye adlandırma.
