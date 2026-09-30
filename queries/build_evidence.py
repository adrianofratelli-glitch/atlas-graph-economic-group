#!/usr/bin/env python3
"""Auditoria da vitrine e curva controlada no Atlas. Nunca altera a base da demo.

A curva usa uma base com UUID, removida no finally. A página publica uma fotografia
medida; não executa benchmarks pelo navegador. Valores monetários são conferidos
em centavos a partir de leituras diretas, independentes do pipeline de traversal.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.db.client import get_client, get_db
from app.db import ownership
from app.config import get_settings


def cents(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal('1')))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def path_between(edges, source, target):
    neighbors = {}
    for e in edges:
        a, b = e['owner_id'], e['owned_id']
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
    previous = {source: None}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        if node == target:
            result = []
            while node is not None:
                result.append(node); node = previous[node]
            return result[::-1]
        for nxt in sorted(neighbors.get(node, [])):
            if nxt not in previous:
                previous[nxt] = node; queue.append(nxt)
    return []


def stats(samples):
    values = sorted(samples)
    return {'samples_ms': values, 'p50_ms': round(statistics.median(values), 2),
            'p95_ms': round(values[max(0, math.ceil(len(values) * .95) - 1)], 2)}


def explain_summary(document):
    """Preserva contadores por estágio; não soma pais e filhos nem inventa totais."""
    rows = []
    metrics = {'nReturned', 'executionTimeMillis', 'executionTimeMillisEstimate',
               'totalDocsExamined', 'totalKeysExamined', 'collectionScans',
               'indexesUsed', 'usedDisk', 'spills', 'indexName'}
    def walk(value, path='$'):
        if isinstance(value, dict):
            data = {k: v for k, v in value.items() if k in metrics}
            if data:
                rows.append({'path': path, 'stage': value.get('stage') or next((k for k in value if k.startswith('$')), None), **data})
            for key, child in value.items():
                if key not in {'command', 'serverInfo', 'serverParameters', '$clusterTime', 'operationTime'}:
                    walk(child, f'{path}.{key}')
        elif isinstance(value, list):
            for i, child in enumerate(value): walk(child, f'{path}[{i}]')
    walk(document)
    return rows


def explain(db, cnpj, depth):
    pipeline = ownership._pipeline_grupo(cnpj, depth, get_settings().max_nodes * 3, ownership.MAX_RAIZES)
    document = db.command('explain', {'aggregate': 'companies', 'pipeline': pipeline,
                          'cursor': {}, 'maxTimeMS': get_settings().graph_max_time_ms}, verbosity='executionStats')
    return explain_summary(document)


def traversal_matches(db, cnpj, depth):
    # Sonda fora da cronometragem: conta os arrays antes do limite de nós da resposta.
    pipeline = ownership._pipeline_grupo(cnpj, depth, get_settings().max_nodes * 3, ownership.MAX_RAIZES)
    for stage in pipeline:
        fields = stage.get('$set', {})
        if 'edges_limited_up' in fields:
            fields['_matched_up'] = {'$size': '$cadeia'}
        if 'abaixo' in fields:
            fields['_matched_down'] = {'$sum': {'$map': {'input': '$descidas', 'as': 'd', 'in': {'$size': '$$d.cadeia'}}}}
    pipeline[-1]['$project'].update({'_matched_up': 1, '_matched_down': 1})
    row = next(db.companies.aggregate(pipeline, maxTimeMS=get_settings().graph_max_time_ms))
    return {'up': row['_matched_up'], 'down': row['_matched_down'],
            'note': 'Correspondências de arestas por direção; uma aresta pode aparecer nos dois sentidos. Descida contada após o cap de arestas por raiz.'}


def audit(db):
    groups = sorted(db.economic_groups.find({'showcase': True}), key=lambda g: (g['levels'], g.get('sector') != 'construcao', g['_id']))
    selected = {}
    for g in groups: selected.setdefault(g['levels'], g)
    out = []
    for level, manifest in selected.items():
        ids = sorted(manifest['member_ids'])
        companies = list(db.companies.find({'_id': {'$in': ids}}, {'cnpj': 1, 'razao_social': 1}))
        labels = {c['_id']: c for c in companies}
        credits = list(db.credit_exposure.find({'company_id': {'$in': ids}},
                       {'company_id': 1, 'limite': 1, 'utilizado': 1, 'vencido': 1, '_id': 0}))
        credit_map = {c['company_id']: c for c in credits}
        # Todas as arestas corporativas internas, inclusive participações cruzadas.
        edges = list(db.ownership.find({'owner_id': {'$in': ids}, 'owned_id': {'$in': ids}},
                     {'owner_id': 1, 'owned_id': 1, 'percentage': 1, '_id': 0}))
        expected = {key: sum(cents(c[key]) for c in credits) for key in ('limite', 'utilizado', 'vencido')}
        cnpj = labels[manifest['applicant_id']]['cnpj']
        actual = ownership.economic_group(cnpj, 6)
        actual_ids = sorted(n['id'] for n in actual['nodes'] if n['kind'] == 'company')
        actual_edges = {(e['from'], e['to'], e['percentage']) for e in actual['edges'] if e['type'] == 'corporate'}
        expected_edges = {(e['owner_id'], e['owned_id'], e['percentage']) for e in edges}
        observed = {k: cents(actual['group_exposure'][k]) for k in expected}
        checks = {'membership': actual_ids == ids, 'corporate_edges': actual_edges == expected_edges,
                  'exposure': observed == expected, 'complete': actual['coverage']['complete'],
                  'manifest_entities_exist': len(labels) == len(ids), 'one_exposure_per_company': len(credits) == len(credit_map)}
        path = path_between(edges, manifest['applicant_id'], manifest['distressed_id'])
        checks['path_to_distressed'] = bool(path)
        item = {'group_id': manifest['_id'], 'levels': level, 'cnpj': cnpj,
                'applicant': labels[manifest['applicant_id']]['razao_social'],
                'expected_company_ids': ids, 'observed_company_ids': actual_ids,
                'expected_cents': expected, 'observed_cents': observed, 'checks': checks,
                'source_sha256': digest({'ids': ids, 'credits': sorted(credits, key=lambda c:c['company_id']), 'edges': sorted(edges, key=lambda e:(e['owner_id'],e['owned_id']))}),
                'members': [{'id': cid, 'name': labels.get(cid, {}).get('razao_social', cid),
                            **{k: cents(credit_map.get(cid, {}).get(k, 0)) for k in expected}} for cid in ids],
                'corporate_edges': edges,
                'path': [{'id': cid, 'name': labels[cid]['razao_social']} for cid in path],
                'traversal_matches': traversal_matches(db, cnpj, 6), 'explain': explain(db, cnpj, 6), 'elapsed_ms': actual['stats']['elapsed_ms']}
        out.append(item)
        print('audit', level, checks, flush=True)
    if not out: raise RuntimeError('Nenhum manifesto de vitrine disponível')
    return out


def tree(depth, branching, prefix):
    """Árvore com valores inteiros, um crédito por empresa e sem sócios PF."""
    companies = [{'_id': prefix + '_0', 'cnpj': prefix + '_0', 'razao_social': 'Raiz de teste', 'is_holding': True}]
    edges, frontier = [], [companies[0]['_id']]
    for _ in range(depth):
        next_level = []
        for parent in frontier:
            for _ in range(branching):
                cid = prefix + '_' + str(len(companies))
                companies.append({'_id': cid, 'cnpj': cid, 'razao_social': 'Empresa de teste', 'is_holding': False})
                edges.append({'_id': cid, 'owner_id': parent, 'owned_id': cid, 'owner_type': 'corporate', 'percentage': 100, 'qualificacao': 'controlador'})
                next_level.append(cid)
        frontier = next_level
    credits = [{'_id': c['_id'], 'company_id': c['_id'], 'limite': 100, 'utilizado': 50,
                'vencido': 10 if i == len(companies)-1 else 0, 'rating': 'A'} for i,c in enumerate(companies)]
    return companies, edges, credits


def curve(db, runs):
    db.companies.create_index('cnpj', unique=True)
    db.ownership.create_index('owner_id'); db.ownership.create_index('owned_id')
    db.credit_exposure.create_index('company_id', unique=True)
    shapes = [(d,b) for b in (1,2,3) for d in (1,2,4,6)] + [(1,1200)]
    rows = []
    with patch.object(ownership, 'get_db', return_value=db):
        for d,b in shapes:
            companies, edges, credits = tree(d,b,f'company_d{d}b{b}')
            db.companies.insert_many(companies); db.ownership.insert_many(edges); db.credit_exposure.insert_many(credits)
            subject = companies[-1]['cnpj']
            ownership.economic_group(subject, d)  # aquecimento, fora da distribuição
            samples, valid = [], True
            expected_ids = {c['_id'] for c in companies}
            for _ in range(runs):
                start = time.perf_counter(); result = ownership.economic_group(subject, d)
                samples.append(round((time.perf_counter()-start)*1000, 3))
                if result.get('found'):
                    shown = {n['id'] for n in result['nodes']}
                    valid &= shown <= expected_ids and result['group_exposure']['limite'] == len(shown)*100
                    if len(companies) <= get_settings().max_nodes:
                        valid &= shown == expected_ids and result['coverage']['complete']
                    else: valid &= not result['coverage']['complete'] and len(shown) <= get_settings().max_nodes
                else: valid = False
            rows.append({'depth': d, 'branching': b, 'fixture_companies': len(companies),
                         'fixture_edges': len(edges), 'returned_companies': len(result.get('nodes', [])),
                         'returned_edges': len(result.get('edges', [])),
                         'complete': result.get('coverage', {}).get('complete', False),
                         'reasons': result.get('coverage', {}).get('reasons', []),
                         'correct': bool(valid), 'traversal_matches': traversal_matches(db, subject, d), **stats(samples), 'explain': explain(db, subject, d)})
            print('curve', d, b, rows[-1]['p95_ms'], 'correct', valid, flush=True)
    return rows


def main():
    p = argparse.ArgumentParser(); p.add_argument('--runs', type=int, default=10)
    args = p.parse_args()
    if args.runs < 5: p.error('Use ao menos 5 repetições')
    client, source = get_client(), get_db()
    name = 'graph_evidence_test_' + uuid.uuid4().hex
    result = {'schema_version': 1, 'measured_at': datetime.now(timezone.utc).isoformat(),
              'runs': args.runs, 'environment': 'Atlas M20 compartilhado; cliente remoto; uma consulta por vez; cache aquecido',
              'mongodb_version': source.command('buildInfo')['version'],
              'production_counts': {c: source[c].estimated_document_count() for c in ('companies','ownership','credit_exposure')},
              'comparison': {'status': 'deferred', 'alternative': 'PostgreSQL WITH RECURSIVE', 'reason': 'Ambiente comparativo adiado pelo responsável pela POV.'}}
    ping = []
    for _ in range(args.runs):
        t = time.perf_counter(); source.command('ping'); ping.append(round((time.perf_counter()-t)*1000,3))
    result['ping'] = stats(ping)
    result['audit_measured_at'] = datetime.now(timezone.utc).isoformat()
    result['audit'] = audit(source)
    try:
        result['curve'] = curve(client[name], args.runs)
    finally:
        client.drop_database(name)
        result['isolated_database_removed'] = True
    result['passed'] = all(all(a['checks'].values()) for a in result['audit']) and all(r['correct'] for r in result['curve'])
    target = ROOT / 'frontend/public/evidence/results.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print('saved', target, 'passed', result['passed'], flush=True)
    if not result['passed']: raise SystemExit(1)

if __name__ == '__main__': main()
