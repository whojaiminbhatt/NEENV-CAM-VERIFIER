from datetime import date
from decimal import Decimal as D
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.adapters import load_case
from app.models import VerifyRequest
from app.metrics import build_facts
from app.config_store import init_db,get_config,connect
from app.verifier import verify
from app.main import app
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def case():return load_case(ROOT/'sample_data')
@pytest.fixture
def db(tmp_path,monkeypatch):
    path=tmp_path/'config.db';monkeypatch.setenv('CAM_CONFIG_DB',str(path));init_db();return path

def req(case,cam=None,partner=1):return VerifyRequest(case=case,partner_id=partner,scheme_id=partner*100+1,cam=cam or (ROOT/'sample_data/sample_cam.txt').read_text(),as_of=date(2026,4,1))
def values(case):return {f.metric:f for f in build_facts(case)[0] if f.period==case.period or f.period is None}

def test_metrics(case):
    f=values(case)
    for k,v in {'bank.average_monthly_credit':'2500000','bank.cash_deposit_share':'10','bank.cheque_ecs_bounces':'3','comparison.gst_to_bank_ratio':'0.8'}.items():
        assert f[k].value==D(v);assert f[k].sources

def test_partial(case,db):
    case.covered_months=case.covered_months[:7];case.transactions=[t for t in case.transactions if t.date.strftime('%Y-%m') in case.covered_months]
    f=values(case);assert f['bank.average_monthly_credit'].value==D('2500000');assert not f['bank.average_monthly_credit'].complete;assert f['comparison.gst_to_bank_ratio'].value is None
    assert any(i['code']=='BANK_COVERAGE' and i['status']=='BLOCK' for i in verify(req(case),db)['policy_issues'])
    assert any(i['code']=='BANK_COVERAGE' and i['status']=='WARN' for i in verify(req(case,partner=2),db)['policy_issues'])

def test_zero_credits(case):
    case.transactions=[];f=values(case);assert f['bank.cash_deposit_share'].value is None;assert f['bank.average_monthly_credit'].value==0

def test_zero_month_coverage(case):
    case.transactions=[];case.covered_months=[];assert values(case)['bank.average_monthly_credit'].value is None

def test_period_mismatch(case):
    case.gst=case.gst[1:];assert values(case)['comparison.gst_to_bank_ratio'].value is None

def test_missing_middle_month(case):
    missing=case.covered_months.pop(4);case.transactions=[t for t in case.transactions if t.date.strftime('%Y-%m')!=missing];assert build_facts(case)[1]['missing_months']==[missing]

def test_wrong_declared_coverage(case):
    case.covered_months.pop();
    with pytest.raises(ValueError):build_facts(case)

def test_sample_errors(case,db):
    r=verify(req(case),db);assert r['status']=='FAIL';assert len(r['claims'])==11
    assert sum(c['status']=='MISMATCH' for c in r['claims'])==6
    assert sum(c['status']=='UNSUPPORTED' for c in r['claims'])==1
    assert sum(c['status']=='CONFLICT' for c in r['claims'])==1
    assert sum(c['status']=='SUPPORTED' for c in r['claims'])==3

def test_wrong_fy(case,db):
    r=verify(req(case,'GST turnover: ₹2.1 crore (FY2025-26).'),db);assert r['claims'][0]['status']=='MISMATCH'

def test_ambiguous_fy(case,db):assert verify(req(case,'GST turnover: ₹2.1 crore.'),db)['claims'][0]['status']=='UNSUPPORTED'

def test_unknown(case,db):assert verify(req(case,'Healthy banking behaviour.'),db)['claims'][0]['status']=='UNSUPPORTED'

def test_versioning(db):
    with pytest.raises(ValueError):get_config(1,date(2024,1,1),path=db,scheme_id=101)
    assert get_config(1,date(2026,1,1),path=db,scheme_id=101)[1]['version']==2
    assert get_config(1,date(2026,1,1),101,path=db,scheme_id=101)[1]['version']==1
    with pytest.raises(ValueError):get_config(2,date(2026,1,1),101,path=db,scheme_id=201)

def test_partner_specific_policy(case,db):
    assert not any(i['code']=='bank.cash_deposit_share' for i in verify(req(case),db)['policy_issues'])
    assert any(i['code']=='bank.cash_deposit_share' and i['status']=='BLOCK' for i in verify(req(case,partner=2),db)['policy_issues'])

def test_api(case,db):
    with TestClient(app) as c:
        assert c.get('/api/v1/health').status_code==200
        assert c.post('/api/v1/metrics',json=case.model_dump(mode='json')).status_code==200
        r=c.post('/api/v1/verify',json=req(case).model_dump(mode='json'));assert r.status_code==200;assert r.json()['status']=='FAIL'
        assert c.post('/api/v1/verify',json={}).status_code==422

def test_llm_fallback(case,db,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False);r=req(case);r.use_llm=True
    report=verify(r,db);assert report['extraction_mode']=='offline_fallback';assert report['warnings']


def test_scheme_and_cam_type(case,db):
    r=req(case);r.scheme_id=102
    assert verify(r,db)['partner_cam_configuration_id']==105
    r.scheme_id=101;r.cam_type='RENEWAL'
    assert verify(r,db)['partner_cam_configuration_id']==103
    r.scheme_id=201
    with pytest.raises(ValueError):verify(r,db)


def test_inactive_not_eligible(db):
    with pytest.raises(ValueError):get_config(1,date(2026,1,1),104,path=db,scheme_id=101)
    with connect(db) as c:c.execute("UPDATE partner_schemes SET status='INACTIVE' WHERE id=101")
    with pytest.raises(ValueError):get_config(1,date(2026,1,1),path=db,scheme_id=101)
    assert get_config(1,date(2026,1,1),101,path=db,scheme_id=101)[1]['configuration_status']=='EXPIRED'


def test_metadata_constraints(db):
    import sqlite3
    with connect(db) as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE cams_meta SET meta_value='8' WHERE configuration_id=102 AND meta_key='required_bank_months'")
        with pytest.raises(sqlite3.IntegrityError):c.execute("DELETE FROM cams_meta WHERE configuration_id=101")
        with pytest.raises(sqlite3.IntegrityError):c.execute("INSERT INTO cams_meta(configuration_id,meta_group,meta_key,meta_value,value_type) VALUES(104,'credit_policy','required_bank_months','8','integer')")
        with pytest.raises(sqlite3.IntegrityError):c.execute("INSERT INTO cams_meta(configuration_id,meta_group,meta_key,meta_value,value_type) VALUES(104,'credit_policy','new_key','true','integer')")
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE partner_cam_configuration SET scheme_id=201 WHERE id=104")
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE partner_cam_configuration SET status='ACTIVE',approved_by='test',approved_at='2026-01-01' WHERE id=104")


def test_missing_metadata_validation(db):
    from app.config_store import assemble
    with connect(db) as c:
        rows=c.execute("SELECT * FROM cams_meta WHERE configuration_id=104 AND meta_key!='required_bank_months'").fetchall()
    with pytest.raises(ValueError):assemble(rows)


def test_idempotent_seed(db):
    with connect(db) as c:n=c.execute('SELECT count(*) FROM cams_meta').fetchone()[0]
    init_db(db)
    with connect(db) as c:assert c.execute('SELECT count(*) FROM cams_meta').fetchone()[0]==n


def test_new_version_activation(db):
    from app.config_store import assemble
    with connect(db) as c:
        c.execute("UPDATE cams_meta SET meta_value='9' WHERE configuration_id=104 AND meta_key='required_bank_months'")
        assemble(c.execute('SELECT * FROM cams_meta WHERE configuration_id=104').fetchall())
        c.execute("UPDATE partner_cam_configuration SET status='EXPIRED',effective_to='2026-05-01' WHERE id=102")
        c.execute("UPDATE partner_cam_configuration SET status='ACTIVE',effective_from='2026-05-01',approved_by='test',approved_at='2026-05-01' WHERE id=104")
    config,meta=get_config(1,date(2026,6,1),path=db,scheme_id=101)
    assert config.credit_policy.required_bank_months==9 and meta['version']==3
    assert get_config(1,date(2026,6,1),102,path=db,scheme_id=101)[0].credit_policy.required_bank_months==12
