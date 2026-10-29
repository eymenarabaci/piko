---
name: 02-parametric-cad-3dprint
description: Parametrik atölye parçaları, manifold 3D modeller ve FDM baskı için doğrulanmış dışa aktarma üretir.
---

# Parametrik CAD ve 3D Baskı

## Mühendislik kuralları
1. OpenJSCAD, Three.js CSG, Blender bpy ve slicer API sürümlerini resmi dokümantasyon veya Context7 ile doğrula.
2. Tüm boyutları mm cinsinden tek parametre şemasında tut; alt/üst limitleri ve türetilmiş ölçüleri tanımla.
3. CAD modeli, görsel mesh ve üretim mesh'ini ayır; viewport tessellation kalitesi üretim doğruluğunu belirlemesin.
4. Boolean operasyonlarından önce dönüşümleri uygula; eşdüzlemli yüzeylerden kaçın ve kontrollü epsilon kullan.
5. CSG sonucunda negatif hacim, sıfır kalınlık ve ayrık adaları kontrol et; self-intersection'ları çöz.
6. Watertight kontrolünde her manifold kenarın iki yüze ait olduğunu, normal yönünü ve pozitif hacmi doğrula.
7. STL birimsizdir; dışa aktarma manifestinde mm birimini, bounding box ve parça ölçülerini kaydet.
8. OBJ/GLB için koordinat eksenleri, materyal ve scale dönüşümlerini açıkça yönet; yeniden içe aktararak ölç.
9. STEP için B-rep destekli CAD çekirdeği kullan; mesh STL/OBJ'yi dosya uzantısı değiştirerek gerçek STEP sayma.
10. Blender bpy otomasyonunda seçili nesne bağlamına bağımlılığı azalt; headless çalışmada sahneyi açıkça oluştur.
11. Modüler pegboard için delik çapı, merkez aralığı, panel kalınlığı ve montaj yönünü gerçek referanstan ölç.
12. Geçme clearance'ı başlangıçta 0.20–0.30 mm olarak dene; bunun toplam boşluk mu yüzey başına boşluk mu olduğunu belirt.
13. PLA, PETG ve TPU için fit kuponu bas; shrinkage, nozzle, flow ve extrusion genişliğine göre toleransı kalibre et.
14. 45 derece desteksiz overhang değerini başlangıç varsayımı say; soğutma, katman yüksekliği ve malzemeyle test et.
15. Baskı yönünü katmanlar arası çekme dayanımına göre seç; yük altında Z yönü ayrılmasını analiz et.
16. İç köşelerde fillet, girişlerde pah kullan; temas alanını ve kilit mekanizması hareketini koru.
17. Minimum duvarı nozzle çapının tam extrusion çizgileriyle uyumlu seç; sadece nominal CAD kalınlığına güvenme.
18. Vida yuvası, heat-set insert, snap-fit ve TPU menteşe için üretici ölçüsü ile test parçası kullan.
19. Bridge uzunluğu, ilk katman elephant foot ve delik ovalleşmesini üretim riskleri olarak incele.
20. Klipper makrolarında firmware yapılandırmasına uygun eksen limitleri, homing ve sıcaklık koşulları kullan; cihazı otomatik hareket ettirmeden önce kapsamı doğrula.
21. OrcaSlicer flow, pressure advance ve sıcaklık kalibrasyonlarını ayrı deneylerle yap; profil ve filament lotunu kaydet.
22. Teslimatta parametrik kaynak, STL/uygun format, baskı yönü, malzeme ve doğrulama ölçülerini birlikte sun.

## Kod şablonu
```js
const {primitives, booleans, transforms} = require('@jscad/modeling');
const main = () => {
  const base = primitives.cuboid({size: [40, 30, 6]});
  const hole = primitives.cylinder({radius: 2.2, height: 8, segments: 64});
  return booleans.subtract(base, transforms.translate([12, 0, 0], hole));
};
module.exports = {main};
```
Şablon geometrisi merkezlidir; export sonrası manifold ve gerçek delik ölçüsünü kontrol et.

```python
import bpy
# Yeni/ayrılmış sahnede üret; mevcut kullanıcı nesnelerini silme.
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
part = bpy.context.object
part.name = 'mount_mm'
part.dimensions = (40, 30, 6)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
# Burada koordinatlar mm olarak tanımlıdır; exporter birimini ayrıca doğrula.
bevel = part.modifiers.new(name='EdgeRelief', type='BEVEL')
bevel.width = 0.5
bevel.segments = 3
bpy.context.view_layer.objects.active = part
bpy.ops.object.modifier_apply(modifier=bevel.name)
```
Blender 3.x/4.x STL exporter operatörleri farklı olabilir; export komutunu kurulu sürümden doğrula.

## Yasaklı hatalar
- 0.25 mm toleransı bütün yazıcı ve malzemelerde garanti olarak sunma.
- Fillet görünümünü yalnızca shader ile üretip baskı geometrisine uygulanmış sayma.
- Mesh'ten çıkartılmış yüzey modelini hassas parametrik STEP olarak raporlama.
- Test baskısı yapılmadan yük taşıma veya snap-fit ömrü doğrulandı deme.
