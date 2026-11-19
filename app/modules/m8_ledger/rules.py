"""M8 台账发布：全量证书批次 -> 版本化设备台账。

状态分桶（互不混用）：
  ACTIVE  在册：本批出现、未撤销且在 as_of 当日有效
  REVOKED 撤销：本批撤销标识为 yes，或上版已撤销（撤销具有粘性）
  EXPIRED 过期：valid_to <= as_of（半开区间 [valid_from, valid_to)）
  MISSING 缺失：本批未出现且未撤销/未过期，仅待核实，绝不当作注销直接下架

同机别名通过 app.domain.equivalence 归并到一台设备；同机重叠证书与撤销记录
一并参与判断，重叠只作标记不改变分桶。每条台账记录保留设备归属与变化来源。
"""
from datetime import date

from app.config import HEADERS
from app.domain import day, equivalence
from app.errors import DomainError
from app.numeric import SENTINELS
from app.reader import read_rows
from app.reasons import isolate

IDENTITY_FIELDS = ('device', 'alias', 'parent', 'valid_from', 'valid_to',
                   'minimum', 'maximum', 'unit', 'revoked', 'version')
STATUS_NAME = {'ACTIVE': '在册', 'MISSING': '缺失', 'REVOKED': '撤销', 'EXPIRED': '过期'}


def _is_blank(value):
    return str(value).strip() in SENTINELS


def _range_pair(row):
    """量程三元组可整体缺省；给一部分则必须全部可解析且下限<=上限。"""
    from app.domain import volts
    if all(_is_blank(row[k]) for k in ('minimum', 'maximum', 'unit')):
        return None
    if any(_is_blank(row[k]) for k in ('minimum', 'maximum', 'unit')):
        raise DomainError('MISSING', '量程下限/上限/单位必须同时提供')
    low = volts(row['minimum'], row['unit'])
    high = volts(row['maximum'], row['unit'])
    if low > high:
        raise DomainError('RANGE', '证书量程下限大于上限')
    return str(low), str(high)


def _validate(row):
    if not row['cert'] or not row['device']:
        raise DomainError('MISSING', '证书或设备为空')
    start, end = day(row['valid_from']), day(row['valid_to'])
    if start >= end:
        raise DomainError('RANGE', '证书区间无效')
    if row['revoked'] not in ('yes', 'no'):
        raise DomainError('REVOKED_FLAG', '撤销标识无效')
    if row['version'] != '1':
        raise DomainError('VERSION', '不支持的证书版本')
    _range_pair(row)
    return start, end


def _previous_index(previous):
    out = {}
    for entry in previous or []:
        out[entry['证书']] = entry
    return out


def evaluate(text, as_of=None, previous=None):
    """纯函数：返回可序列化的台账发布结果，不写库。"""
    as_of = day(as_of) if isinstance(as_of, str) else (as_of or date.today())
    ctx = {'text': text, 'rows': [], 'quarantine': [], 'stages': {}}
    rows = read_rows(ctx)
    input_count = ctx['input_count']

    adopted, conflicted, seen_signature = {}, set(), set()
    validated_certs = set()
    duplicate_rows = 0
    for row in rows:
        try:
            start, end = _validate(row)
        except DomainError as exc:
            isolate(ctx, row, exc.code, exc.message)
            continue
        signature = tuple(row[h] for h in HEADERS)
        if signature in seen_signature:
            duplicate_rows += 1
            continue
        seen_signature.add(signature)
        cert = row['cert']
        validated_certs.add(cert)
        if cert in adopted and any(adopted[cert][k] != row[k] for k in IDENTITY_FIELDS):
            conflicted.add(cert)
            isolate(ctx, row, 'CONFLICT', '同证书定义冲突，本批不采信')
            continue
        if cert in conflicted:
            isolate(ctx, row, 'CONFLICT', '同证书定义冲突，本批不采信')
            continue
        adopted.setdefault(cert, {**row, '_from': start, '_to': end})

    # 冲突证书的首次出现同样不采信，进隔离（剥离内部日期键，保证证据可JSON序列化）。
    for cert in conflicted:
        first = adopted.pop(cert, None)
        if first is not None:
            isolate(ctx, {k: v for k, v in first.items() if not k.startswith('_')},
                    'CONFLICT', '同证书定义冲突，本批不采信')

    names = equivalence([{'device': r['device'], 'alias': r['alias']} for r in adopted.values()])
    for item in adopted.values():
        item['canonical'] = names.get(item['device'], item['device'])

    accepted_certs = set()
    # 同机证书区间重叠（半开区间，m3 同口径），仅标记不改变分桶。
    overlap_map = {cert: [] for cert in adopted}
    ordered = sorted(adopted)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1:]:
            a, b = adopted[left], adopted[right]
            if a['canonical'] != b['canonical']:
                continue
            if max(a['_from'], b['_from']) < min(a['_to'], b['_to']):
                overlap_map[left].append(right)
                overlap_map[right].append(left)

    prev_index = _previous_index(previous)
    entries, new_certs, changed, carried = [], 0, 0, 0
    for cert in sorted(adopted):
        item = adopted[cert]
        prev = prev_index.pop(cert, None)
        if item['revoked'] == 'yes':
            code = 'REVOKED'
        elif item['_to'] <= as_of:
            code = 'EXPIRED'
        elif item['_from'] > as_of:
            isolate(ctx, {k: v for k, v in item.items() if not k.startswith('_')},
                    'NOT_YET_VALID', '证书生效日晚于判定日，本批不采信')
            continue
        else:
            code = 'ACTIVE'
        accepted_certs.add(cert)
        overlaps = ','.join(sorted(overlap_map[cert]))
        marker = '同机重叠:' + overlaps if overlaps else ''
        if prev is None:
            source = '本批新增（批次证书）'
            new_certs += 1
        elif prev['状态码'] != code or prev.get('设备') != item['canonical'] or marker:
            source = '本批更新（批次证书）'
            changed += 1
        else:
            source = '本批沿用（内容一致）'
            carried += 1
        entries.append({
            '证书': cert, '设备': item['canonical'], '设备别名': item['alias'] or '',
            '状态': STATUS_NAME[code], '状态码': code,
            '生效日': item['valid_from'], '失效日': item['valid_to'],
            '本批撤销': item['revoked'], '重叠证书': overlaps, '标记': marker,
            '变化来源': source, '来源批次': '本批', '行号': item['行号'],
        })

    # 本批未出现/未采信：沿用上版，按撤销粘性、过期、缺失三分，绝不静默下架。
    for cert in sorted(prev_index):
        prev = prev_index[cert]
        if prev['状态码'] == 'REVOKED':
            code, source = 'REVOKED', '沿用上版（撤销保留，本批未出现）'
        elif prev['失效日'] and day(prev['失效日']) <= as_of:
            code, source = 'EXPIRED', '沿用上版（已过期，本批未出现）'
        else:
            code, source = 'MISSING', '本批未出现（待核实，未下架）'
        marker = prev.get('标记', '')
        if cert in conflicted:
            marker = ('定义冲突本批不采信;' + marker).strip(';')
        changed += 1
        entries.append({
            '证书': cert, '设备': prev['设备'], '设备别名': prev.get('设备别名', ''),
            '状态': STATUS_NAME[code], '状态码': code,
            '生效日': prev.get('生效日', ''), '失效日': prev.get('失效日', ''),
            '本批撤销': 'yes' if code == 'REVOKED' else 'no',
            '重叠证书': '', '标记': marker,
            '变化来源': source, '来源批次': prev.get('来源批次', '上版'), '行号': '上版',
        })

    def count(code):
        return sum(1 for e in entries if e['状态码'] == code)

    conflict_rows = sum(1 for q in ctx['quarantine'] if q['原因码'] == 'CONFLICT')
    not_yet_rows = sum(1 for q in ctx['quarantine'] if q['原因码'] == 'NOT_YET_VALID')
    structural_rows = len(ctx['quarantine']) - conflict_rows - not_yet_rows
    # 通过校验的唯一证书数：每个冲突证书的首条定义仍占一个证书，其余冲突行才是多余行。
    extra_conflict_rows = conflict_rows - len(conflicted)
    validated_rows = input_count - structural_rows - duplicate_rows - extra_conflict_rows
    # 守恒：批次侧 通过校验证书=输入-结构隔离-完全重复-多余冲突行；台账侧 条目集合=
    # 上版集合 与 本批实际采信证书集合 的并集——任何证书都不会被静默删除（冲突证书
    # 虽本批不采信，仍沿用上版并显式标注；未生效证书在隔离区可复核）。
    prev_certs = {e['证书'] for e in (previous or [])}
    conserved = validated_rows == len(validated_certs) and \
        {e['证书'] for e in entries} == prev_certs | accepted_certs
    metrics = [
        ['在册 ACTIVE', count('ACTIVE'), '份'],
        ['缺失 MISSING', count('MISSING'), '份'],
        ['撤销 REVOKED', count('REVOKED'), '份'],
        ['过期 EXPIRED', count('EXPIRED'), '份'],
        ['本批新增', new_certs, '份'], ['状态/归属变化', changed, '份'],
        ['内容一致沿用', carried, '份'], ['重复行', duplicate_rows, '行'],
        ['隔离行', len(ctx['quarantine']), '行'],
        ['行数守恒', '相符' if conserved else '差异', ''],
    ]
    return {
        'as_of': str(as_of), 'entries': entries, 'quarantine': ctx['quarantine'],
        'metrics': metrics, 'input_count': input_count,
        'duplicate_rows': duplicate_rows, 'conserved': conserved,
    }
