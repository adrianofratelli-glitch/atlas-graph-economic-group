"""Atlas real, banco efêmero exclusivo. Nunca reinicia nem limpa o banco da demo.

Uso: backend/venv/bin/python tests/live_hardening.py
Cria fixtures mínimas, exercita a API com transações/Change Streams e remove
somente o banco com UUID criado por esta execução, inclusive quando falha.
"""
import os
import sys
import time
import uuid
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

name = 'graph_resilience_test_' + uuid.uuid4().hex
os.environ['MONGODB_DB'] = name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi.testclient import TestClient
from app.db.client import get_db, get_client
from app.db import credit_decision
from app.services.alerts import hub
from app.services import investigation
from main import app

checks = []
def check(label, condition):
    checks.append({'test': label, 'passed': bool(condition)})
    print(('PASS ' if condition else 'FAIL ') + label, flush=True)
    if not condition: raise AssertionError(label)

def run():
    db = get_db()
    assert db.name == name and name.startswith('graph_resilience_test_')
    db.companies.insert_many([{'_id': 'company_' + x, 'cnpj': x, 'razao_social': x,
        'advisor_id': 'advisor_a', 'is_holding': x == 'root', 'credit_status': 'active'}
        for x in ['root', 'a', 'b', 'c']])
    db.people.insert_one({'_id': 'person_p', 'name': 'P'})
    db.advisors.insert_one({'_id': 'advisor_a', 'nome': 'Assessor A', 'reports_to': None, 'papel': 'assessor'})
    db.ownership.insert_many([{'_id': str(i), 'owner_id': owner, 'owned_id': child,
        'owner_type': kind, 'percentage': 100} for i, (owner, child, kind) in enumerate([
            ('person_p', 'company_root', 'individual'), ('company_root', 'company_a', 'corporate'),
            ('company_root', 'company_b', 'corporate'), ('company_b', 'company_c', 'corporate')])])
    db.credit_exposure.insert_many([{'_id': x, 'company_id': 'company_' + x, 'advisor_id': 'advisor_a',
        'limite': 100, 'utilizado': 50, 'vencido': 20 if x == 'c' else 0, 'rating': 'A'}
        for x in ['root', 'a', 'b', 'c']])
    db.companies.create_index('cnpj', unique=True)
    db.ownership.create_index('owned_id'); db.ownership.create_index('owner_id')
    db.credit_exposure.create_index('company_id', unique=True)
    with TestClient(app, raise_server_exceptions=False) as api:
        for _ in range(50):
            if hub.state['running']: break
            time.sleep(.1)
        check('change stream conectado', hub.state['running'])
        subscription = hub.subscribe()
        try:
            shallow = api.get('/api/group/c?depth=1').json()
            check('profundidade parcial declarada', not shallow['coverage']['complete'])
            r = api.post('/api/credit/review', json={'investigation_token': shallow['investigation']['token']})
            check('consulta parcial não grava', r.status_code == 409 and db.credit_decisions.count_documents({}) == 0)
            g = api.get('/api/group/c?depth=6').json()
            check('grupo completo e soma', g['coverage']['complete'] and g['stats']['companies'] == 4 and g['group_exposure']['limite'] == 400)
            check('sem arestas órfãs', all(e['from'] in {n['id'] for n in g['nodes']} and e['to'] in {n['id'] for n in g['nodes']} for e in g['edges']))
            old = g['investigation']['token']
            db.credit_exposure.update_one({'_id': 'c'}, {'$set': {'limite': 150}})
            check('exposição alterada invalida comprovante', api.post('/api/credit/review', json={'investigation_token': old}).status_code == 409)
            check('recusa mantém estado limpo', db.companies.count_documents({'credit_status': 'under_review'}) == 0)
            g = api.get('/api/group/c?depth=6').json()
            body = {'investigation_token': g['investigation']['token'], 'reason': 'teste isolado'}
            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(lambda _: api.post('/api/credit/review', json=body), range(6)))
            check('seis aberturas concorrentes idempotentes', all(r.status_code == 200 for r in responses))
            ids = {r.json()['case_id'] for r in responses}; cid = next(iter(ids))
            check('um único caso e valores do snapshot', len(ids) == 1 and db.credit_decisions.count_documents({}) == 1 and db.credit_decisions.find_one({'_id': cid})['group_exposure']['limite'] == 450)
            check('todas as marcas atômicas', db.companies.count_documents({'case_id': cid}) == 4 and db.credit_exposure.count_documents({'case_id': cid}) == 4)
            other = api.get('/api/group/a?depth=6').json()
            r = api.post('/api/credit/review', json={'investigation_token': other['investigation']['token']})
            check('outro comprovante sobreposto recebe 409', r.status_code == 409 and r.json()['detail']['case_id'] == cid)
            event = subscription.get(timeout=15)
            check('evento real de abertura agrega quatro empresas', event['type'] == 'review_opened' and event['companies'] == 4)
            check('encerramento confirmado', api.post('/api/credit/close/' + cid).status_code == 200)
            event = subscription.get(timeout=15)
            check('evento real de encerramento', event['type'] == 'review_closed' and event['companies'] == 4)
            check('nenhuma marca remanescente', db.companies.count_documents({'case_id': cid}) == 0 and db.credit_exposure.count_documents({'case_id': cid}) == 0)
            check('encerramento idempotente', api.post('/api/credit/close/' + cid).json()['replayed'])
            check('comprovante encerrado não reabre', api.post('/api/credit/review', json=body).status_code == 409)
            check('caso ausente retorna 404', api.post('/api/credit/close/missing').status_code == 404)
            # Truncamento é aplicado no payload, preserva sujeito e impede revisão.
            from app.config import get_settings
            settings = get_settings(); before = settings.max_nodes; settings.max_nodes = 2
            try:
                small = api.get('/api/group/c?depth=6').json()
                check('teto de nós respeitado', len(small['nodes']) <= 2 and small['stats']['truncated'])
                check('truncamento preserva sujeito', any(n.get('is_subject') for n in small['nodes']))
                check('truncamento bloqueia revisão', not small['investigation']['review_allowed'])
            finally: settings.max_nodes = before
            # Ciclo societário: termina e declara ausência de raiz resolvida.
            db.ownership.insert_one({'_id': 'cycle', 'owner_id': 'company_c', 'owned_id': 'company_root', 'owner_type': 'corporate', 'percentage': 1})
            cyc = api.get('/api/group/c?depth=6')
            check('ciclo termina com cobertura parcial', cyc.status_code == 200 and not cyc.json()['coverage']['complete'])
        finally:
            hub.unsubscribe(subscription)

if __name__ == '__main__':
    try: run()
    finally:
        hub.stop()
        if hub._thread: hub._thread.join(timeout=5)
        client = get_client()
        assert get_db().name == name and name.startswith('graph_resilience_test_')
        client.drop_database(name)
        Path('tests/live-hardening-results.json').write_text(json.dumps({'checks': checks, 'isolated_database_removed': True}, indent=2))
        client.close()
