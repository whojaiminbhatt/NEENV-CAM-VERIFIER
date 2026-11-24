from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from app.models import VerifyRequest, CaseData
from app.config_store import init_db
from app.metrics import build_facts
from app.verifier import verify
from app.claims import extract_claims
from pydantic import BaseModel

@asynccontextmanager
async def lifespan(app):
    init_db(); yield

app=FastAPI(title='Neenv CAM Verifier',version='1.0.0',lifespan=lifespan)

@app.get('/api/v1/health')
def health(): return {'status':'ok'}

@app.post('/api/v1/metrics')
def metrics(case:CaseData):
    try:
        facts,coverage=build_facts(case)
        return {'facts':[f.model_dump(mode='json') for f in facts],'coverage':coverage}
    except ValueError as e: raise HTTPException(422,str(e)) from e

@app.post('/api/v1/verify')
def verify_endpoint(req:VerifyRequest):
    try:return verify(req)
    except ValueError as e:raise HTTPException(422,str(e)) from e

class ClaimRequest(BaseModel):
    cam:str
    use_llm:bool=False

@app.post('/api/v1/claims/extract')
def claims(req:ClaimRequest):
    result,mode,warnings=extract_claims(req.cam,req.use_llm)
    return {'claims':result,'mode':mode,'warnings':warnings}
