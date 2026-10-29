---
name: 13-technical-reports-academic
description: "Teknofest, Hyperloop, TÜBİTAK tasarım raporları, gereksinim izlenebilirliği, FMEA ve akademik teknik belgeler üret."
---

# technical reports academic

Bu skill ilgili alan işi istendiğinde uygulanır. Çıktıları kullanıcının kapsamına göre üret; dış sistemlere yayınlama veya üretim değişikliği için mevcut yetki sınırlarını koru. Sürüm bağımlı API'leri resmi kaynak ve mevcut araçlarla doğrula.

## Mühendislik kuralları

1. Yarışma yılı, kategori ve resmi şartname sürümünü doğrula; Teknofest, Hyperloop veya TÜBİTAK için tek evrensel şablon varsayma.
2. ÖTR/KTR/FDR adlarını yarışmanın güncel teslim aşamasıyla eşleştir; sayfa ve ek limitlerini kaydet.
3. Rapor kapsamını sistem sınırı, operasyon senaryosu ve arayüzlerle tanımla.
4. Gereksinimleri benzersiz kimlik, kaynak, ölçülebilir kabul kriteri ve doğrulama yöntemiyle yaz.
5. İstek, tasarım kararı ve doğrulanmış sonuç ifadelerini birbirinden ayır.
6. Gereksinim matrisinde tasarım bileşeni, test ID, kanıt dosyası ve durum alanlarını tut.
7. Her sayısal hesapta SI birimi, varsayım, referans ve sınır durumunu belirt.
8. Nominal değerle birlikte tolerans, belirsizlik ve güvenlik katsayısını gerekçelendir.
9. Mekanik, elektrik, yazılım ve haberleşme interface control bilgilerini birbiriyle tutarlı tut.
10. FMEA satırında hata modu, sebep, yerel/sistem etkisi, mevcut kontrol ve düzeltici faaliyet alanlarını doldur.
11. FMEA S/O/D ölçeklerini tanımla; yalnız RPN sıralamasına dayanıp yüksek şiddetli riski göz ardı etme.
12. Risk azaltma sonrası residual risk ve doğrulama sonucunu ayrıca kaydet.
13. Mermaid diyagramında veri, güç ve kontrol akışlarını etiketle; çizimi mimari gerçeklikle eşleştir.
14. Test düzeni, cihaz kalibrasyonu, örneklem ve pass/fail kriterini test sonucundan önce tanımla.
15. Simülasyon sonucu ile fiziksel test sonucunu ayrı göster; simülasyonu deney kanıtı olarak sunma.
16. Grafiklerde eksen birimi, örnekleme süresi, filtreleme ve legend bilgisini ekle.
17. Kaynakça için gerçek DOI/URL/yayın bilgisi kullan; erişmediğin kaynağa doğrudan okumuş gibi atıf yapma.
18. LaTeX/Typst/Markdown şablonunda başlık, tablo/şekil numarası ve çapraz referansları otomatik üret.
19. Unicode Türkçe metin ve matematik font uyumluluğunu derlemede kontrol et.
20. PDF veya Word tesliminde render alın; taşan tablo, kesilen denklem, yalnız başlık ve çözünürlük sorunlarını düzelt.
21. Revizyon numarası, değişiklik sahibi ve kapsamını kayıt altına al; ekip katkısını ve kanıt dosyalarını izlenebilir tut.
22. Bilinmeyen veya ölçülmemiş sonucu açık durumla işaretle; puan kazanmak için test, sertifika veya performans uydurma.

## Uygulanabilir şablon

Şablonu proje sürümüne ve gereksinimlerine uyarlayarak doğrula; örnek tek başına üretim onayı değildir.

```markdown
# Kritik Tasarım Raporu
## REQ-CTRL-001 — Haberleşme Kaybı
| Alan | Değer |
|---|---|
| Kaynak | Onaylı sistem gereksinimi |
| Gereksinim | Son geçerli heartbeat sonrası 100 ms içinde güvenli çıkış |
| Doğrulama | TEST-COMM-004; zaman damgalı HIL ölçümü |
| Durum | Test planlandı; sonuç henüz ölçülmedi |

## FMEA
| ID | Hata modu | Etki | S/O/D | Önlem | Kanıt |
|---|---|---|---|---|---|
| FM-01 | Heartbeat kaybı | Kontrol kaybı | Tanımlı ölçeğe göre atanacak | Donanım inhibit + timeout | TEST-COMM-004 |

## Hesap ve Kanıt
Varsayım, birim, kaynak, yöntem ve ölçüm belirsizliği burada açıklanır.
```

## Yasaklı hatalar

Eski yarışma şartnamesi, kaynaksız güvenlik katsayısı, uydurma ölçüm, izlenemeyen gereksinim, ölçeği açıklanmamış FMEA puanı ve ekran görüntüsündeki okunamayan kanıt yasaktır.

## Teslim ve doğrulama

Kaynak dosyaları, gerekli bağımlılık/sürüm bilgilerini, çalıştırılan kontrollerin sonucunu ve çözülemeyen kısıtları birlikte teslim et. İlgili görünür çıktıyı açıp kontrol et; ölçülmeyen veya çalıştırılmayan kontrolü başarılı diye sunma.

