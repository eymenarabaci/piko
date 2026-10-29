# Agent Remote (Android Companion APK)

**Agent Manager** için özel olarak geliştirilmiş, Codex Remote / Cursor Companion tarzı harici Android uzaktan kontrol ve izleme uygulaması.

---

## 🌟 Özellikler

- 📱 **Mobil Görev Yönetimi:** Telefondan prompt yazıp veya sesle dikte edip yeni ajan görevi başlatma.
- ⚡ **Canlı Müdahale & Onay:** Ajan bir plan hazırladığında telefona gelen ekrandan tek dokunuşla **"Planı Onayla ve Yürüt"**, **"Devam Et"** veya **"İptal Et"**.
- 📜 **Canlı Terminal & Log Akışı:** CLI ve alt işçilerin (Ollama, Codex, AGY, CAO) çıktılarını anlık izleme.
- 🖥️ **Donanım ve Model Durumu:** RTX 3060 VRAM, yerel modeller (Qwen 2.5 Coder 7B, Llama 3.2 3B, DeepSeek R1 8B) ve günlük token harcaması.
- 🔒 **Güvenli Dış Ağ Erişimi:** Tailscale (WireGuard) üzerinden şifreli bağlantı ve Bearer Token yetkilendirmesi.

---

## 🚀 3 Adımda Hızlı Kurulum

### 1. Bilgisayarda Uzaktan Modu Başlatın
`agent manager/` klasöründeki **`remote.cmd`** dosyasını çalıştırın.
Sunucu `0.0.0.0:8765` üzerinde dinlemeye başlar ve ekranda mobil erişim tokenınızı gösterir.

### 2. Tailscale ile Cihazları Eşleyin
1. Bilgisayarınıza [Tailscale](https://tailscale.com) kurun ve oturum açın.
2. Android telefonunuza Google Play Store'dan **Tailscale** yükleyin ve aynı hesapla giriş yapın.
3. Artık bilgisayarınız ve telefonunuz dünyanın neresinde olursanız olun (4G/5G dahil) birbirini görür. Bilgisayarınızın Tailscale IP'si `100.x.y.z` formatındadır.

### 3. APK'yı Yükleyin ve Bağlanın
- **APK Dosyası:** `AgentRemote.apk` dosyasını telefonunuza yükleyin.
  *(Ayrıca web arayüzündeki **Mobil & Tailscale** sekmesinden tek tıkla da indirebilirsiniz: `http://127.0.0.1:8765/ui/AgentRemote.apk`)*
- Uygulamayı açın, **Bağlantı** sekmesine:
  - **Sunucu:** `http://<Tailscale_IP>:8765` (veya evdeki Wi-Fi için `http://127.0.0.1:8765`)
  - **Mobil Token:** Web arayüzünde veya `remote.cmd` ekranında yazan token
- **Kaydet ve Bağlan**'a dokunun. Artık tüm sistemi cebinizden kontrol edebilirsiniz!

---

## 🛠️ Yeniden Derleme (Build)

Uygulamada bir değişiklik yaptığınızda APK'yı sıfırdan derlemek için:

```powershell
cd '.\agent-remote-android'
& '..\agent manager\.venv\Scripts\python.exe' build_apk.py
```
Çıktı doğrudan `AgentRemote.apk` ve `agent-manager/ui/AgentRemote.apk` dosyalarına yazılır ve imzalanır.
