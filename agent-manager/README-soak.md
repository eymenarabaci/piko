# PikoSoak — Faz 9 dayanıklılık koşusu

`tools/soak_run.py` gerçek zaman beklemeden **sahte saatle** bir soak koşusu
yapar. Her simüle saatte iş kayıtları, ofis/iletişim paketleri, rastgele
onay/ret turu, çök-kalk, kota tükenmesi ve kilit çakışması üretilir. Sonuçlar:

- `work/soak/saat.json` — her saat sonunda güncellenen makine raporu
- `work/soak/saat-NN.json` — saatlik anlık görüntüler
- `work/soak/SOAK_RAPOR.md` — çarpıcı özet
- `work/soak/ozet.json` — koşunun tamamı

Koşu canlı model, posta veya takvim API'sine gitmez; yalnız yerel SQLite
fixture'larını kullanır. `work/` git tarafından yok sayılır.

## Elle çalıştırma

```powershell
cd 'C:\Users\eymen\Desktop\AI-Orchestrator\agent-manager'
.\.venv\Scripts\python.exe tools\soak_run.py --hours 24 --jobs 48 --seed 9
```

Kısa doğrulama (2 saat):

```powershell
.\.venv\Scripts\python.exe tools\soak_run.py --hours 2 --jobs 8 --seed 11 --fault-every 1
.\.venv\Scripts\python.exe -m pytest tests\test_soak_harness.py -q
```

Seçenekler: `--approval-rate`, `--accept-rate`, `--fault-every`, `--workdir`.
`--fault-every N` her N saatte bir çök-kalk döngüsü ekler; `1` her saat demektir.

## Zamanlanmış görev (PikoSoak)

Aşağıdaki komut her gece 02:00'de 24 saatlik soak koşusunu başlatır. Sunucu
başlatmaz, 8767/8765 portlarına dokunmaz; yalnız betiği çalıştırır.

```powershell
$python = 'C:\Users\eymen\Desktop\AI-Orchestrator\agent-manager\.venv\Scripts\python.exe'
$script = 'C:\Users\eymen\Desktop\AI-Orchestrator\agent-manager\tools\soak_run.py'
$action = "`"$python`" `"$script`" --hours 24 --jobs 48 --seed 9"
schtasks /Create /TN "PikoSoak" /TR $action /SC DAILY /ST 02:00 /F /RL HIGHEST
```

Görevi hemen denemek ve durumunu görmek:

```powershell
schtasks /Run /TN "PikoSoak"
schtasks /Query /TN "PikoSoak" /V /FO LIST
```

Kaldırmak:

```powershell
schtasks /Delete /TN "PikoSoak" /F
```

> Not: Görev, kullanıcı oturum açmamışken de çalışsın istenirse
> `schtasks /Create ... /RU "$env:USERNAME" /RP *` ile parola sorulacak biçimde
> oluşturun. Raporlar `agent-manager\work\soak\` altında birikir.
