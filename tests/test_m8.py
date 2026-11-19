"""M8 台账发布：状态分桶、归属保留、幂等版本、回滚与复核。"""
import pytest

from app.config import LEDGER_SAMPLE_W1, LEDGER_SAMPLE_W2, LEDGER_SAMPLE_W3
from app.modules.m8_ledger.rules import evaluate
from app.modules.m8_ledger import store


@pytest.fixture(autouse=True)
def isolated_ledger_db(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB', str(tmp_path / 'ledger.sqlite3'))
    yield


@pytest.fixture
def week1():
    return LEDGER_SAMPLE_W1.read_text(encoding='utf-8-sig')


@pytest.fixture
def week2():
    return LEDGER_SAMPLE_W2.read_text(encoding='utf-8-sig')


@pytest.fixture
def week3():
    return LEDGER_SAMPLE_W3.read_text(encoding='utf-8-sig')


AS_OF = '2026-11-19'


def _by_cert(result):
    return {e['证书']: e for e in result['entries']}


# ---------- 规则层：四个状态分桶 ----------

def test_first_batch_all_explicit_buckets(week1):
    result = evaluate(week1, as_of=AS_OF)
    by = _by_cert(result)
    assert by['W1-C01']['状态码'] == 'ACTIVE'
    # valid_to 等于判定日：半开区间 [from,to)，判过期
    assert by['W1-C06']['状态码'] == 'EXPIRED'
    assert by['W1-C07']['状态码'] == 'EXPIRED'
    assert result['conserved'] is True
    assert all(e['来源批次'] == '本批' for e in result['entries'])


def test_missing_is_distinct_from_revoked(week1, week2):
    r1 = evaluate(week1, as_of=AS_OF)
    r2 = evaluate(week2, as_of=AS_OF, previous=r1['entries'])
    by = _by_cert(r2)
    # W1-C03 本批未出现，未撤销未过期 -> 缺失待核实，绝不下架
    assert by['W1-C03']['状态码'] == 'MISSING'
    assert '待核实，未下架' in by['W1-C03']['变化来源']
    assert by['W1-C03']['设备'] == 'D1003'  # 设备归属保留


def test_revoked_bucket_and_sticky(week1, week2):
    r1 = evaluate(week1, as_of=AS_OF)
    r2 = evaluate(week2, as_of=AS_OF, previous=r1['entries'])
    assert _by_cert(r2)['W1-C08']['状态码'] == 'REVOKED'
    # 下一批不再出现该证书：撤销粘性，不回退成缺失或在册
    lines = week2.splitlines()
    head, body = lines[0], [ln for ln in lines[1:] if not ln.startswith('W1-C08,')]
    week3_like = '\n'.join([head] + body) + '\n'
    r3 = evaluate(week3_like, as_of=AS_OF, previous=r2['entries'])
    c08 = _by_cert(r3)['W1-C08']
    assert c08['状态码'] == 'REVOKED' and '撤销保留' in c08['变化来源']


def test_expired_bucket_is_half_open_endpoint():
    csv = ('cert,device,alias,parent,valid_from,valid_to,minimum,maximum,unit,'
           'revoked,measured_at,value,measure_unit,version\n'
           'X,D1,,ROOT,2026-01-01,2026-11-19,0,10,V,no,2026-11-19,5,V,1\n')
    result = evaluate(csv, as_of=AS_OF)
    assert _by_cert(result)['X']['状态码'] == 'EXPIRED'


def test_not_yet_valid_is_quarantined_not_published(week2):
    result = evaluate(week2, as_of=AS_OF)
    assert 'W2-C12' not in _by_cert(result)
    assert any(q['cert'] == 'W2-C12' and q['原因码'] == 'NOT_YET_VALID' for q in result['quarantine'])
    assert result['conserved'] is True


# ---------- 别名归并、重叠、冲突、重复 ----------

def test_alias_merges_to_one_device(week1, week2):
    r1 = evaluate(week1, as_of=AS_OF)
    result = evaluate(week2, as_of=AS_OF, previous=r1['entries'])
    by = _by_cert(result)
    # W2-C11 的 device 即 D1002，与沿用上版的 W1-C02 归到同一台设备
    assert by['W2-C11']['设备'] == 'D1002'
    assert by['W1-C02']['设备'] == 'D1002'


def test_overlap_marked_but_does_not_change_bucket(week3):
    r1 = evaluate(LEDGER_SAMPLE_W1.read_text(encoding='utf-8-sig'), as_of=AS_OF)
    r2 = evaluate(LEDGER_SAMPLE_W2.read_text(encoding='utf-8-sig'), as_of=AS_OF, previous=r1['entries'])
    r3 = evaluate(week3, as_of=AS_OF, previous=r2['entries'])
    c13 = _by_cert(r3)['W3-C13']
    assert 'W3-C14' in c13['重叠证书'] and c13['状态码'] == 'ACTIVE'
    assert r3['conserved'] is True


def test_conflicting_definition_quarantined_and_previous_retained(week1, week2):
    r1 = evaluate(week1, as_of=AS_OF)
    r2 = evaluate(week2, as_of=AS_OF, previous=r1['entries'])
    assert any(q['cert'] == 'W1-C02' and q['原因码'] == 'CONFLICT' for q in r2['quarantine'])
    c02 = _by_cert(r2)['W1-C02']
    assert c02['状态码'] == 'MISSING' and '冲突' in c02['标记']


def test_duplicate_full_row_counted_once(week2):
    result = evaluate(week2, as_of=AS_OF)
    assert result['duplicate_rows'] == 1
    assert result['conserved'] is True


def test_dirty_rows_quarantined_with_line_numbers(week2):
    result = evaluate(week2, as_of=AS_OF)
    bad = [q for q in result['quarantine'] if q.get('cert') == 'BAD']
    assert bad and bad[0]['原因码'] == 'MISSING' and isinstance(bad[0]['行号'], int)


def test_change_source_records_origin(week1, week2):
    r1 = evaluate(week1, as_of=AS_OF)
    r2 = evaluate(week2, as_of=AS_OF, previous=r1['entries'])
    by = _by_cert(r2)
    assert by['W2-C10']['变化来源'].startswith('本批新增')
    assert by['W1-C01']['变化来源'].startswith('本批沿用')
    assert by['W1-C08']['变化来源'].startswith('本批更新')


# ---------- 存储层：版本、幂等、回滚、复核 ----------

def test_publish_is_idempotent_same_batch(week1):
    first = store.publish(week1, as_of=AS_OF)
    again = store.publish(week1, as_of=AS_OF)
    assert first['version'] == again['version'] == 1
    assert first['reused'] is False and again['reused'] is True
    assert len(store.list_versions()) == 1


def test_different_batch_creates_new_version(week1, week2):
    store.publish(week1, as_of=AS_OF)
    second = store.publish(week2, as_of=AS_OF)
    assert second['version'] == 2 and second['reused'] is False
    assert store.current()['version'] == 2


def test_rollback_restores_old_version_and_republish_repoints(week1, week2):
    store.publish(week1, as_of=AS_OF)
    store.publish(week2, as_of=AS_OF)
    assert store.rollback(1) == {'version': 1, 'changed': True}
    assert store.current()['version'] == 1
    # 重跑旧批次内容只移动指针，不新增版本
    republished = store.publish(week2, as_of=AS_OF)
    assert republished['version'] == 2 and republished['reused'] is True
    assert store.current()['version'] == 2 and len(store.list_versions()) == 2


def test_rollback_unknown_version_is_404():
    from app.errors import DomainError
    with pytest.raises(DomainError) as exc:
        store.rollback(99)
    assert exc.value.status == 404


def test_ledger_review_optimistic_lock(week1):
    from app.errors import DomainError
    published = store.publish(week1, as_of=AS_OF)
    assert store.review(published['version'], 1, '通过')['revision'] == 2
    with pytest.raises(DomainError) as exc:
        store.review(published['version'], 1, '退回')
    assert exc.value.status == 409


def test_audit_trail_records_every_change(week1):
    store.publish(week1, as_of=AS_OF)
    store.publish(week1, as_of=AS_OF)
    actions = [a['action'] for a in store.audit_trail()]
    assert 'publish' in actions and 'idempotent' in actions


# ---------- HTTP 层 ----------

def test_http_publish_idempotent_and_pages(client, week1, week2):
    r1 = client.post('/api/ledger/publish', json={'csv': week1, 'as_of': AS_OF})
    assert r1.status_code == 200 and r1.get_json()['version'] == 1
    r2 = client.post('/api/ledger/publish', json={'csv': week1, 'as_of': AS_OF})
    assert r2.get_json()['reused'] is True and len(client.get('/api/ledger/versions').get_json()) == 1
    client.post('/api/ledger/publish', json={'csv': week2, 'as_of': AS_OF})
    assert client.get('/work/8').status_code == 200
    page = client.get('/work/8?version=1').data.decode()
    assert '设备台账发布单' in page and '待核实，未下架' not in page  # v1 无缺失项


def test_http_preview_does_not_persist(client, week2):
    resp = client.post('/api/m8', json={'csv': week2, 'as_of': AS_OF})
    assert resp.status_code == 200 and resp.get_json()['manifest']['kind'] == 'ledger'
    assert client.get('/api/ledger/versions').get_json() == []
    assert client.get('/api/ledger/current').status_code == 404


def test_http_rollback_and_evidence(client, week1, week2):
    client.post('/api/ledger/publish', json={'csv': week1, 'as_of': AS_OF})
    client.post('/api/ledger/publish', json={'csv': week2, 'as_of': AS_OF})
    rb = client.post('/api/ledger/rollback', json={'version': 1})
    assert rb.status_code == 200 and rb.get_json()['version'] == 1
    evidence = client.get('/api/ledger/evidence.csv?version=2').data.decode('utf-8-sig')
    assert 'REVOKED' in evidence and '变化来源' in evidence and 'MISSING' in evidence


def test_http_upload_publish_and_guardrails(client, week1):
    import io
    data = {'file': (io.BytesIO(week1.encode()), 'week1.csv'), 'as_of': AS_OF}
    up = client.post('/api/ledger/publish', data=data, content_type='multipart/form-data')
    assert up.status_code == 200 and up.get_json()['version'] == 1
    assert client.post('/api/ledger/publish', json=[]).status_code == 400
    assert client.post('/api/ledger/publish', json={'csv': ''}).status_code == 422
    assert client.post('/api/ledger/publish', json={'csv': week1, 'as_of': 'bad'}).status_code == 422


def test_m8_preview_accepts_multipart_and_wrong_field_rejected(client, week1):
    import io
    resp = client.post('/api/m8', data={'file': (io.BytesIO(week1.encode()), 'w1.csv')},
                       content_type='multipart/form-data')
    assert resp.status_code == 200 and resp.get_json()['manifest']['kind'] == 'ledger'
    bad = client.post('/api/m8', data={'wrong': (io.BytesIO(b'a,b'), 'x.csv')},
                      content_type='multipart/form-data')
    assert bad.status_code == 400


def test_publish_failure_keeps_old_version(client, week1, monkeypatch):
    import sqlite3
    from app.modules.m8_ledger import store as ledger_store
    client.post('/api/ledger/publish', json={'csv': week1, 'as_of': AS_OF})
    real_connection = ledger_store.connection

    class Broken:
        def __enter__(self):
            raise sqlite3.OperationalError('simulated disk failure')

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ledger_store, 'connection', lambda: Broken())
    resp = client.post('/api/ledger/publish', json={'csv': week1 + '\nX,D9,,ROOT,2026-01-01,2027-01-01,0,10,V,no,2026-11-19,5,V,1\n', 'as_of': AS_OF})
    assert resp.status_code == 500 and resp.get_json()['code'] == 'PUBLISH'
    monkeypatch.setattr(ledger_store, 'connection', real_connection)
    # 旧版仍然可读且仍是当前版本（发布事务整体失败，指针未动）
    assert client.get('/api/ledger/current').get_json()['version'] == 1
    assert len(client.get('/api/ledger/versions').get_json()) == 1
