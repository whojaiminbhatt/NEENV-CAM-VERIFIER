from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
from app.models import CaseData, Evidence, SourceRef, Period

CASH = {'CASH_DEPOSIT'}
BOUNCE = {'CHEQUE_RETURN','ECS_RETURN'}

def months(period):
    y,m=period.start.year,period.start.month
    result=[]
    while (y,m) <= (period.end.year,period.end.month):
        result.append(f'{y:04d}-{m:02d}')
        y,m=(y+1,1) if m==12 else (y,m+1)
    return result

def build_facts(case: CaseData):
    # Validate at the service boundary too (model_copy/update and mutable callers
    # can otherwise bypass Pydantic validators).
    case = CaseData.model_validate(case.model_dump())
    expected=months(case.period)
    if len(expected)>12 or case.period.start.day != 1 or case.period.end.day != monthrange(case.period.end.year,case.period.end.month)[1]:
        raise ValueError('Expected a whole-month analysis window of at most 12 months')
    if len(set(case.covered_months)) != len(case.covered_months) or not set(case.covered_months)<=set(expected):
        raise ValueError('Invalid or duplicate covered months')
    covered=sorted(case.covered_months)
    tx=[t for t in case.transactions if case.period.start<=t.date<=case.period.end]
    if any(t.date.strftime('%Y-%m') not in covered for t in tx): raise ValueError('Transaction in month not declared covered')
    complete=covered==expected
    # Same date/amount/category is only a suspicion, not a stable event ID.
    # Keep rows; mark all banking metrics untrusted until reconciled.
    groups = defaultdict(list)
    for t in tx:
        groups[(t.date,t.credit,t.debit,t.category)].append(t)
    suspected = []
    for group in groups.values():
        if len(group) < 2:
            continue
        if any(t.account_id is None for t in group):
            suspected.append(sorted(t.row for t in group))
        else:
            by_account = defaultdict(list)
            for t in group:
                by_account[t.account_id].append(t)
            for same in by_account.values():
                if len(same) > 1 and any(t.transaction_id is None for t in same):
                    suspected.append(sorted(t.row for t in same))
    reliable = complete and not suspected
    coverage={'expected_months':expected,'covered_months':covered,'missing_months':sorted(set(expected)-set(covered)),'complete':complete,
              'bank_data_reliable':not suspected,'suspected_duplicate_rows':suspected}
    facts=[]
    def add(metric,value,unit,sources,formula='Direct extract',period=case.period,ok=True):
        facts.append(Evidence(metric=metric,value=value,unit=unit,period=period,sources=sources,calculation=formula,complete=ok))
    def bank(field,rows): return SourceRef(document='bank_transactions.csv',field=field,rows=[t.row for t in rows])
    coverage_ref=SourceRef(document='case.json',field='covered_months')
    credits=[t for t in tx if t.credit>0]
    cash=[t for t in credits if t.category in CASH]
    bounce=[t for t in tx if t.category in BOUNCE]
    total=sum((t.credit for t in credits),D(0))
    add('bank.total_credits',total if covered else None,'INR',[bank('credit',credits),coverage_ref],'Sum gross incoming credits',ok=reliable)
    add('bank.average_monthly_credit',total/D(len(covered)) if covered else None,'INR',[bank('credit',credits),coverage_ref],'Gross credits / covered months',ok=reliable)
    add('bank.cash_deposit_share',sum((t.credit for t in cash),D(0))/total*100 if total else None,'PERCENT',[bank('credit,category',cash),bank('credit',credits),coverage_ref],'Cash credits / gross credits * 100',ok=reliable)
    add('bank.cheque_ecs_bounces',D(len(bounce)) if covered else None,'COUNT',[bank('category',tx),bank('category',bounce),coverage_ref],'Count return event rows',ok=reliable)
    for name,records in [('gst',case.gst),('itr',case.itr)]:
        seen=set()
        for i,r in enumerate(records):
            key=(r.period.start,r.period.end)
            if key in seen: raise ValueError(f'Duplicate {name} period')
            seen.add(key)
            add('gst.turnover' if name=='gst' else 'itr.revenue',r.amount,'INR',[SourceRef(document=f'{name}_summary.json',field=f'[{i}].amount')],period=r.period)
    gst=next((f for f in facts if f.metric=='gst.turnover' and f.period==case.period),None)
    if gst and reliable and total:
        add('comparison.gst_to_bank_ratio',D(gst.value)/total,'RATIO',gst.sources+[bank('credit',credits),coverage_ref],'GST turnover / gross bank credits')
        add('comparison.gst_bank_variance_pct',abs(total-D(gst.value))/D(gst.value)*100 if gst.value else None,'PERCENT',gst.sources+[bank('credit',credits),coverage_ref],'Absolute difference / GST turnover * 100')
    else:
        add('comparison.gst_to_bank_ratio',None,'RATIO',(gst.sources if gst else [])+[bank('credit',credits),coverage_ref],'Unavailable: missing comparable period, suspected duplicates or zero denominator',ok=False)
    for metric,value,unit,doc,field in [('bureau.score',case.bureau_score,'COUNT','bureau_summary.json','bureau_score'),('business.name',case.business_name,'TEXT','business_profile.json','business_name'),('business.promoter_experience',case.promoter_experience_years,'COUNT','business_profile.json','promoter_experience_years')]:
        if value is not None: add(metric,value if isinstance(value,str) else D(value),unit,[SourceRef(document=doc,field=field)],period=None)
    return facts,coverage
