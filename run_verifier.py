import argparse,json,sys
from pathlib import Path
from datetime import date
from fastapi.encoders import jsonable_encoder
from app.adapters import load_case
from app.models import VerifyRequest
from app.config_store import init_db
from app.verifier import verify

def main():
    p=argparse.ArgumentParser(description='Evidence-backed CAM verification')
    p.add_argument('--data',default='sample_data',help='Folder containing normalized extracts')
    p.add_argument('--cam',help='Override sample_cam.txt')
    p.add_argument('--partner-id',type=int,default=1)
    p.add_argument('--scheme-id',type=int,default=101)
    p.add_argument('--cam-type',default='NEW_APPLICATION')
    p.add_argument('--configuration-id',type=int)
    p.add_argument('--as-of',type=date.fromisoformat,default=date.today())
    p.add_argument('--llm',action='store_true')
    p.add_argument('--output',help='Write JSON report to this path')
    p.add_argument('--request-only',action='store_true',help='Emit API request JSON instead of executing')
    a=p.parse_args()
    try:
        req=VerifyRequest(case=load_case(a.data),partner_id=a.partner_id,scheme_id=a.scheme_id,cam_type=a.cam_type,configuration_id=a.configuration_id,as_of=a.as_of,cam=Path(a.cam or Path(a.data)/'sample_cam.txt').read_text(encoding='utf-8'),use_llm=a.llm)
        if a.request_only: result=req.model_dump(mode='json')
        else: init_db();result=verify(req)
        text=json.dumps(jsonable_encoder(result),indent=2,ensure_ascii=False)
        if a.output:Path(a.output).write_text(text+'\n',encoding='utf-8')
        else:print(text)
    except (ValueError,OSError) as exc:
        print(f'Input/configuration error: {exc}',file=sys.stderr);return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
