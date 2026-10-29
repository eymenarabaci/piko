---
name: resource-cleanup
description: Track and release disposable processes, browser sessions, renderers, local servers and agent helpers created for a task. Use while launching these resources and during task completion, or when the user requests Codex RAM/MCP cleanup. Preserve shared servers and active user applications.
---

# Göreve ait kaynakları temizle

Kullanıcının bilgisayarında görev yardımcılarının birikmesini önle. Bu beceri sürekli çalışan bir RAM temizleyici değildir; model tarafından kullanıldığında görev yaşam döngüsünü yönetir. Ana ChatGPT/Codex uygulamasının belleğini sınırlandıramaz veya çalışma zamanı tarafından paylaşılan MCP'lerin otomatik kapanmasını garanti edemez.

## Başlarken

- Mevcut oturumları ve araçları yeniden kullan. Aynı sunucuyu yalnız süreç adı tekrar ediyor diye kopya sayma: npm/uv başlatıcısı ve onun alt süreci tek sunucu zinciri olabilir.
- Alt ajanı yalnız görevin anlamlı bağımsız işi bunu gerektiriyorsa aç. Özellikle pil/RAM tanılarken tanılamanın kendi yükünü artırma.
- Kendi başlattığın geçici kaynakları `work/resource-ledger.json` içinde kaydet: PID, UTC başlangıç zamanı, yürütülebilir dosya yolu, ebeveyn PID, görev sahibi, paylaşılıp paylaşılmadığı ve kapanabilirlik. Hassas komut satırlarını veya anahtarları kaydetme.
- MCP ayarını kalıcı değiştirmek bütün sonraki görevleri etkiler. Yalnız kullanıcının yetkilendirdiği kapsamda değiştir, yedekle ve TOML'yi doğrula. Gerekli bir araç kapalıysa görev kapsamında yeniden etkinleştirilmesini değerlendir; etkinleştiğini doğrulamadan kullanılabilir deme. Desteklenmeyen boşta kapanma ayarı uydurma.

## İş biterken

1. Kaynağın işi tamamlandı mı ve başka oturum tarafından kullanılıyor mu kontrol et. Bir alt ajanın tamamlanması, onun MCP süreçlerinin kendiliğinden kapandığının kanıtı değildir.
2. Önce ortamın desteklediği kapatma/dispose/close araçlarını kullan. Yalnız interrupt sunuluyorsa bunu kaynakların serbest bırakıldığı şeklinde raporlama.
3. Süreç kapatma yetkisi mevcutsa yalnız kaydedilmiş, göreve özel, tamamlanmış ve paylaşımsız süreçleri kapat. PID, başlangıç zamanı, dosya yolu ve ebeveyn PID'yi yeniden doğrula. PID veya süreç adına dayanarak toplu sonlandırma yapma.
4. `scripts/close-owned-processes.ps1 -ManifestPath <dosya>` varsayılan olarak salt okunur plan verir. Gerçek kapatma için yalnız yetkilendirilmiş kayıtlarla `-Apply` kullan. Betik süreç ağacını tahmin ederek genişletmez; kapanacak her çocuk da ayrı kayıtlı olmalıdır.
5. Kapanmayan, yeniden başlayan veya sahipliği belirsiz kaynakları koru ve kısa bildir. Kullanıcı istemedikçe ChatGPT/Codex, EasyEDA, Chrome Remote Desktop, antivirüs, lisans/SQL, donanım yönetimi, VPN veya SSH hizmetlerini kapatma. Uyku, kapak ve güç tuşlarını kaynak temizliği sırasında değiştirme.
6. İş sonunda canlı süreçleri ve gerekiyorsa kullanılabilir fiziksel RAM'i yeniden ölç. Özel commit'i fiziksel RAM, toplam working set'i çakışmasız RAM toplamı gibi sunma. Farkın sistemin eşzamanlı hareketlerinden etkilenebileceğini belirt.

Önbellek/standby listelerini zorla boşaltma, çalışma kümelerini budama, sıkıştırmayı veya pagefile'ı kapatma. Bunlar görev yardımcılarını kapatmanın yerine geçmez.

## Manifest biçimi

`owner_task` dolu olmalı. Her `resources` kaydı şu alanları taşımalı:

```json
{
  "owner_task": "current-task-id",
  "resources": [
    {
      "pid": 12345,
      "created_utc": "2026-10-16T00:00:00.0000000Z",
      "executable_path": "C:\\tools\\python.exe",
      "parent_pid": 1000,
      "state": "completed",
      "disposable": true,
      "shared": false
    }
  ]
}
```

Örnek kimlikleri canlı süreçlere uyarlamadan kullanma. Betiğin teknik kimlik kontrolü, görev sahipliğinin veya kullanıcı yetkisinin yerine geçmez.

## Kalıcı ayar ile çalışan oturum ayrımı

Bir MCP için `enabled = false` yazılması, o anda çalışan uygulamanın eski oturum yapılandırmasını hemen bıraktığını kanıtlamaz. Dosyayı doğrula, mümkünse yeni bir istemcinin gördüğü etkin durumu kontrol et ve mevcut süreçlerin yeniden oluşup oluşmadığını gözle. Ortam desteklenen bir yapılandırma yenileme aracı sunuyorsa onu kullan. Yalnız belgelendiği için mevcut oturuma erişilemeyen bir API'yi çalışmış sayma. Yenileme erişilebilir değilse uygulamanın sonraki yeniden açılışında okunacak ayarı ve mevcut oturumdaki sınırı ayrı raporla; sonsuz süreç öldürme döngüsü veya izinsiz arka plan bekçisi kurma.
