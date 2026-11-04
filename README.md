# Piko

Yerel CLI ajanları için kalıcı görev yönetimi, bağımlılık tabanlı planlama ve kontrollü paralel yürütme.

Piko bir işi planlara ve adımlara ayırır; uygun modeli yönlendirir, sonuçları denetler ve görev geçmişini diskte tutar. Uzun süren işlerde oturumdan devam, kota bekleme ve sınırlı hata toparlama mekanizmaları bulunur. Sürekli insan gözetimini azaltmayı amaçlar; her görevin müdahalesiz tamamlanacağını garanti etmez.

## Temel özellikler

- **Planlama:** Hızlı, Dengeli, Ayrıntılı ve Paralel seçenekleri. Paralel kip ihtiyaç olduğunda en fazla 6 alt planı, toplam 20 adımı tek bağımlılık grafiğinde birleştirir.
- **Kapasiteye göre ilerleme:** Motor örneği başına en fazla 6 adım çalışanı. Her adım kendi bağımlılığını bekler; bağımsız bir kardeş işin bitmesi için toplu bariyer uygulanmaz. Üst program fazları sıralıdır.
- **Kontrollü yazma:** İsteğe bağlı ayrı çalışma kopyaları, bağımsız denetim ve dosya hash karşılaştırmasıyla birleştirme. Aynı dosyanın çelişen değişiklikleri sessizce ezilmez.
- **Kalıcı görev ve teslim:** İş kaydı, plan geçmişi, karar kapıları, sürümlü teslimler ve kullanıcı kabulü. Eksik veya bozuk denetim başarılı tamamlanma sayılmaz.
- **Toparlanma:** Checkpoint ve desteklenen CLI oturum kimlikleriyle devam; geçici hatada sınırlı backoff, kota bekleme ve politika kapsamındaki model yönlendirmesi.
- **Hafıza:** Proje dosya indeksi, kullanıcı kuralları ve görev kayıtları ayrı tutulur. Model özeti bağımsız doğrulama yerine geçmez.
- **Ölçüm:** Token toplamları yalnız sağlayıcının bildirdiği kullanımdır. Bildirilmeyen tüketim bilinmiyor olarak kalır; ücret veya kalan kota tahmini değildir.
- **Arayüz:** Projeler, sohbetler, canlı terminal, Piko/Geliştirici görünümü, görev bildirimleri ve isteğe bağlı Android uzaktan erişim.

Direct yürütücü mevcut CLI oturumlarını kullanır. OpenCode/Evren, Codex, Antigravity ve yerel Ollama yolları yapılandırmaya göre sunulur; her sağlayıcının araç/izin desteği aynı değildir. Bu dağıtımın sağlayıcı izin listesi Evren ile sınırlıdır; katalogda görünmek yürütme yetkisi vermez. Ayrıntılar [motor kılavuzunda](agent-manager/README.md).

## Windows kurulumu

Python 3.12+, Git ve paketlenmiş MCP araçları için Node.js/npm gerekir. Kullanacağınız CLI ayrıca kurulmuş ve hesabı açılmış olmalıdır.

```powershell
git clone https://github.com/eymenarabaci/piko.git
cd piko
.\setup.cmd
.\desktop.cmd
```

Başlatıcı tarayıcıyı açar; varsayılan adres `http://127.0.0.1:8765` olur. Port başka kopyaya aitse seçilen adresi kullanın. Aynı veri klasörüne birden fazla servis bağlamayın. Kurulum sağlayıcı hesabı veya API anahtarı oluşturmaz.

`setup.sh` Python bağımlılıkları için yardımcıdır; Windows Job Object, CAO ve Android yolları Linux/macOS üzerinde aynı doğrulamaya sahip değildir.

## Günlük kullanım

1. Projeyi ve çalışma klasörünü seçin, hedefi ve kabul ölçütlerini yazın.
2. Planlama kademesini seçin. Bağımsız işleri varsa **Paralel** kullanın; yazma iznini ayrıca verin.
3. Plan, terminal ve iş kaydından ilerlemeyi izleyin. Karar veya kullanıcı testi gerektiren durumda yanıt verin.
4. Teslimi çıktı ve test kanıtlarıyla değerlendirin; kabul edin veya düzeltme isteyin.

Paralellik zorunlu değildir. Aynı dosyayı değiştiren işler birleştirmede çatışabilir; bağımlı bir son doğrulama adımı birleşmiş ürünü test etmelidir.

## Güvenlik ve uzaktan erişim

Varsayılan kullanım yereldir. `remote.cmd` dış arayüzde dinleyebilir; uzaktan erişim bearer token ve tercihen Tailscale gibi özel ağ gerektirir. Genel internete doğrudan yayın için sertleştirilmiş çok kullanıcılı hizmet değildir.

Yazma izni, köprü onayları ve kaynak kilitleri korunur. Ayrı klasör/kopya **işletim sistemi sandbox'ı değildir**; terminalin ve harici araçların hesap yetkileri geçerlidir. Sırlar, oturum verileri ve yerel veritabanları GitHub'a gönderilmemelidir. Dış servis etkilerinde exactly-once garantisi yoktur; belirsiz sonuç yeniden uygulama yerine doğrulama gerektirir.

## Belgeler ve doğrulama

- [Çalışma mimarisi](agent-manager/docs/ARCHITECTURE.md)
- [Paralel alt planlar ve kontrollü birleştirme](agent-manager/docs/isolated-parallel.md)
- [İş kaydı ve teslim akışı](agent-manager/docs/WORKFLOW.md)
- [Bu sürümün doğrulaması](agent-manager/docs/VALIDATION.md)
- [Değişiklikler](CHANGELOG.md)

```powershell
cd agent-manager
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

Birim/entegrasyon testlerindeki sahte sağlayıcılar gerçek LLM başarısı veya uzun süre kesintisiz otonomi kanıtı değildir. Kontrollü süreç deneyleri, gerçek sağlayıcı denemeleri ve normal testler ayrı raporlanır.

## Depo düzeni

| Dizin | İçerik |
| --- | --- |
| `agent-manager/` | FastAPI, motor, web arayüzü ve testler |
| `agent-remote-android/` | İsteğe bağlı Android istemcisi |
| `vendor/cao-upstream/` | Paketlenmiş CAO kaynağı |
| `skills/`, `tools/` | Beceri ve araç paketleri |
| `workspace/` | Yerel çalışma alanı; kullanıcı içeriği yayımlanmaz |

Uygulama içi güncelleme yalnız desteklenen, temiz Git klonunda ve aktif iş yokken uygulanır; veri yedeği alınır. Geliştirme kopyasındaki değişiklikleri çalışan servise aktarmak ayrı bir dağıtım işlemidir.

[MIT Lisansı](LICENSE).
