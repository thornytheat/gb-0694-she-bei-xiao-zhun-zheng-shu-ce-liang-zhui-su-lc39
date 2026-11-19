import hashlib, time
from app.config import RULE_VERSION
def make(text, as_of=None, kind='run'):
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    basis=RULE_VERSION+':'+kind+':'+digest+((':'+as_of) if as_of else '')
    return {'run_id':hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16], 'sha256':digest, 'rule_version':RULE_VERSION, 'parameters':{'as_of':as_of} if as_of else {}, 'kind':kind, 'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
