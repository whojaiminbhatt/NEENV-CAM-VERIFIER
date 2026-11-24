import csv, json
from pathlib import Path
from app.models import CaseData

def load_case(folder: str | Path) -> CaseData:
    folder = Path(folder)
    def read(name): return json.loads((folder / name).read_text(encoding='utf-8'))
    with (folder / 'bank_transactions.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = [dict(r, row=i) for i,r in enumerate(csv.DictReader(f),2)]
    return CaseData.model_validate({**read('case.json'), **read('business_profile.json'), **read('bureau_summary.json'), 'transactions':rows, 'gst':read('gst_summary.json'), 'itr':read('itr_summary.json')})
