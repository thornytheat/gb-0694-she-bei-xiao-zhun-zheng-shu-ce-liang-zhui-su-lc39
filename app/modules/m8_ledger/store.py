"""台账版本存储：幂等发布、回滚、复核乐观锁。

同一份批次内容（SHA256+规则版本+判定日）只能对应一个台账版本，重复提交复用，
不新增版本号。发布在单事务内完成，任何异常回滚，旧版指针保持可用。
"""
import json
import os
import sqlite3
from pathlib import Path

from app.errors import DomainError
from app.modules.m8_ledger.rules import evaluate
from app.manifest import make
from app.numeric import serial


def connection():
    path = Path(os.environ.get('APP_DB', 'data/runs.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS ledger_versions('
               'ledger_id TEXT PRIMARY KEY, version INTEGER NOT NULL UNIQUE, '
               'payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, '
               'decision TEXT NOT NULL DEFAULT "待复核", created_rowid INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS ledger_pointer(current_version INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS ledger_audit('
               'id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, '
               'action TEXT NOT NULL, from_version TEXT, to_version TEXT, note TEXT)')
    return db


def _audit(db, action, from_version, to_version, note=''):
    from time import strftime, gmtime
    db.execute('INSERT INTO ledger_audit(at,action,from_version,to_version,note) VALUES (?,?,?,?,?)',
               (strftime('%Y-%m-%dT%H:%M:%SZ', gmtime()), action,
                None if from_version is None else str(from_version),
                None if to_version is None else str(to_version), note))


def _row_payload(row):
    return {**json.loads(row['payload']), 'version': row['version'],
            'revision': row['revision'], 'decision': row['decision']}


def publish(text, as_of=None):
    try:
        with connection() as db:
            # 先试算以确定判定日（缺省取当天），再以同一判定日生成幂等键。
            pointer = db.execute('SELECT current_version FROM ledger_pointer').fetchone()
            current_version = pointer['current_version'] if pointer else None
            previous = None
            if current_version is not None:
                cur = db.execute('SELECT payload FROM ledger_versions WHERE version=?', (current_version,)).fetchone()
                previous = json.loads(cur['payload'])['entries']
            result = evaluate(text, as_of=as_of, previous=previous)
            result = serial(result)
            manifest = make(text, as_of=result['as_of'], kind='ledger')
            result['manifest'] = manifest
            ledger_id = manifest['run_id']
            existing = db.execute('SELECT * FROM ledger_versions WHERE ledger_id=?', (ledger_id,)).fetchone()
            if existing:
                reused = {**_row_payload(existing), 'reused': True,
                          'already_current': existing['version'] == current_version}
                if existing['version'] != current_version:
                    _audit(db, 'repoint', current_version, existing['version'], '同批次重跑，指针恢复到既有版本')
                    db.execute('UPDATE ledger_pointer SET current_version=?', (existing['version'],))
                else:
                    _audit(db, 'idempotent', current_version, current_version, '同批次重跑，不新增版本')
                return reused
            version = (db.execute('SELECT COALESCE(MAX(version),0)+1 AS v FROM ledger_versions').fetchone())['v']
            result['version'] = version
            payload = json.dumps(result, ensure_ascii=False)
            db.execute('INSERT INTO ledger_versions(ledger_id,version,payload,created_rowid) VALUES (?,?,?,?)',
                       (ledger_id, version, payload, version))
            db.execute('DELETE FROM ledger_pointer')
            db.execute('INSERT INTO ledger_pointer(current_version) VALUES (?)', (version,))
            _audit(db, 'publish', current_version, version, '全量批次发布')
            return {**result, 'revision': 1, 'decision': '待复核', 'reused': False,
                    'already_current': False}
    except sqlite3.Error as exc:
        raise DomainError('PUBLISH', '台账发布失败，已保留旧版：' + str(exc), 500)


def current():
    with connection() as db:
        pointer = db.execute('SELECT current_version FROM ledger_pointer').fetchone()
        if pointer is None:
            return None
        row = db.execute('SELECT * FROM ledger_versions WHERE version=?', (pointer['current_version'],)).fetchone()
        return _row_payload(row)


def get_version(version):
    with connection() as db:
        row = db.execute('SELECT * FROM ledger_versions WHERE version=?', (version,)).fetchone()
        if row is None:
            raise DomainError('NOT_FOUND', '台账版本不存在', 404)
        return _row_payload(row)


def list_versions():
    with connection() as db:
        pointer = db.execute('SELECT current_version FROM ledger_pointer').fetchone()
        current_version = pointer['current_version'] if pointer else None
        rows = db.execute('SELECT ledger_id,version,revision,decision,created_rowid FROM ledger_versions ORDER BY version').fetchall()
        return [{**dict(r), 'current': r['version'] == current_version} for r in rows]


def rollback(target_version):
    try:
        with connection() as db:
            target = db.execute('SELECT version FROM ledger_versions WHERE version=?', (target_version,)).fetchone()
            if target is None:
                raise DomainError('NOT_FOUND', '目标版本不存在，无法回滚', 404)
            pointer = db.execute('SELECT current_version FROM ledger_pointer').fetchone()
            current_version = pointer['current_version'] if pointer else None
            if current_version == target_version:
                _audit(db, 'rollback-noop', current_version, target_version, '已是当前版本')
                return {'version': target_version, 'changed': False}
            db.execute('DELETE FROM ledger_pointer')
            db.execute('INSERT INTO ledger_pointer(current_version) VALUES (?)', (target_version,))
            _audit(db, 'rollback', current_version, target_version, '恢复到旧版台账')
            return {'version': target_version, 'changed': True}
    except sqlite3.Error as exc:
        raise DomainError('PUBLISH', '回滚失败，当前版本未变：' + str(exc), 500)


def review(version, revision, decision):
    if decision not in ('通过', '退回'):
        raise DomainError('DECISION', '复核动作无效')
    try:
        with connection() as db:
            changed = db.execute('UPDATE ledger_versions SET revision=revision+1,decision=? '
                                 'WHERE version=? AND revision=?', (decision, version, revision)).rowcount
            if not changed:
                raise DomainError('VERSION', '版本已变化，请重新载入', 409)
            _audit(db, 'review', version, version, decision + ' r' + str(revision + 1))
        return {'version': version, 'revision': revision + 1, 'decision': decision}
    except sqlite3.Error as exc:
        raise DomainError('PUBLISH', '复核写入失败：' + str(exc), 500)


def audit_trail():
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT id,at,action,from_version,to_version,note FROM ledger_audit ORDER BY id')]
