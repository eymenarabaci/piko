---
name: 14-edtech-mentorship-curriculum
description: "Mühendislik atölyesi müfredatı, uygulamalı ders planı, Marp/Reveal.js sunumları ve öğrenci takip panoları oluştur."
---

# edtech mentorship curriculum

Bu skill ilgili alan işi istendiğinde uygulanır. Çıktıları kullanıcının kapsamına göre üret; dış sistemlere yayınlama veya üretim değişikliği için mevcut yetki sınırlarını koru. Sürüm bağımlı API'leri resmi kaynak ve mevcut araçlarla doğrula.

## Mühendislik kuralları

1. Hedef yaş, ön koşul, süre, cihaz sayısı ve eğitmen kapasitesini ders kapsamına bağla.
2. Öğrenme kazanımını gözlenebilir fiil ve teslim edilebilir artefaktla yaz.
3. Dersleri ön koşul grafiğiyle sırala; GPIO öğrenilmeden PID/RTOS'a geçme.
4. Her oturumda kısa kavram, rehberli uygulama, bağımsız görev ve yansıtma bölümü planla.
5. Donanım dağıtımında BOM, bağlantı çizimi, sürüm ve hazırlık kontrolünü sağla.
6. Atölye elektrik/ısı/mekanik risklerini kullanılan ekipmana özel anlat; öğrenciyi yetkisiz şebeke gerilimi işine yönlendirme.
7. Başlangıç kodunun kurulumunu temiz makinede doğrula; bağımlılık sürümlerini ve offline alternatifi kaydet.
8. Temel ve ileri görev seviyelerini ayır; erken bitiren öğrenciler için somut genişletme görevi hazırla.
9. Hata ayıklama rubriğinde hipotez, gözlem, ölçüm ve düzeltme kaydını puanla.
10. Öğrencinin tüm çözümünü üretmek yerine ipucu kademelerini kullan; değerlendirme sırasında izin verilen yardımı açıklığa kavuştur.
11. Takım rollerini dönüşümlü uygula; tek öğrencinin tüm klavye/donanım kontrolünü almasını önle.
12. Marp ve Reveal.js seçimini PDF ihtiyacı, interaktivite ve hosting ortamına göre yap.
13. Sunumda tek ana kazanımı bir slaytta işle; bağlantı şeması ve kodu okunabilir ölçekte göster.
14. Canlı demo başarısızlığı için test edilmiş kayıt veya offline örnek hazırla.
15. Kod bloklarını dil etiketiyle yaz; uzun örneği adımlı böl ve çalıştırılabilir dosyasına bağla.
16. Renk dışı işaret, yeterli kontrast, alt metin ve klavye erişimiyle erişilebilirliği gözet.
17. Yoklama verisinde yalnız ihtiyaç duyulan öğrenci kimliği/tarih/durum alanlarını tut.
18. Öğrenci kişisel verisini herkese açık dashboard veya repository içinde paylaşma.
19. Dashboard yetkilerini öğrenci, mentor ve yönetici rolleriyle ayır; öğrencinin başka öğrenciyi görmesini görev kapsamına göre sınırla.
20. Takipte görev, teslim, engel ve mentor notlarını ayrı tut; yalnız ders katılımını başarı ölçütü yapma.
21. Rubriğin ölçüt/puan açıklamasını ders öncesinde paylaş; geri bildirimi somut artefakta dayandır.
22. Sunumu render ederek taşma ve okunabilirliği, dashboard'u Playwright ile yoklama/filtre/yetki akışlarını kontrol et.

## Uygulanabilir şablon

Şablonu proje sürümüne ve gereksinimlerine uyarlayarak doğrula; örnek tek başına üretim onayı değildir.

```markdown
---
marp: true
theme: default
paginate: true
---
# Sensör Verisini Güvenilir Okumak
Kazanım: Öğrenci hatalı paketleri CRC ile ayırt eder.

---
## Rehberli Uygulama — 15 dakika
1. Verilen seri paket örneğini aç.
2. Doğru ve bozuk CRC örneklerini karşılaştır.
3. Geçersiz veride son güvenilir değeri işaretle.

---
## Bağımsız Teslim — 20 dakika
- Paket parser dosyası ve üç test senaryosu.
- CRC hatasının UI'da nasıl gösterildiğini açıklayan kısa kayıt.
- Rubrik: doğruluk 50, hata yönetimi 30, açıklama 20.
```

## Yasaklı hatalar

Yaşa uymayan donanım riski, çalışan başlangıç örneği olmadan kurulum dersi, çözümsüz sürüm çakışması, tek katılım metriği, izinsiz öğrenci verisi paylaşımı ve okunamayan slayt kodu yasaktır.

## Teslim ve doğrulama

Kaynak dosyaları, gerekli bağımlılık/sürüm bilgilerini, çalıştırılan kontrollerin sonucunu ve çözülemeyen kısıtları birlikte teslim et. İlgili görünür çıktıyı açıp kontrol et; ölçülmeyen veya çalıştırılmayan kontrolü başarılı diye sunma.

