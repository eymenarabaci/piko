"""Bounded, evidence-based review of one completed implementation step."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from .schema import robust_json_decode


class Finding(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    issue: str = Field(min_length=1, max_length=700)
    evidence: str = Field(min_length=1, max_length=700)
    line: int | None = Field(default=None, ge=1)


class Review(BaseModel):
    status: Literal["pass", "fail", "needs_user_test"]
    findings: list[Finding] = Field(default_factory=list, max_length=8)
    test_result: str = Field(default="", max_length=1000)
    question: str = Field(default="", max_length=1000)


def parse_review(output: str) -> Review:
    data, _ = robust_json_decode(output)
    review = Review.model_validate(data)
    if review.status == "fail" and not review.findings:
        raise ValueError("Hata kararı somut bulgu içermiyor.")
    if review.status == "needs_user_test" and not review.question.strip():
        raise ValueError("Kullanıcı testi için soru gerekli.")
    return review


def review_prompt(goal: str, step_title: str, step_prompt: str, output: str) -> str:
    return (
        "Sen bağımsız, salt okunur QA denetçisisin. Dosya değiştirme. "
        "Yalnız ilgili değişiklikleri incele ve gerekiyorsa hedefli test çalıştır. "
        "Raporlanan başarıyı kanıt sayma; ilgili dosya ve gerçek test sonucunu kontrol et. "
        "Açık hata yoksa pass de. Yalnız somut, yeniden üretilebilir hata için fail de; "
        "her bulguda dosya, hata ve kanıt belirt. Kullanıcının fiziksel/öznel testi gerçekten gerekiyorsa "
        "needs_user_test seç ve tek net soru sor. Uzun genel tavsiye verme. "
        "Yalnız JSON döndür: "
        '{"status":"pass|fail|needs_user_test","findings":'
        '[{"path":"...","line":1,"issue":"...","evidence":"..."}],'
        '"test_result":"...","question":"..."}.\n'
        f"Ana hedef: {goal[:2500]}\nAdım: {step_title}\n{step_prompt[:3500]}\n"
        f"Uygulayıcı çıktısı (iddia, kanıt değil): {output[:3500]}"
    )


def repair_prompt(goal: str, step_title: str, step_prompt: str, findings: list[Finding]) -> str:
    return (
        "Sen aynı adımın hedefli düzeltme uygulayıcısısın. Yalnız aşağıdaki doğrulanmış bulguları düzelt. "
        "İlgisiz değişiklik, test veya yeni öz denetim turu başlatma; düzeltip teslim et. "
        f"Ana hedef: {goal[:2000]}\nAdım: {step_title}\n{step_prompt[:2500]}\n"
        f"Denetçi bulguları: {json.dumps([f.model_dump() for f in findings], ensure_ascii=False)}"
    )
