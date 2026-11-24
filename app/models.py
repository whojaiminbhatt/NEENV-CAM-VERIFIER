import re
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from enum import Enum

class ConfigurationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    EXPIRED = "EXPIRED"

class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Period(BaseModel):
    start: date
    end: date
    @model_validator(mode='after')
    def valid(self):
        if self.start > self.end: raise ValueError('Invalid period')
        return self

class SourceRef(BaseModel):
    document: str
    field: str
    rows: list[int] = Field(default_factory=list)

class Evidence(BaseModel):
    metric: str
    value: Decimal | str | None
    unit: str
    period: Period | None = None
    sources: list[SourceRef]
    calculation: str = 'Direct extract'
    complete: bool = True

# Explicit upstream classification vocabulary; OTHER is a deliberate classification,
# not a fallback for unrecognized data. Add provider mappings in adapters.
TRANSACTION_CATEGORIES = frozenset({
    'CASH_DEPOSIT', 'CASH_WITHDRAWAL', 'CHEQUE_RETURN', 'ECS_RETURN',
    'NEFT', 'RTGS', 'IMPS', 'UPI', 'CHEQUE', 'ECS', 'NACH',
    'TRANSFER', 'INTERNAL_TRANSFER', 'INTEREST', 'FEE', 'REVERSAL',
    'OTHER', 'DEBIT_CARD', 'CREDIT_CARD', 'ATM', 'BANK_CHARGE',
})

class Transaction(BaseModel):
    row: int = Field(ge=2)
    date: date
    credit: Decimal = Field(ge=0)
    debit: Decimal = Field(ge=0)
    category: str
    account_id: str | None = Field(default=None, min_length=1)
    transaction_id: str | None = Field(default=None, min_length=1)

    @field_validator('category', mode='before')
    @classmethod
    def normalize_category(cls, value):
        if not isinstance(value, str):
            raise ValueError('Transaction category must be a string')
        normalized = re.sub(r'[\s-]+', '_', value.strip().upper())
        if normalized not in TRANSACTION_CATEGORIES:
            raise ValueError(f'Unsupported transaction category: {value!r}; map explicitly in the adapter')
        return normalized

    @field_validator('account_id', 'transaction_id', mode='before')
    @classmethod
    def normalize_identity(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode='after')
    def check_identity(self):
        if self.transaction_id and not self.account_id:
            raise ValueError('transaction_id requires account_id')
        if self.category == 'CASH_DEPOSIT' and self.credit <= 0:
            raise ValueError('CASH_DEPOSIT requires a positive credit')
        return self

class FinancialRecord(BaseModel):
    period: Period
    amount: Decimal = Field(ge=0)

class CaseData(BaseModel):
    case_id: str
    period: Period
    covered_months: list[str]
    transactions: list[Transaction]
    gst: list[FinancialRecord]
    itr: list[FinancialRecord]
    bureau_score: int | None = Field(default=None, ge=0, le=900)
    business_name: str | None = None
    promoter_experience_years: int | None = Field(default=None, ge=0)
    @model_validator(mode='after')
    def unique_rows(self):
        if len({t.row for t in self.transactions}) != len(self.transactions): raise ValueError('Duplicate source rows')
        identities = [(t.account_id,t.transaction_id) for t in self.transactions if t.transaction_id]
        if len(set(identities)) != len(identities): raise ValueError('Duplicate transaction identity (account_id, transaction_id)')
        return self

class Policy(ConfigModel):
    required_bank_months: int = Field(ge=1, le=12, strict=True)
    min_bureau_score: int = Field(ge=0, le=900, strict=True)
    max_cash_deposit_share_pct: Decimal = Field(ge=0, le=100)

class Rules(ConfigModel):
    gst_bank_variance_warning_pct: Decimal = Field(ge=0)
    allow_consistent_wording_only_if_variance_within_pct: Decimal = Field(ge=0)

class Format(ConfigModel):
    required_sections: list[str]
    section_order: list[str]
    wording_preferences: dict[str,str] = Field(default_factory=dict)

class Config(ConfigModel):
    cam_format: Format
    credit_policy: Policy
    verification_rules: Rules
    ratios: dict[str,dict[str,bool]]

class Claim(BaseModel):
    line: int = Field(ge=1)
    text: str
    metric: str | None = None
    value: Decimal | str | None = None
    unit: str | None = None
    period: Period | None = None
    kind: Literal['numeric','text','semantic','unknown'] = 'unknown'
    period_error: str | None = None

class VerifyRequest(BaseModel):
    case: CaseData
    partner_id: int
    scheme_id: int
    cam_type: str = Field(default="NEW_APPLICATION", min_length=1)
    configuration_id: int | None = None
    cam: str = Field(min_length=1)
    use_llm: bool = False
    as_of: date = Field(default_factory=date.today)
