from flask import Blueprint, render_template, request
from app.config import TITLE, MODULES, SHORT, SAMPLE, LEDGER_SAMPLE_W1, LEDGER_SAMPLE_W2, LEDGER_SAMPLE_W3
from app.pipeline import calculate
from app.modules.m8_ledger import store
from app.store import latest, save, get_run
bp=Blueprint('web',__name__)
@bp.route('/')
@bp.route('/work/<int:stage>')
def work(stage=1):
    if stage<1 or stage>8:
        from flask import abort
        abort(404)
    if stage==8:
        version=request.args.get('version')
        ledger=store.get_version(int(version)) if version else store.current()
        samples={
            'week1':LEDGER_SAMPLE_W1.read_text(encoding='utf-8-sig'),
            'week2':LEDGER_SAMPLE_W2.read_text(encoding='utf-8-sig'),
            'week3':LEDGER_SAMPLE_W3.read_text(encoding='utf-8-sig'),
        }
        return render_template('m8.html', title=TITLE, modules=MODULES, short=SHORT,
                               stage=8, ledger=ledger, versions=store.list_versions(),
                               audit=store.audit_trail(), samples=samples)
    run=get_run(request.args['run_id']) if request.args.get('run_id') else latest()
    if run is None: run=save(calculate())
    current=run['stages'][str(stage)]
    return render_template('m'+str(stage)+'.html', title=TITLE, modules=MODULES, short=SHORT, run=run, current=current, stage=stage, sample=SAMPLE.read_text(encoding='utf-8-sig'))
