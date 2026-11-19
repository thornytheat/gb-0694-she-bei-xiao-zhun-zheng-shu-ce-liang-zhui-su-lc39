from app.config import LEDGER_SAMPLE_W1
from app.manifest import make
from app.modules.m8_ledger.rules import evaluate
from app.numeric import serial


def preview(text=None, as_of=None, previous=None):
    """无状态试算：发布前在页面/API核对变更依据，不落库。"""
    if text is None:
        text = LEDGER_SAMPLE_W1.read_text(encoding='utf-8-sig')
    if not isinstance(text, str):
        from app.errors import DomainError
        raise DomainError('TYPE', 'CSV必须是文本')
    result = evaluate(text, as_of=as_of, previous=previous)
    result['manifest'] = make(text, as_of=result['as_of'], kind='ledger')
    return serial(result)