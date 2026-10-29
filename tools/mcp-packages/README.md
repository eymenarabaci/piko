# MCP paketleri

`package.json` ve `package-lock.json` MCP sunucularının sabitlenmiş bağımlılık listesidir. Node.js kuruluysa bu dizinde `npm ci` çalıştırarak paketleri yerel olarak kurabilirsiniz. `node_modules` depoya eklenmez.

Agent Manager, yalnız kurulu paketin giriş dosyası gerçekten mevcutsa onu `/api/personas` yanıtında `installed_unverified` olarak listeler. Bu durum tek başına bağlantı kanıtı değildir. CAO planları, kimlik bilgisi istemeyen `sequential-thinking` ve `context7` araçlarını gerekli Codex/Antigravity adımına özel geçici persona profili üzerinden bağlayabilir. Profil adım sonunda kaldırılır. Doğrudan CLI, AO ve Ollama adımlarına MCP atanmaz. Diğer paketler yalnız kurulu envanteridir; kimlik bilgisi ve çalışma alanı yetkisi gerektiren araçlar otomatik açılmaz.

GitHub, Firebase gibi servisler için kimlik bilgileri kişisel ortam yapılandırmasında tutulmalıdır. Anahtarları bu depoya eklemeyin.
