# Neenv CAM Verifier

A small, evidence-first Python microservice and CLI for the **Self-Improving CAM Agent** take-home assignment. It verifies an existing CAM against structured borrower extracts. It does not generate CAMs or make lending decisions.

**Important:** Neenv's actual sample case, planted-error CAM, lender comparison and historical queries were not available when this implementation was prepared. Everything under `sample_data/` is explicitly synthetic. The included report demonstrates the code on those fixtures only; it is not a result on Neenv's sample.

## reliability update

 preserves the four-table lender/scheme/CAM configuration schema. No raw PDF/OCR, bank-specific or tax-return parser enhancements are included. Changes are limited to verifier reliability and auditability:

- Explicit malformed/ambiguous financial years produce `MISMATCH`, not an unqualified match.
- Only recognized section headings are skipped; headings containing assertions are verified.
- Common compound statements split into atomic claims. Ambiguous shared periods or unsupported context stay unresolved.
- Optional LLM mode returns exact quotations only; code independently parses metric, unit, value and period. Incomplete/overlapping quotes cannot erase original text.
- Recognized category case/spacing is normalized; unknown classifications return an input error.
- Confirmed duplicate account/transaction IDs are rejected; suspected duplicate rows produce a warning and incomplete banking evidence, not automatic deletion.
- Reports include input and configuration fingerprints. SQLite runtime connections enable recursive triggers to protect approved configurations from `INSERT OR REPLACE` bypasses.

## 1. Quick start

Requires Python 3.11+ (tested with Python 3.13). Run the commands from the repository root after extracting the ZIP.

```bash
cd neenv-cam-verifier
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q
python run_verifier.py --output report.json
```

On Windows PowerShell use `py -3 -m venv .venv` and `.venv\Scripts\Activate.ps1` instead of the first two Python/activation commands. If necessary, run `python -m pip` using the virtual environment's executable directly.

Open `report.json` in your editor. The sample intentionally returns `FAIL`; that means the verifier identified incorrect claims, not that the program crashed. The CLI exits 0 after a successful verification run, even if the CAM fails; malformed input/configuration returns exit code 2.

### CLI options

```bash
python run_verifier.py --help
python run_verifier.py --partner-id 2 --scheme-id 201 --output report_b.json
python run_verifier.py --partner-id 1 --scheme-id 102 --output term_loan.json
python run_verifier.py --partner-id 1 --scheme-id 101 --cam-type RENEWAL --output renewal.json
python run_verifier.py --configuration-id 101 --output historical.json
python run_verifier.py --data /path/to/normalized/extracts --cam /path/to/cam.txt --output report.json
```

The default configuration DB is created automatically on the first verification run. Set `CAM_CONFIG_DB` to a writable alternate SQLite file if desired. The `--as-of` date filters ACTIVE configurations by effective dates, not the financial analysis period. An expired configuration is never selected for new processing, even with an earlier `--as-of`. `--configuration-id` explicitly replays an approved historical version for the specified partner, scheme and CAM type. The CLI defaults to partner 1, scheme 101, NEW_APPLICATION. For an existing v1 database, retain a backup and set `CAM_CONFIG_DB=config_v2.db` before running; this release intentionally does not silently migrate or overwrite old configuration data. can reuse a valid V2 database; back it up first.

## 2. Run as a microservice

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open Swagger UI at <http://127.0.0.1:8000/docs>.

From a second terminal (with the virtual environment activated):

```bash
curl http://127.0.0.1:8000/api/v1/health
python run_verifier.py --request-only --output request.json
curl -X POST http://127.0.0.1:8000/api/v1/verify \
  -H 'Content-Type: application/json' --data-binary @request.json
```

For Windows, use `curl.exe` and a single-line command. `request.json` is also suitable for Postman or Swagger's request body. The request includes normalized case data, `partner_id`, required `scheme_id`, `cam_type` (defaults to NEW_APPLICATION), optional pinned configuration ID, CAM text, `as_of` and `use_llm`. The API and CLI invoke the same service functions; no duplicated financial logic.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Process health |
| POST | `/api/v1/metrics` | Accepts the `case` object from request.json; returns evidence and coverage |
| POST | `/api/v1/verify` | Accepts full request.json; returns findings, configuration version and proposed queries |
| POST | `/api/v1/claims/extract` | Accepts `{"cam":"...","use_llm":false}`; extracts claims for inspection |

The API is deliberately local-only for this assessment. It has no production authentication, authorization, tenant isolation, rate limits or data retention controls. Do not expose it on a public interface or send real borrower information without those controls.

## 3. Optional LLM assistance

The offline verifier is the default and does not need any API key. To enable optional claim extraction using OpenAI:

```bash
export OPENAI_API_KEY='your-key'
export OPENAI_MODEL='gpt-4o-mini'
python run_verifier.py --llm --output llm_report.json
```

PowerShell: `$env:OPENAI_API_KEY = 'your-key'` and `$env:OPENAI_MODEL = 'gpt-4o-mini'`. For API use, set `use_llm` to true in the request and set the environment variables before starting the server. Never commit credentials. This mode sends CAM text (not the raw transaction extracts) to the configured OpenAI API; use synthetic/redacted data only unless your organization has approved that processing.

The LLM returns a JSON claims array of `{line, text}` quotations. Code checks that each quotation is an exact, unique contiguous span of an unresolved clause. A proposed split is accepted only if the complete clause is covered by nonoverlapping spans separated by safe connectors/punctuation. Model-provided metrics, amounts, periods and verdicts are ignored. Python independently parses each quoted assertion and compares it against deterministic evidence. Already parsed offline claims are never removed or overridden. Unrecognized wording, omitted context, negation, ambiguous period scope and invalid model output cannot be silently certified. Missing keys/API errors fall back to offline mode with a warning. Tests mock both the quote extractor and the SDK response; no live external API call was made.

## 4. Structure

```text
neenv-cam-verifier/
├── app/
│   ├── main.py           # FastAPI endpoints
│   ├── models.py         # Pydantic domain and API contracts
│   ├── adapters.py       # CSV/JSON to canonical case representation
│   ├── metrics.py        # Decimal arithmetic, evidence and period coverage
│   ├── config_store.py   # SQLite scheme/CAM lookup and metadata assembly
│   ├── claims.py         # Conservative offline parser + optional LLM extraction
│   └── verifier.py       # Claim matching, policy checks and proposed queries
├── sql/schema.sql        # Four-table schema and integrity triggers
├── sample_data/          # Synthetic extracts, lender configs, CAM, example output
├── tests/
│   ├── test_verifier.py  # Original calculation, API and configuration tests
│   └── test_v3_regressions.py # Adversarial parser, LLM and transaction tests
├── run_verifier.py       # CLI entry point
├── requirements.txt
├── VERIFIER_REPORT.md
├── AI_USAGE.md
└── README.md
```

## 5. Input contracts and adapting Neenv's data

All JSON files are UTF-8. The CLI expects a folder with these filenames:

| File | Required content |
|---|---|
| `case.json` | case_id, period `{start,end}` (ISO dates), covered_months (YYYY-MM strings) |
| `bank_transactions.csv` | date (ISO), credit, debit (nonnegative INR amounts), category; optional account_id and transaction_id |
| `gst_summary.json` | Array of `{period:{start,end},amount}` records |
| `itr_summary.json` | Array of `{period:{start,end},amount}` records |
| `bureau_summary.json` | bureau_score (integer or null) |
| `business_profile.json` | business_name and promoter_experience_years (optional values) |
| `sample_cam.txt` | Existing CAM text, Markdown headings for section checks |

`covered_months` is statement coverage metadata, not a list of months with transactions. A complete month may have zero transactions. In a production ingestion layer derive and validate coverage from statement ranges, independently of transaction activity. Do not assert coverage for a month merely because it has one transaction. Analysis windows must span whole calendar months, at most 12 months. All transactions within the requested window must belong to a declared covered month.

The normalized transaction category is supplied by the extract. The accepted categories are defined by `TRANSACTION_CATEGORIES` in `app/models.py`: `CASH_DEPOSIT`, `CASH_WITHDRAWAL`, `CHEQUE_RETURN`, `ECS_RETURN`, `NEFT`, `RTGS`, `IMPS`, `UPI`, `CHEQUE`, `ECS`, `NACH`, `TRANSFER`, `INTERNAL_TRANSFER`, `INTEREST`, `FEE`, `REVERSAL`, `OTHER`, `DEBIT_CARD`, `CREDIT_CARD`, `ATM`, `BANK_CHARGE`. Case, whitespace and hyphen normalization handles variants such as `cash deposit`. Unknown categories fail validation (API HTTP 422; CLI exit 2); never silently map unknown categories to `OTHER`. `OTHER` is allowed only as a deliberate upstream classification. A cash deposit must have a positive credit. Return event rows must represent distinct events; fee/reversal rows must not be classified as additional events.

Optional `account_id` and `transaction_id` identify a bank event. When supplied, transaction_id requires account_id, and repeated `(account_id, transaction_id)` is rejected. Without reliable identities, identical date/credit/debit/category signatures within an account (or with unknown account attribution) are flagged as potential duplicates. Distinct stable IDs within the same account are not treated as duplicates solely because amounts and dates match. The service retains all rows, marks banking metrics incomplete, withholds the GST/bank ratio and returns `BANK_SUSPECTED_DUPLICATES` with source rows. A raw count may therefore be provisional; reconcile the events upstream and rerun. This heuristic can flag legitimate repeated transactions and cannot detect every duplicate or multi-row representation of one bounce. No automatic deletion or multi-account reconciliation is performed.

 Actual lender definitions may exclude internal transfers, reversals or non-business credits; the current definition is explicitly gross credits, not sales. Review the actual data and policy, change `app/adapters.py` for column/field mappings and classification, then update calculations/tests only if the definitions differ. Additional source facts (e.g. legal constitution, outstanding balances) require explicit mappings in the fact engine. No OCR is implemented.

Provenance uses logical document filenames, CSV line numbers (header = line 1), JSON field paths, periods and formula descriptions. This demo assumes normalized extracts preserve original row order. For production, adapters should retain original document IDs, hashes, exact row/page locations and transformation lineage even when records are reordered. The microservice API expects trusted normalized source identifiers/row numbers from its caller; it does not authenticate original documents.
## 6. Financial definitions

| Metric | Deterministic formula | Synthetic expected value |
|---|---|---:|
| Average monthly bank credits | Sum gross credits / covered months | ₹25 lakh |
| Cash deposit share | Cash deposit credits / gross credits × 100 | 10% |
| Cheque/ECS bounces | Count distinct rows tagged as return events | 3 |
| GST turnover vs bank credits ratio | Comparable GST turnover / gross bank credits | 0.8 (80%) |

All monetary calculations use Decimal. Percent comparisons allow 0.05 percentage points, ratios 0.0005, money ₹1, count exact. These are illustrative tolerances, not lender policy. The verifier recognizes simple normalized CAM labels with INR/lakh/crore, percentages, ratios and explicit Indian financial years (e.g. FY2025-26). When multiple periods exist, a missing period is ambiguous and requires review. Explicit malformed years (e.g. FY2025-27) are rejected as claim mismatches. Multi-claim wording with unclear period scope remains unsupported. Complex prose and unsupported units are intentionally not automatically trusted.

If banking is incomplete or has unresolved duplicate-event warnings, affected metrics are labeled incomplete and the denominator is the covered months only. Full-period GST/bank comparison is withheld unless coverage is complete, no suspected duplicate groups remain and GST is for the same period. `coverage.complete` describes statement-month coverage only; `bank_data_reliable` describes the duplicate check, not comprehensive source quality. Zero denominators return null, not a fabricated zero ratio. GST/ITR differences and the absolute GST-bank variance are surfaced with evidence; the verifier never invents the reason.

## 7. Lender and scheme configuration (unchanged from V2)

```text
partners (1)
  └── (N) partner_schemes
        └── (N) partner_cam_configuration
              └── (N) cams_meta
```

| Table | Responsibility |
|---|---|
| partners | Lender identity and status |
| partner_schemes | Partner-specific loan products, scheme code, loan type and status |
| partner_cam_configuration | Partner, scheme, CAM type, version, effective dates, approval and status |
| cams_meta | Individual typed JSON metadata values, groups, keys and section order |

The composite foreign key `(partner_id, scheme_id)` prevents cross-partner scheme references. Versions are unique within `(partner_id, scheme_id, cam_type, version)`. Metadata keys are unique within `(configuration_id, meta_group, meta_key)`. SQLite checks JSON validity and logical type. Approved metadata cannot be inserted, updated or deleted; approved configuration identity and approval fields cannot be overwritten. Lifecycle status and end date may change. Overlapping ACTIVE effective intervals are rejected by database triggers. Dates are ISO calendar dates with half-open intervals `[effective_from, effective_to)`.

Statuses are exactly `ACTIVE`, `INACTIVE`, `EXPIRED`. New drafts remain INACTIVE until approved. Only active partners, active schemes and active approved configurations within their validity interval are eligible for new verification. Explicit historical replay accepts approved configurations even when no longer active; it does not accept an unapproved draft. Expiry is enforced by the date filter even if an administrator has not updated the status label; automatic expiry jobs are not implemented.

### Seed configurations (fictional lenders)

| Partner | Scheme ID | CAM type | Configuration ID | Version | Status | Required bank months |
|---|---:|---|---:|---:|---|---:|
| 1 | 101 | NEW_APPLICATION | 101 | 1 | EXPIRED | 12 |
| 1 | 101 | NEW_APPLICATION | 102 | 2 | ACTIVE | 12 |
| 1 | 101 | RENEWAL | 103 | 1 | ACTIVE | 12 |
| 1 | 101 | NEW_APPLICATION | 104 | 3 | INACTIVE | 12 |
| 1 | 102 | NEW_APPLICATION | 105 | 1 | ACTIVE | 6 |
| 2 | 201 | NEW_APPLICATION | 201 | 1 | ACTIVE | 7 |

Lender A working capital permits cash share up to 20%; lender B permits up to 8%. These are synthetic examples only. All seed values live in `sample_data/partners.json`, with one metadata entry per key. On first run the seed inserts an INACTIVE header, inserts metadata, validates the assembled configuration, then records demo approval/status. Repeated starts do not overwrite existing records. Set `CAM_SEED_DEMO=0` to disable demo provisioning when using a separately managed database.

### Metadata contract

| Group | Example key | JSON value | Runtime usage |
|---|---|---|---|
| credit_policy | required_bank_months | 12 | Completeness check |
| credit_policy | min_bureau_score | 700 | Bureau threshold |
| credit_policy | max_cash_deposit_share_pct | 20 | Cash share threshold |
| verification_rules | gst_bank_variance_warning_pct | 10 | Conflict warning |
| cam_sections | banking_analysis | {"required": true} | Section presence and display_order |
| ratios | gst_to_bank_ratio | {"enabled": true} | Presentation extension point |
| wording_preferences | summary_style | "concise" | Presentation extension point |

`config_store.assemble()` reconstructs the typed `Config` consumed by the existing verifier. Unsupported groups and unknown policy/rule keys are rejected instead of silently ignored. The database permits granular complex JSON values, but new rule types, formulas and metadata groups need explicit engine support and tests. No arbitrary database expression is executed. Ratio selection and general wording preferences are future generation extension points; all four assignment metrics are computed regardless of presentation settings.

### Inspect the configuration

```sql
SELECT pc.id, pc.partner_id, pc.scheme_id, pc.cam_type, pc.version,
       pc.status, cm.meta_group, cm.meta_key, cm.meta_value
FROM partner_cam_configuration pc
JOIN cams_meta cm ON cm.configuration_id = pc.id
ORDER BY pc.id, cm.meta_group, cm.display_order, cm.id;
```

Open the generated `config.db` with SQLite or a database client. To demonstrate lifecycle changes, create a new INACTIVE version, clone the metadata, edit only the unapproved version, validate using `assemble()`, regression-test and obtain appropriate human/lender approval. In one transaction end the old version and activate the approved successor. The regression tests demonstrate this transition. Production authentication, approval workflow, rollback orchestration and automatic expiry processing remain design scope, not exposed mutation APIs. A rollback should publish an authorized new version containing previously approved values, rather than edit history.

Reports and proposed queries include partner ID, scheme ID, CAM type, configuration ID, version, status and historical replay indicator. Persist the configuration ID with each CAM revision in the host LOS. The intended production split is one extraction/fact calculation per immutable borrower snapshot and separate evaluation per lender/scheme; this demo recomputes facts per verification request and does not persist borrower cases.

## 8. Output and safety boundaries

Claim statuses: `SUPPORTED` (specific parsed claim matches evidence), `MISMATCH` (wrong figure/unit/period), `UNSUPPORTED` (cannot establish claim), `CONFLICT` (evidence disagrees with positive consistency wording), `INCOMPLETE` (missing comparable evidence). Overall `FAIL` means a mismatch or blocking policy issue, `REVIEW` means warnings or unresolved claims, `PASS` means no findings within this verifier's limited checks. **PASS never means credit approval**. Human approval is always required.

Findings include CAM line, original text, metric, claimed value, evidence and reason. Policy issues include completeness, cash share, bureau score, variance and CAM section checks. Query proposals are returned as draft records with case, partner configuration, line and question. They are not sent to customers. A production LOS should persist query IDs, communication history, responses, evidence, reviewer decision and CAM revisions, re-run verification, then close the query. The separate design describes this workflow and human-approved learning; neither WhatsApp integration nor autonomous learning is required or implemented here.

## 9. Tests and limitations

```bash
python -m pytest -q
```


To extend live: add an adapter mapping or metric in `metrics.py`, its canonical claim label in `claims.py`, and a pytest fixture. Add a scheme-specific CAM configuration version in a controlled migration rather than hard-coding a lender in the verifier. Rerun tests and the sample report after every change.
