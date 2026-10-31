# 2026-10-31 doğrulaması

- Python 3.12.10; FastAPI 0.162.0; LangGraph 1.2.17; Pydantic 2.14.1; pytest 9.1.1.
- Komut: `.venv/Scripts/python.exe -m pytest tests -q` — 138 test başarılı (29,44 saniye).
- Komut: `node --check ui/app.js` — başarılı.
- Testler gerçek LangGraph/checkpoint ve SQLite akışını kullanır. CLI/model yanıtları test sağlayıcılarıyla simüle edilir; gerçek ücretli model çağrısı veya token tasarrufu A/B ölçümü yapılmadı.
- Faz sırası, önceki iş denetlenmeden yeni plan açılmaması, hedefli düzeltme, kullanıcı testinde bekleme/devam ve eski adımların tekrar yürütülmemesi doğrulandı.
- Mesaj sırası, istemci tekrarları, canlı gönderim rezervasyonu ve sonraki adıma fallback doğrulandı.
- Yerel Playwright arayüz kontrolü: aktif işte teslim seçenekleri, mesaj kartları, kullanıcı test yanıtı ve tek seferlik zamanlama. Kanıtlar çalışma alanındaki `work` kayıtlarında tutulur.
- Rol ayrımı prompt tabanlıdır. CAO canlı terminal girdisi destekler; doğrudan CLI mesajı sonraki sınırda alır. Token kazancı henüz ölçülmüş sonuç değildir.
