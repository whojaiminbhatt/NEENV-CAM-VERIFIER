from decimal import Decimal as D, InvalidOperation
import hashlib
import json
from app.models import CaseData
from app.metrics import build_facts
from app.claims import extract_claims
from app.config_store import get_config


def _sha256_json(model):
    encoded = json.dumps(model.model_dump(mode='json'), sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def verify(req, path=None):
    config,meta=get_config(req.partner_id,req.as_of,req.configuration_id,path,scheme_id=req.scheme_id,cam_type=req.cam_type)
    facts,coverage=build_facts(req.case)
    claims,mode,warnings=extract_claims(req.cam,req.use_llm,config.cam_format.section_order)
    def find(metric,period=None):
        candidates=[f for f in facts if f.metric==metric]
        if period: return next((f for f in candidates if f.period==period),None)
        if len(candidates)==1:return candidates[0]
        return None
    def result(c,status,reason,f=None):
        return {'line':c.line,'claim':c.text,'metric':c.metric,'cam_value':c.value,'claimed_unit':c.unit,'claimed_period':c.period.model_dump(mode='json') if c.period else None,'period_error':c.period_error,'status':status,'reason':reason,'evidence':f.model_dump(mode='json') if f else None}
    results=[]
    for c in claims:
        if c.period_error:
            results.append(result(c,'MISMATCH',c.period_error));continue
        if c.kind=='semantic' and c.metric in ('comparison.consistency','policy.cash_share'):
            metric='comparison.gst_bank_variance_pct' if c.metric=='comparison.consistency' else 'bank.cash_deposit_share'
            f=find(metric,c.period)
            # Negation/qualifiers are not safely interpreted by the simple semantic rule.
            import re
            if re.search(r'\b(?:not|inconsistent|unlikely|outside|may|might|cannot|unclear)\b',c.text,re.I):
                results.append(result(c,'UNSUPPORTED','Qualified/negative wording requires manual review',f));continue
            if not f or not f.complete or f.value is None: results.append(result(c,'INCOMPLETE','Comparable evidence unavailable',f));continue
            threshold=config.verification_rules.allow_consistent_wording_only_if_variance_within_pct if c.metric=='comparison.consistency' else config.credit_policy.max_cash_deposit_share_pct
            results.append(result(c,'SUPPORTED' if D(f.value)<=threshold else ('CONFLICT' if c.metric=='comparison.consistency' else 'MISMATCH'),f'Configured threshold: {threshold}%',f));continue
        if c.kind not in ('numeric','text') or not c.metric:
            results.append(result(c,'UNSUPPORTED','No deterministic evidence mapping; manual review required'));continue
        f=find(c.metric,c.period)
        if not f:
            known=any(x.metric==c.metric for x in facts)
            results.append(result(c,'MISMATCH' if known and c.period else 'UNSUPPORTED','Wrong period or ambiguous/missing source'));continue
        if not f.complete or f.value is None:
            results.append(result(c,'INCOMPLETE','Incomplete source coverage, unresolved data quality or unavailable metric',f));continue
        if c.kind=='text': equal=str(c.value).casefold()==str(f.value).casefold() and f.unit=='TEXT'
        else:
            try:
                # Explicit absolute tolerances, no fuzzy numeric matching.
                tol={'INR':D('1'),'PERCENT':D('0.05'),'RATIO':D('0.0005'),'COUNT':D(0)}.get(f.unit,D(0))
                equal=c.unit==f.unit and abs(D(c.value)-D(f.value))<=tol
            except (InvalidOperation,TypeError,ValueError):equal=False
        results.append(result(c,'SUPPORTED' if equal else 'MISMATCH','Matches source' if equal else 'Value or unit differs from source',f))
    issues=[]
    def issue(code,status,message,evidence=None):issues.append({'code':code,'status':status,'message':message,'evidence':evidence.model_dump(mode='json') if evidence else None})
    if coverage['suspected_duplicate_rows']:
        issue('BANK_SUSPECTED_DUPLICATES','WARN','Potential duplicate events; reconcile source rows '+str(coverage['suspected_duplicate_rows']),find('bank.cheque_ecs_bounces'))
    if len(coverage['covered_months'])<config.credit_policy.required_bank_months:issue('BANK_COVERAGE','BLOCK','Required bank months missing: '+', '.join(coverage['missing_months']))
    elif not coverage['complete']:issue('BANK_COVERAGE','WARN','Partial banking; no annualisation')
    for metric,limit,op in [('bureau.score',config.credit_policy.min_bureau_score,'min'),('bank.cash_deposit_share',config.credit_policy.max_cash_deposit_share_pct,'max')]:
        f=find(metric)
        if not f or f.value is None or not f.complete:issue(metric,'WARN','Policy check unavailable',f)
        elif (op=='min' and D(f.value)<limit) or (op=='max' and D(f.value)>limit):issue(metric,'BLOCK',f'Policy threshold violated ({limit})',f)
    f=find('comparison.gst_bank_variance_pct')
    if f and f.value is not None and D(f.value)>config.verification_rules.gst_bank_variance_warning_pct:issue('GST_BANK_VARIANCE','WARN','Variance requires clarification; reason not established',f)
    gst=find('gst.turnover',req.case.period); itr=find('itr.revenue',req.case.period)
    if gst and itr and gst.value!=itr.value:issues.append({'code':'GST_ITR_VARIANCE','status':'WARN','message':'GST and ITR differ; reason not established','evidence':[gst.model_dump(mode='json'),itr.model_dump(mode='json')]})
    if not gst or not itr:issue('FINANCIAL_PERIOD','WARN','Comparable GST or ITR period missing')
    headings=[line.lstrip('# ').strip().lower().replace(' ','_') for line in req.cam.splitlines() if line.startswith('#')]
    ordered=[h for h in headings if h in config.cam_format.section_order]
    expected_order=[h for h in config.cam_format.section_order if h in headings]
    if ordered!=expected_order:issue('SECTION_ORDER','WARN','CAM section order differs from configured format')
    for section in config.cam_format.required_sections:
        if section not in headings:issue('MISSING_SECTION','WARN',f'Missing section: {section}')
    if not results:issue('NO_CLAIMS','BLOCK','No verifiable claims found')
    queries=[{'case_id':req.case.case_id,**meta,'line':r['line'],'question':f"Please clarify: {r['claim']} ({r['reason']})",'status':'PROPOSED'} for r in results if r['status']!='SUPPORTED']
    queries += [{'case_id':req.case.case_id,**meta,'line':None,'question':i['message'],'status':'PROPOSED'} for i in issues if i['code'] in ('BANK_COVERAGE','BANK_SUSPECTED_DUPLICATES','GST_BANK_VARIANCE','GST_ITR_VARIANCE')]
    audit = {'normalized_case_sha256':_sha256_json(CaseData.model_validate(req.case.model_dump())),
             'cam_text_sha256':hashlib.sha256(req.cam.encode('utf-8')).hexdigest(),
             'assembled_configuration_sha256':_sha256_json(config),
             'as_of':req.as_of.isoformat(), 'verifier_version':'3.0.0'}
    return {'case_id':req.case.case_id,**meta,'audit':audit,'status':'FAIL' if any(i['status']=='BLOCK' for i in issues) or any(r['status']=='MISMATCH' for r in results) else 'REVIEW' if issues or warnings or any(r['status']!='SUPPORTED' for r in results) else 'PASS','extraction_mode':mode,'warnings':warnings,'coverage':coverage,'facts':[f.model_dump(mode='json') for f in facts],'claims':results,'policy_issues':issues,'proposed_queries':queries,'manual_approval_required':True}
