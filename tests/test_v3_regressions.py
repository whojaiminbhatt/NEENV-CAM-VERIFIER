"""Regression tests for V2 review findings; no external LLM credentials needed."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import claims
from app.adapters import load_case
from app.config_store import init_db
from app.main import app
from app.metrics import build_facts
from app.models import CaseData, VerifyRequest
from app.verifier import verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def case():
    return load_case(ROOT / 'sample_data')


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / 'config.db'
    monkeypatch.setenv('CAM_CONFIG_DB', str(path))
    init_db(path)
    return path


def request(case, text, llm=False):
    return VerifyRequest(case=case, partner_id=1, scheme_id=101,
                         as_of=date(2026, 4, 1), cam=text, use_llm=llm)


def fact_map(case):
    facts, coverage = build_facts(case)
    return {f.metric: f for f in facts if f.period == case.period or f.period is None}, coverage


@pytest.mark.parametrize('period', ['FY2025-27', 'FY2025', 'FY25-26', 'FY2025-XX', 'FY2025-26 FY2024-25', 'financial year 2025-27'])
def test_invalid_explicit_period(case, db, period):
    report = verify(request(case, f'Average monthly bank credits: ₹25 lakh ({period}).'), db)
    assert report['status'] == 'FAIL'
    assert report['claims'][0]['status'] == 'MISMATCH'
    assert 'year' in report['claims'][0]['reason']


@pytest.mark.parametrize('period', ['FY2025-26', 'FY 2025-2026', 'financial year 2025/26'])
def test_valid_period(case, db, period):
    assert verify(request(case, f'Average monthly bank credits: ₹25 lakh ({period}).'), db)['claims'][0]['status'] == 'SUPPORTED'


def test_heading_assertion(case, db):
    result = verify(request(case, '# Banking Analysis\n## Average monthly bank credits: ₹31 lakh'), db)
    assert len(result['claims']) == 1
    assert result['claims'][0]['line'] == 2
    assert result['claims'][0]['status'] == 'MISMATCH'


def test_unknown_heading_retained(case, db):
    result = verify(request(case, '# Promoter has a nationwide network'), db)
    assert result['claims'][0]['status'] == 'UNSUPPORTED'


def test_multiple_claims(case, db):
    report = verify(request(case, 'Average monthly bank credits were ₹25 lakh and there were zero bounces.'), db)
    assert [(c['metric'], c['status']) for c in report['claims']] == [
        ('bank.average_monthly_credit', 'SUPPORTED'), ('bank.cheque_ecs_bounces', 'MISMATCH')]
    assert all(c['line'] == 1 for c in report['claims'])


def test_compound_with_ambiguous_period_not_approved(case, db):
    text = 'Average monthly bank credits: ₹25 lakh and cash deposit share: 10% (FY2024-25).'
    assert verify(request(case, text), db)['claims'][0]['status'] != 'SUPPORTED'


def test_consistency_and_combined_bounce_not_split(case, db):
    result = verify(request(case, 'GST and banking are consistent.\nCheque and ECS bounces: 3.'), db)
    assert [c['status'] for c in result['claims']] == ['CONFLICT', 'SUPPORTED']


def test_llm_full_partition_and_independent_values(case, db, monkeypatch):
    # Offline grammar deliberately does not split comma-separated assertions.
    text = 'Average monthly bank credits: ₹25 lakh, cash deposit share: 15%.'
    assert claims.offline_claims(text)[0].kind == 'unknown'
    # Deliberately false model-normalized fields must have no influence.
    monkeypatch.setattr(claims, '_llm_quotes', lambda _: [
        {'line': 1, 'text': 'Average monthly bank credits: ₹25 lakh', 'value': '3100000', 'metric': 'gst.turnover'},
        {'line': 1, 'text': 'cash deposit share: 15%.', 'value': '10', 'kind': 'numeric'},
    ])
    report = verify(request(case, text, True), db)
    assert report['extraction_mode'] == 'hybrid'
    assert [(c['status'], c['cam_value']) for c in report['claims']] == [('SUPPORTED', D('2500000')), ('MISMATCH', D('15'))]


@pytest.mark.parametrize('text,quotes', [
    ('It is not true that average monthly bank credits: ₹25 lakh.', ['average monthly bank credits: ₹25 lakh.']),
    ('Average monthly bank credits: ₹25 lakh, nationwide distribution network.', ['Average monthly bank credits: ₹25 lakh']),
    ('Average monthly bank credits: ₹31 lakh, cash deposit share: 10%.', ['Average monthly bank credits: ₹25 lakh', 'cash deposit share: 10%.']),
    ('Average monthly bank credits: ₹25 lakh (FY2025-27).', ['Average monthly bank credits: ₹25 lakh']),
    ('Average monthly bank credits: ₹25 lakh only if approved.', ['Average monthly bank credits: ₹25 lakh']),
    ('Average monthly bank credits: ₹25 lakh, cash deposit share: 10%.', ['Average monthly bank credits: ₹25 lakh, cash deposit share: 10%.', 'cash deposit share: 10%.']),
])
def test_llm_cannot_erase_context_or_invent_quotes(case, db, monkeypatch, text, quotes):
    monkeypatch.setattr(claims, '_llm_quotes', lambda _: [{'line': 1, 'text': q} for q in quotes])
    result = verify(request(case, text, True), db)
    assert result['claims']
    assert all(c['status'] != 'SUPPORTED' for c in result['claims'])


def test_llm_cannot_override_offline_mismatch(case, db, monkeypatch):
    monkeypatch.setattr(claims, '_llm_quotes', lambda _: [{'line': 1, 'text': 'Average monthly bank credits: ₹25 lakh'}])
    r = verify(request(case, 'Average monthly bank credits: ₹31 lakh', True), db)
    assert r['claims'][0]['status'] == 'MISMATCH'


def test_llm_malformed_response_fallback(case, db, monkeypatch):
    monkeypatch.setattr(claims, '_llm_quotes', lambda _: {'invalid': True})
    r = verify(request(case, 'Average monthly bank credits: ₹31 lakh', True), db)
    assert r['extraction_mode'] == 'offline_fallback' and r['warnings']
    assert r['claims'][0]['status'] == 'MISMATCH'


@pytest.mark.parametrize('category', ['cash_deposit', ' cash deposit ', 'Cash-Deposit'])
def test_category_normalization(case, category):
    for t in case.transactions:
        if t.category == 'CASH_DEPOSIT':
            t.category = category  # service boundary validation must cover mutated objects
    assert fact_map(case)[0]['bank.cash_deposit_share'].value == D('10')


def test_unknown_category_rejected_api_and_service(case, db):
    case.transactions[0].category = 'UNKNOWN_FORMAT'
    with pytest.raises(ValidationError, match='Unsupported transaction category'):
        build_facts(case)
    with TestClient(app) as client:
        assert client.post('/api/v1/metrics', json=case.model_dump(mode='json')).status_code == 422
        assert client.post('/api/v1/verify', json=request(case, 'Cash deposit share: 10%').model_dump(mode='json')).status_code == 422


def test_cash_deposit_requires_credit(case):
    case.transactions[0].category = 'CASH_DEPOSIT'
    case.transactions[0].credit = D(0)
    with pytest.raises(ValidationError, match='positive credit'):
        build_facts(case)


def test_suspected_duplicate_not_certified(case, db):
    original = next(t for t in case.transactions if t.category == 'CHEQUE_RETURN')
    duplicate = original.model_copy(update={'row': 999})
    case.transactions.append(duplicate)
    facts, coverage = fact_map(case)
    assert [original.row, 999] in coverage['suspected_duplicate_rows']
    assert not coverage['bank_data_reliable']
    assert facts['bank.cheque_ecs_bounces'].value == D(4)  # provisional raw count, not certified
    assert not facts['bank.cheque_ecs_bounces'].complete
    assert not facts['bank.average_monthly_credit'].complete
    assert facts['comparison.gst_to_bank_ratio'].value is None
    report = verify(request(case, 'Cheque/ECS bounces: 4'), db)
    assert report['claims'][0]['status'] == 'INCOMPLETE'
    assert any(i['code'] == 'BANK_SUSPECTED_DUPLICATES' for i in report['policy_issues'])
    assert report['status'] != 'PASS'


def test_confirmed_duplicate_rejected(case):
    t = case.transactions[0]
    t.account_id, t.transaction_id = 'ACC1', 'TX1'
    case.transactions.append(t.model_copy(update={'row': 999}))
    with pytest.raises(ValidationError, match='Duplicate transaction identity'):
        build_facts(case)


def test_distinct_ids_same_signature_not_duplicate(case):
    t = case.transactions[0]
    t.account_id, t.transaction_id = 'ACC1', 'TX1'
    case.transactions.append(t.model_copy(update={'row': 999, 'transaction_id': 'TX2'}))
    assert fact_map(case)[1]['bank_data_reliable']


def test_transaction_id_requires_account(case):
    case.transactions[0].transaction_id = 'TX1'
    with pytest.raises(ValidationError, match='requires account_id'):
        build_facts(case)


def test_distinct_accounts_can_share_transaction_id(case):
    t = case.transactions[0]
    t.account_id, t.transaction_id = 'ACC1', 'TX1'
    case.transactions.append(t.model_copy(update={'row': 999, 'account_id': 'ACC2'}))
    assert fact_map(case)[1]['bank_data_reliable']


def test_units_not_interchangeable(case, db):
    result = verify(request(case, 'Bureau score: ₹735\nCash deposit share: 10 lakh\nAverage monthly bank credits: ₹2,5,000'), db)
    assert all(c['status'] != 'SUPPORTED' for c in result['claims'])


def test_mock_sdk_response(case, db, monkeypatch):
    import sys
    import json
    from types import SimpleNamespace
    text = 'Average monthly bank credits: ₹25 lakh, cash deposit share: 15%.'
    def create(**kwargs):
        assert kwargs['response_format'] == {'type': 'json_object'}
        assert 'untrusted CAM' in kwargs['messages'][0]['content']
        assert text in kwargs['messages'][1]['content']
        output = {'claims': [{'line': 1, 'text': 'Average monthly bank credits: ₹25 lakh'},
                             {'line': 1, 'text': 'cash deposit share: 15%.'}]}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    report = verify(request(case, text, True), db)
    assert report['extraction_mode'] == 'hybrid'
    assert [c['status'] for c in report['claims']] == ['SUPPORTED', 'MISMATCH']


def test_audit_fingerprints(case, db):
    r = request(case, 'Cash deposit share: 10%')
    a = verify(r, db)['audit']
    assert a == verify(r, db)['audit']
    assert all(len(a[k]) == 64 for k in ('normalized_case_sha256','cam_text_sha256','assembled_configuration_sha256'))
    r.cam = 'Cash deposit share: 15%'
    assert verify(r, db)['audit']['cam_text_sha256'] != a['cam_text_sha256']
    r.case.transactions[0].credit += D(1)
    assert verify(r, db)['audit']['normalized_case_sha256'] != a['normalized_case_sha256']
    r.configuration_id = 101
    assert verify(r, db)['audit']['assembled_configuration_sha256'] != a['assembled_configuration_sha256']


def test_replace_cannot_overwrite_approved_config(db):
    import sqlite3
    from app.config_store import connect
    with connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('''INSERT OR REPLACE INTO partner_cam_configuration
                (id, partner_id, scheme_id, name, cam_type, version, status, effective_from, created_by)
                VALUES(102,1,101,'replaced','NEW_APPLICATION',2,'INACTIVE','2026-01-01','test')''')
