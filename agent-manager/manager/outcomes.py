"""Reject provider failure banners even when the CLI exits successfully."""
import re

_QUOTA = re.compile(
    r"(?:you[’']?ve|you have).{0,20}(?:hit|reached|exceeded).{0,30}(?:usage|rate|quota|limit)|"
    r"(?:usage|rate)[ _-]?limit.{0,30}(?:reached|exceeded|exhausted)|"
    r"quota[_ -]?exceeded|insufficient[_ -]?quota|resource[_ -]?exhausted|"
    r"(?:individual\s+)?quota\s+(?:reached|exhausted)|resets?\s+in\s+\d|"
    r"too many requests|exceeded your current quota|kullanım kotası.{0,30}(?:dol|ulaş)", re.I)
_ERROR = re.compile(r"^\s*(?:error|fatal|hata)\s*:\s*\S", re.I)


class InfrastructureError(RuntimeError):
    """An unavailable local runtime cannot be repaired by changing the model."""


def is_infrastructure_error(error) -> bool:
    if isinstance(error, InfrastructureError):
        return True
    text = str(error).casefold()
    return any(marker in text for marker in (
        'failed to spawn code-mode host', 'codex cli bulunamadı',
        'antigravity cli bulunamadı', 'windows powershell was not found',
        'çalışma klasörü bulunamadı', 'cao windows motoru hazır değil',
    )) or ('codex-code-mode-host.exe' in text and any(marker in text for marker in (
        'bulunamad', 'bulunamıyor', 'not found', 'cannot find', 'os error 2', 'no such file',
    )))


def ensure_usable_result(result):
    output = str(result.get('output') or '').strip()
    if is_infrastructure_error(output):
        raise InfrastructureError('Yerel araç çalışma ortamı kullanılamıyor; kurulum düzeltilmeden yeniden denemeyin: ' + output[:1200])
    if _QUOTA.search(output):
        raise RuntimeError('Sağlayıcı kullanım kotası veya rate limit hatası: ' + output[:2000])
    if result.get('status') in {'failed', 'error', 'cancelled', 'interrupted'}:
        raise RuntimeError('Sağlayıcı işlemi başarısız: ' + str(result.get('error') or output)[:2000])
    if not output:
        raise RuntimeError('Sağlayıcı kullanılabilir çıktı döndürmedi.')
    if _ERROR.match(output) and len(output) < 2000:
        raise RuntimeError('Sağlayıcı hata çıktısı döndürdü: ' + output)
    return result
