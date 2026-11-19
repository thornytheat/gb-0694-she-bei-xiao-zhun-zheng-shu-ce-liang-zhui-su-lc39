import csv
import io

from flask import Blueprint, Response, jsonify, request

from app.errors import DomainError
from app.modules.m8_ledger import store
from app.modules.m8_ledger.service import preview
from app.numeric import serial
from app.reader import decode

bp = Blueprint('m8', __name__)


def _as_of(payload):
    value = payload.get('as_of')
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise DomainError('DATE', '判定日必须是 YYYY-MM-DD 文本')
    from app.domain import day
    day(value)
    return value


def _body_text(payload):
    if request.files:
        return decode(request.files['file'].read())
    return payload.get('csv')


@bp.route('/api/m8', methods=['GET', 'POST'])
def module_8():
    """无状态试算，不产生台账版本。"""
    payload = request.get_json(silent=True) or {}
    previous = payload.get('previous')
    if previous is not None and not isinstance(previous, list):
        raise DomainError('REQUEST', 'previous 必须是台账条目数组')
    result = preview(_body_text(payload), as_of=_as_of(payload), previous=previous)
    return jsonify(serial(result))


@bp.route('/api/ledger/publish', methods=['POST'])
def publish():
    payload = request.get_json(silent=True) or {}
    text = _body_text(payload)
    if not text:
        raise DomainError('EMPTY', '发布内容为空')
    return jsonify(serial(store.publish(text, as_of=_as_of(payload))))


@bp.route('/api/ledger/current')
def current():
    result = store.current()
    if result is None:
        raise DomainError('NOT_FOUND', '尚无台账版本', 404)
    return jsonify(serial(result))


@bp.route('/api/ledger/versions')
def versions():
    return jsonify(serial(store.list_versions()))


@bp.route('/api/ledger/versions/<int:version>')
def version_detail(version):
    return jsonify(serial(store.get_version(version)))


@bp.route('/api/ledger/rollback', methods=['POST'])
def rollback():
    payload = request.get_json(silent=True) or {}
    try:
        target = int(payload.get('version'))
    except (ValueError, TypeError):
        raise DomainError('VERSION', '版本号必须为整数')
    return jsonify(serial(store.rollback(target)))


@bp.route('/api/ledger/review', methods=['POST'])
def review():
    payload = request.get_json(silent=True) or {}
    try:
        version = int(payload.get('version'))
        revision = int(payload.get('revision', 0))
    except (ValueError, TypeError):
        raise DomainError('VERSION', '版本与修订号必须为整数')
    return jsonify(serial(store.review(version, revision, payload.get('decision'))))


@bp.route('/api/ledger/audit')
def audit():
    return jsonify(serial(store.audit_trail()))


@bp.route('/api/ledger/evidence.csv')
def evidence():
    if request.args.get('version'):
        try:
            result = store.get_version(int(request.args['version']))
        except ValueError:
            raise DomainError('VERSION', '版本号必须为整数')
    else:
        result = store.current()
        if result is None:
            raise DomainError('NOT_FOUND', '尚无台账版本', 404)
    out = io.StringIO(newline='')
    rows = [{'阶段': '台账条目', **r} for r in result['entries']]
    rows += [{'阶段': '隔离', **r} for r in result['quarantine']]
    fields = list(dict.fromkeys(['阶段', 'ledger_id', '台账版本', '规则版本', '判定日']
                                + [k for r in rows for k in r]))
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    common = {'ledger_id': result['manifest']['run_id'], '台账版本': result['version'],
              '规则版本': result['manifest']['rule_version'], '判定日': result['as_of']}
    for row in rows:
        writer.writerow({**common, **row})
    return Response('﻿' + out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=ledger-evidence.csv'})
