# ◈ AI-Orchestrator (Agent Manager)

> **Gelişmiş Çoklu Ajan ve Görev Orkestrasyon Sistemi (LangGraph & ConPTY Destekli)**
> Yerel modeller (Ollama), Antigravity (Gemini), Codex (OpenAI) ve özel CLI araçlarını tek bir çalışma alanında birleştiren, açık kaynaklı yeni nesil yapay zeka ajan orkestratörü.

---

## 🌟 Öne Çıkan Özellikler

- 🧠 **Kalıcı Birleşik Hafıza (Compounding Memory):** Proje bazlı kuralları, kullanıcı tercihlerini, mimari kararları ve içgörüleri otomatik öğrenir ve bağlama enjekte eder.
- ⚡ **Proaktif Görev Tetikleyicileri (Proactive Triggers & Daemon):** Zamanlanmış (cron/aralık) veya dosya değişikliklerini (watchdog) izleyip otomatik ajan görevleri yürüten arka plan servisi.
- 📁 **Çoklu Proje & Çalışma Alanı Yönetimi:** Codex tarzı izole proje klasörleri, hızlı proje geçişi ve projeye özel sohbet geçmişi.
- 📊 **Proje Metrikleri HUD Barı:** Her proje için harcanan toplam token, tamamlanan plan adımları ve aktif tetikleyicileri anlık takip edin.
- 🔄 **Dinamik CLI & Otomatik Model Tespiti:** Sistemde kurulu Ollama modellerini, Antigravity (`agy`), Codex veya Claude CLI'larını her oturumda otomatik tanır.
- 📱 **Agent Remote (Android Companion Uygulaması):** Telefonunuzdan sesle görev başlatma, planları tek tıkla onaylama/yürütme, canlı terminal akışını izleme ve Tailscale VPN üzerinden güvenli uzaktan kontrol.
- 🛠️ **Zengin Beceri ve MCP Ekosistemi:** Genomik, moleküler biyoloji, veri analizi, kod düzenleme ve sistem otomasyon araçları dahildir.

---

## 🚀 Hızlı Başlangıç (Windows)

### 1. Kurulum
Depoyu klonlayın ve kök dizindeki kurulum dosyasını çalıştırın:
```cmd
git clone https://github.com/eymenarabaci/AI-Orchestrator.git
cd AI-Orchestrator
setup.cmd
```
*Gerekli tüm Python kütüphaneleri otomatik olarak `.venv` sanal ortamına kurulacaktır.*

### 2. Başlatma
- **Yerel Masaüstü Modu:** `desktop.cmd` dosyasına çift tıklayın. Tarayıcınızda `http://127.0.0.1:8765` açılacaktır.
- **Uzaktan Erişim / Mobil Modu:** `remote.cmd` dosyasını çalıştırın (Sunucu dış ağlara ve Tailscale IP'nize açılır).

---

## 📱 Android Uzaktan Kontrol Kurulumu

1. `desktop.cmd` veya `remote.cmd` ile sistemi başlatın.
2. Web arayüzünün sol menüsündeki **Mobil Erişim** sekmesini açın.
3. **AgentRemote.apk** dosyasını telefonunuza indirin ve kurun *(Uygulama `agent-remote-android/AgentRemote.apk` konumunda hazır olarak da yer almaktadır)*.
4. Bilgisayarınızda ve telefonunuzda **Tailscale** açarak eşleyin.
5. Telefondaki uygulamaya Tailscale IP'nizi ve ekranda beliren **Mobil Bearer Token**'ınızı girip bağlanın.

---

## ⚙️ Kendi CLI ve Modellerinizi Bağlama

Sistem, ortamınızda çalışan CLI araçlarını ve Ollama modellerini otomatik olarak keşfeder:
- **Ollama:** `ollama run qwen2.5-coder:7b` veya benzeri bir model indirdiğinizde, arayüzdeki **↻ Modelleri Tara** butonuna basarak modelleri anında listeye ekleyebilirsiniz.
- **Özel CLI Ayarları:** `agent-manager/config/cli_config.json` dosyasını düzenleyerek kendi özel model ve API uç noktalarınızı tanımlayabilirsiniz.

---

## 📂 Proje Yapısı

```
AI-Orchestrator/
├── agent-manager/            # FastAPI sunucusu, planlama motoru ve web arayüzü
│   ├── manager/              # LangGraph, ConPTY, hafıza, tetikleyiciler
│   ├── ui/                   # Modern Web UI (HTML/CSS/JS)
│   ├── config/               # Modeller ve CLI yapılandırması
│   └── tests/                # Otomatik test senaryoları (pytest)
├── agent-remote-android/     # Android Companion mobil uygulaması
├── skills/                   # Dahili beceriler ve araç paketleri
├── tools/                    # MCP sunucuları ve araçları
├── workspace/                # Varsayılan yerel çalışma alanı
├── desktop.cmd               # Masaüstü başlatıcı
├── remote.cmd                # Mobil / Uzaktan başlatıcı
└── setup.cmd                 # Kurulum sihirbazı
```

---

## 📄 Lisans
Bu proje [MIT Lisansı](LICENSE) kapsamında açık kaynak olarak paylaşılmıştır.
