import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from manager.request_routing import classify_request
from manager.schema import RunRequest


@pytest.mark.parametrize(('message', 'expected'), [
    ('Merhaba', 'chat'),
    ('GitHub ve Firebase nasıl çalışıyor?', 'chat'),
    ('Şu hata ne demek? ' + 'diagnostic ' * 90, 'chat'),
    ('Mevcut projede masaüstü uygulaması yapıldı mı, Android giriş ve GitHub bağlantısı nasıl çalışıyor?', 'chat'),
    ('Navbar.tsx içindeki buton adını Kaydet yap', 'chat'),
    ('Bu küçük hatayı düzelt', 'chat'),
    ('Yeni bir plan oluştur', 'plan'),
    ('Bunların hepsini uygula', 'plan'),
    ('Firebase ve GitHub entegrasyonunu ekle', 'plan'),
    ('Sıfırdan masaüstü uygulama istiyorum', 'plan'),
    ('1. Oturum kur\n2. API geliştir\n3. Mobil uygulama oluştur', 'plan'),
    ('Kullanıcıların profil bilgilerini yönetici sonradan düzenleyebilsin, profil fotoğrafı da eklensin', 'plan'),
    ('Drive klasörüne işe özel klasör açılsın ve GitHub ile otomatik senkronizasyon çalışsın', 'plan'),
    ('Pencereli masaüstü uygulamasını ve mobil APK uygulamasını oluştur; arayüzü de yeniden düzenle', 'plan'),
])
def test_request_route(message, expected):
    assert classify_request(message) == expected


def _assist_namespace():
    source = ast.parse((Path(__file__).parents[1] / 'manager/app.py').read_text(encoding='utf-8'))
    handler = next(node for node in ast.walk(source) if isinstance(node, ast.AsyncFunctionDef) and node.name == 'assist')
    handler.decorator_list = []
    calls = []

    async def create(request):
        calls.append(request)
        return {'id': 'new-plan', **request.model_dump()}

    async def require(run_id):
        return {'id': run_id, 'workspace': '.', 'status': 'running'}

    async def chat(*args):
        calls.append('chat')
        return {'reply': 'answer', 'model_id': 'selected'}

    ns = {'Request': object, 'RunRequest': RunRequest, 'classify_request': classify_request,
          'require': require, 'orchestrator_chat': chat, 'direct_chat': chat,
          'app': SimpleNamespace(state=SimpleNamespace(
              attachments=SimpleNamespace(resolve_many=lambda ids: []),
              engine=SimpleNamespace(create=create))),
          'HTTPException': RuntimeError}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])), '<assist-test>', 'exec'), ns)
    return ns, calls


def _run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError('Unexpected external async operation')


def test_complex_request_creates_approval_plan_in_existing_conversation():
    ns, calls = _assist_namespace()
    async def json_body():
        return {'message': 'Firebase ve GitHub entegrasyonunu ekle', 'model_id': 'selected',
                'conversation_run_id': 'a' * 32, 'allow_writes': True}
    response = _run_immediate(ns['assist'](SimpleNamespace(json=json_body)))
    assert response['kind'] == 'plan' and response['requires_approval'] is True
    assert calls[0].mode == 'plan'
    assert calls[0].planner_model == 'selected'
    assert calls[0].conversation_run_id == 'a' * 32
    assert calls[0].allow_writes is True


def test_simple_question_does_not_invoke_planner():
    ns, calls = _assist_namespace()
    async def json_body():
        return {'message': 'Bu hata ne demek?', 'conversation_run_id': 'a' * 32}
    response = _run_immediate(ns['assist'](SimpleNamespace(json=json_body)))
    assert response['kind'] == 'chat'
    assert calls == ['chat']


def test_detailed_level_routes_substantial_work_to_plan():
    assert classify_request('Kullanıcı yönetimi ekranını düzenleyip kalıcı profil bilgilerini ekle', 'detailed') == 'plan'
    assert classify_request('Bu mimari neden yavaş çalışıyor?', 'detailed') == 'chat'
