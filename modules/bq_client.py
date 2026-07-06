"""
Real BigQuery data-access layer. Replaces modules/db.py (SQLite) from the
local prototype. Same function names/shapes so nl2sql.py and app.py barely
change.

Auth: uses Application Default Credentials (ADC). Locally:
    gcloud auth application-default login
On Cloud Run: the service account attached to the service is used
automatically - no key file needed.
"""

import os
import pandas as pd
from google.cloud import bigquery

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "your-gcp-project-id")
DATASET = os.environ.get("BQ_DATASET", "swasthya_ai")

SCHEMA_DESCRIPTION = f"""
Table: `{PROJECT_ID}.{DATASET}.symptom_reports`
  - report_date (DATE, weekly)
  - district (STRING)
  - block (STRING)
  - village (STRING)
  - disease (STRING) -- one of: Dengue, Malaria, Acute Diarrhoeal Disease, Chikungunya, Typhoid, Seasonal Flu
  - cases (INT64)
  - population (INT64)

Table: `{PROJECT_ID}.{DATASET}.immunization_records`
  - report_date (DATE, weekly)
  - district (STRING)
  - block (STRING)
  - village (STRING)
  - vaccine (STRING) -- one of: BCG, OPV, Pentavalent, Measles-Rubella
  - children_due (INT64)
  - children_covered (INT64)
"""

ALLOWED_START = ("select", "with")
FORBIDDEN_KEYWORDS = ("insert", "update", "delete", "drop", "alter", "merge", "create", "truncate")

_client = None


def get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT_ID)
    return _client


def is_safe_select(sql: str) -> bool:
    s = sql.strip().lower()
    if not s.startswith(ALLOWED_START):
        return False
    if any(kw in s for kw in FORBIDDEN_KEYWORDS):
        return False
    return True


def run_query(sql: str) -> pd.DataFrame:
    if not is_safe_select(sql):
        raise ValueError(f"Refusing to run unsafe/non-SELECT query: {sql}")
    client = get_client()
   
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    client.query(sql, job_config=job_config)  # raises if invalid
    return client.query(sql).to_dataframe()


def load_table(table_name: str, limit: int = 5000) -> pd.DataFrame:
    sql = f"SELECT * FROM `{PROJECT_ID}.{DATASET}.{table_name}` LIMIT {limit}"
    return get_client().query(sql).to_dataframe()


def insert_notification(row: dict) -> None:
    """Real workflow-automation write target: a BigQuery table, which a
    Cloud Function / Pub-Sub subscriber can also watch to fire an actual
    SMS (Twilio) or email (SendGrid) - see modules/agent_adk.py."""
    table_id = f"{PROJECT_ID}.{DATASET}.notifications_log"
    errors = get_client().insert_rows_json(table_id, [row])
    if errors:
        raise RuntimeError(f"BigQuery insert failed: {errors}")
