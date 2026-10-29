---
name: 16-eda-schematic-pcb-generator
description: "KiCad MCP, kiutils/SKIDL, tscircuit veya EasyEDA ile gerçek şema/PCB ve doğrulanmış üretim çıktıları üret."
---

# eda schematic pcb generator

Bu skill ilgili alan işi istendiğinde uygulanır. Çıktıları kullanıcının kapsamına göre üret; dış sistemlere yayınlama veya üretim değişikliği için mevcut yetki sınırlarını koru. Sürüm bağımlı API'leri resmi kaynak ve mevcut araçlarla doğrula.

## Mühendislik kuralları

1. Teslim türünü .kicad_sch/.kicad_pcb, EasyEDA JSON, Gerber, BOM, Pick & Place veya STEP olarak belirle; metin açıklamasıyla işi tamamlanmış sayma.
2. KiCad sürümü, kicad-cli yolu, sembol/footprint kütüphaneleri ve proje formatı uyumunu doğrula.
3. KiCad MCP hazırsa mevcut araç şemalarını incele; desteklemediği çizim yeteneğini varsayma.
4. kiutils S-expression düzenler, SKIDL bağlantı/netlist üretir; netlisti okunabilir şema çizimiyle eş tutma.
5. Sembol pin numarası, elektriksel pin tipi ve footprint pad eşleşmesini datasheet ile denetle.
6. Şemayı grid üzerinde yerleştir; sembol ve metin çakışmasını önlemek için bounding-box planı yap.
7. Sinyali soldan sağa, güç dağıtımını üstten alta düzenle; farklı akış gerekiyorsa açık etiket kullan.
8. Net label isimlerini tek kaynakta tanımla; benzer adların yanlış birleşmesi ve etiket konumunu görsel denetle.
9. Güç girişleri, PWR_FLAG ve no-connect işaretlerini ERC anlamıyla kullan; uyarı susturmak için rastgele ekleme.
10. Her entegrede 100 nF ve gerekirse 10 µF yerel rezerv kapasitörü başlangıç yaklaşımıdır; datasheet, ESR ve frekans gereksinimi önceliklidir.
11. Dekuplaj kapasitörlerini besleme pinine yakın, kısa dönüş yolu ile yerleştir; miktar tek başına yeterli değildir.
12. KiCad ERC/DRC komutlarını kurulu sürümde --help ile doğrula; rapor ve çıkış kodunu birlikte incele.
13. tscircuit için context7 ve kurulu tsci --help ile desteklenen export/build seçeneklerini kontrol et; varsayımsal CLI bayrağı uydurma.
14. React-to-PCB devresinde board, chip, mosfet, capacitor ve trace bileşenlerinin kurulu sürüm props sözleşmesini doğrula.
15. Autoroute çıktısını son tasarım sayma; return path, clearance, via ve yüksek di/dt döngülerini incele.
16. EasyEDA Pro MCP akışında gerçek LCSC Cxxxx kodunu stok/datasheet ve paket boyutuyla doğrula; tahmini parça kodu üretme.
17. IPC-2221 yol genişliği hesabında akım, bakır kalınlığı, izinli sıcaklık artışı ve iç/dış katman ayrımını belirt; modern stackup için IPC-2152 verilerini de değerlendir.
18. Creepage/clearance için çalışma gerilimi, kirlilik derecesi, malzeme ve geçerli standardı tanımla; basit akım hesabını izolasyon hesabı sayma.
19. PCB stackup, bakır ağırlığı, via tipi, üretici minimumları ve kart sınırını yönlendirme öncesi ayarla.
20. Motor/güç devresinde yüksek akım halkası, Kelvin sense, termal via ve gate dönüşünü ayrı planla.
21. BOM'da refdes, değer, footprint, MPN ve miktarı; Pick & Place'de birim, yüz, rotasyon ve origin bilgisini doğrula.
22. Gerber/Excellon ve STEP çıktılarını bağımsız viewer ile aç; katman, delik, silkscreen ve mekanik yönü kontrol et.
23. ERC/DRC kalan bulgularını dosya/konum ve gerekçeli waiver ile raporla; çalıştırılamayan kontrolü geçti olarak yazma.
24. Teslimde kaynak proje, üretim çıktıları, sürümler ve kontrol raporlarını birlikte sakla; fiziksel üretim öncesi kritik pin/polariteyi yeniden denetle.

## Uygulanabilir şablon

Şablonu proje sürümüne ve gereksinimlerine uyarlayarak doğrula; örnek tek başına üretim onayı değildir.

```powershell
# KiCad kurulu sürümünün seçeneklerini önce doğrula.
kicad-cli sch erc --help
kicad-cli pcb drc --help
# Yaygın güncel CLI biçimi; help çıktısı bu seçenekleri doğrulamalı.
kicad-cli sch erc --exit-code-violations -o erc.rpt circuit.kicad_sch
if ($LASTEXITCODE -ne 0) { throw "ERC raporunu incele: erc.rpt" }
kicad-cli pcb drc --exit-code-violations -o drc.rpt circuit.kicad_pcb
if ($LASTEXITCODE -ne 0) { throw "DRC raporunu incele: drc.rpt" }
tsci --version
tsci --help
# tscircuit export komutunu kurulu CLI help ve resmi dokümantasyonundan seç.
```

## Yasaklı hatalar

Netlisti tamamlanmış şema diye teslim etme, sahte LCSC kodu, ERC/DRC çalışmadan geçti iddiası, kütüphanesiz rastgele pin numarası, denetlenmemiş autoroute, her entegreye datasheet dışı zorunlu kapasitör ve uydurma tsci export komutu yasaktır.

## Teslim ve doğrulama

Kaynak dosyaları, gerekli bağımlılık/sürüm bilgilerini, çalıştırılan kontrollerin sonucunu ve çözülemeyen kısıtları birlikte teslim et. İlgili görünür çıktıyı açıp kontrol et; ölçülmeyen veya çalıştırılmayan kontrolü başarılı diye sunma.

