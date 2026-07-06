"""
Conversational Analytics module — real Vertex AI version.

Replaces modules/nl2sql.py (Gemini developer API + SQLite) with:
  - Vertex AI's Gemini endpoint (project-scoped, enterprise auth/quotas,
    not the free-tier AI Studio key)
  - BigQuery as the query engine instead of SQLite

This is the direct code-level analog of "Vertex AI Conversational
Analytics (NL-to-SQL) over BigQuery" from the architecture doc.
"""

import os
import re
import pandas as pd
import vertexai
from vertexai.generative_models import GenerativeModel

from modules.bq_client import SCHEMA_DESCRIPTION, run_query, PROJECT_ID

LOCATION = os.environ.get("GCP_REGION", "asia-south1")
MODEL_NAME = os.environ.get("VERTEX_MODEL", "gemini-2.5-flash")

_initialized = False


def _init():
    global _initialized
    if not _initialized:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _initialized = True


NL2SQL_PROMPT_TEMPLATE = """You are a SQL generator for a BigQuery-based community health
surveillance system. Given the schema below and a user's natural language
question, output ONLY a single valid BigQuery Standard SQL SELECT query.
No explanations, no markdown fences, no semicolons at the end.

Rules:
- Only use SELECT statements. Never write/alter data.
- Only use fully-qualified table names exactly as given in the schema.
- Only use columns that exist in the schema.
- If the question needs a date range and none is given, use the full data range.
- If the question is ambiguous, make a reasonable assumption and answer it anyway.
- CRITICAL: if you ORDER BY an aggregate (e.g. SUM(cases), COUNT(*), AVG(...)),
  you MUST also include that same aggregate in the SELECT list (with an alias
  like `total_cases`), so the numeric value is actually visible in the result,
  not just used for sorting.
- In this dataset, `village` and `block` are currently identical to `district`
  (the source data is district-level only) — treat all three as equivalent,
  and prefer grouping by `district` unless the user specifically says "block"
  or "village".
- Return ONLY the raw SQL query text.

Schema:
{schema}

User question: "{question}"

SQL query:"""

SUMMARY_PROMPT_TEMPLATE = """You are a health-data assistant speaking to a district health officer
or ASHA/community health worker. Given the user's original question and the
resulting data (as a small table), write a short, clear, plain-language
answer (2-4 sentences). Mention concrete numbers/places from the data.
If the data is empty, say so plainly and suggest a reason.

Original question: "{question}"

Result data (CSV):
{data_csv}

Answer:"""


def _clean_sql(raw_sql: str) -> str:
    sql = raw_sql.strip()
    sql = re.sub(r"^```(sql)?", "", sql, flags=re.IGNORECASE).strip()
    sql = re.sub(r"```$", "", sql).strip()
    return sql.rstrip(";").strip()


_QUERY_PARTS_RE = re.compile(
    r"^\s*SELECT\s+(?P<select>.*?)\s+FROM\s+(?P<rest_from>.*?)"
    r"(?P<orderby>\s+ORDER\s+BY\s+(?P<orderexpr>.*?))?"
    r"(?P<limit>\s+LIMIT\s+\d+\s*)?$",
    re.IGNORECASE | re.DOTALL,
)


def _ensure_order_by_columns_are_selected(sql: str) -> str:
    """Deterministic safety net: the LLM is instructed to include any
    ORDER BY aggregate (SUM/COUNT/AVG/...) in the SELECT list too, so the
    actual number is visible in results - but prompt instructions aren't
    100% reliable across calls. This repairs the query in code whenever
    the model forgets, rather than hoping the prompt alone is enough.
    """
    m = _QUERY_PARTS_RE.match(sql)
    if not m or not m.group("orderexpr"):
        return sql  

    select_clause = m.group("select")
    order_expr_raw = m.group("orderexpr").strip()

    
    terms = [t.strip() for t in order_expr_raw.split(",")]
    new_terms = []
    extra_select_items = []

    for i, term in enumerate(terms):
        term_match = re.match(r"^(?P<expr>.+?)(?P<dir>\s+(ASC|DESC))?$", term, re.IGNORECASE)
        expr = term_match.group("expr").strip()
        direction = term_match.group("dir") or ""

        is_function_call = "(" in expr  # e.g. SUM(cases), COUNT(*)
        already_selected = re.search(re.escape(expr), select_clause, re.IGNORECASE) is not None

        if is_function_call and not already_selected:
            alias = f"agg_metric_{i}"
            extra_select_items.append(f"{expr} AS {alias}")
            new_terms.append(f"{alias}{direction}")
        else:
            new_terms.append(term)

    if not extra_select_items:
        return sql  

    new_select = select_clause + ", " + ", ".join(extra_select_items)
    new_order_by = ", ".join(new_terms)

    fixed_sql = sql[: m.start("select")] + new_select + sql[m.end("select"): m.start("orderexpr")] \
        + new_order_by + sql[m.end("orderexpr"):]
    return fixed_sql


def nl_to_sql(question: str) -> str:
    _init()
    model = GenerativeModel(MODEL_NAME)
    prompt = NL2SQL_PROMPT_TEMPLATE.format(schema=SCHEMA_DESCRIPTION, question=question)
    response = model.generate_content(prompt)
    sql = _clean_sql(response.text)
    return _ensure_order_by_columns_are_selected(sql)


def summarize_result(question: str, df: pd.DataFrame) -> str:
    _init()
    model = GenerativeModel(MODEL_NAME)
    csv_preview = df.head(20).to_csv(index=False) if not df.empty else "(no rows returned)"
    prompt = SUMMARY_PROMPT_TEMPLATE.format(question=question, data_csv=csv_preview)
    response = model.generate_content(prompt)
    return response.text.strip()


def ask(question: str) -> dict:
    sql = nl_to_sql(question)
    df = run_query(sql)
    answer = summarize_result(question, df)
    return {"question": question, "sql": sql, "dataframe": df, "answer": answer}
