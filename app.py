"""
Swasthya AI — GCP-native Streamlit app.

Requires:
  - GCP project with billing enabled
  - APIs enabled + BigQuery tables created + data loaded (see infra/setup.sh)
  - ADC auth set up locally, OR running on Cloud Run with an attached
    service account (see infra/deploy_cloud_run.sh)

Env vars expected (set in .env locally or as Cloud Run env vars):
  GCP_PROJECT_ID, GCP_REGION, BQ_DATASET
"""

import os
import json
import re
import streamlit as st
import pandas as pd

from modules.bq_client import PROJECT_ID, DATASET
from modules import nl2sql_vertex
from google.cloud import bigquery

st.set_page_config(page_title="Swasthya AI (GCP)", layout="wide")

st.sidebar.title("Swasthya AI")
st.sidebar.caption(f"Project: `{PROJECT_ID}` · Dataset: `{DATASET}`")
st.sidebar.info(
    "Running against **real BigQuery + Vertex AI**. "
    "Make sure `gcloud auth application-default login` has been run, "
    "or that this is deployed on Cloud Run with a service account."
)

client = bigquery.Client(project=PROJECT_ID)


if "selected_anomaly" not in st.session_state:
    st.session_state.selected_anomaly = None




@st.cache_data(ttl=300, show_spinner=False)
def fetch_disease_anomalies(project_id: str, dataset: str) -> pd.DataFrame:
    """All current disease-spike anomalies, with block/district recovered via JOIN
    and an approximate z-score derived from the confidence interval width.
    ML.DETECT_ANOMALIES only returns series_id/report_date/cases/is_anomaly/
    lower_bound/upper_bound/anomaly_probability — nothing else — so this JOIN
    and SPLIT is required to get back to human-readable fields."""
    
    query = f"""
        SELECT
            a.report_date,
            SPLIT(a.series_id, '::')[OFFSET(0)] AS village,
            SPLIT(a.series_id, '::')[OFFSET(1)] AS disease,
            SPLIT(a.series_id, '::')[OFFSET(0)] AS block,
            SPLIT(a.series_id, '::')[OFFSET(0)] AS district,
            a.cases AS cases_this_week,
            ROUND((a.lower_bound + a.upper_bound) / 2, 2) AS expected_baseline,
            ROUND(SAFE_DIVIDE(
                a.cases - (a.lower_bound + a.upper_bound) / 2,
                NULLIF((a.upper_bound - a.lower_bound) / 4, 0)
            ), 2) AS z_score_approx,
            ROUND(a.anomaly_probability, 3) AS anomaly_probability
        FROM ML.DETECT_ANOMALIES(
            MODEL `{project_id}.{dataset}.disease_spike_model`,
            STRUCT(0.95 AS anomaly_prob_threshold)) a
        WHERE a.is_anomaly = TRUE
        ORDER BY a.anomaly_probability DESC
        LIMIT 200
    """
    return client.query(query).to_dataframe()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_immunization_anomalies(project_id: str, dataset: str) -> pd.DataFrame:
    """Same idea as fetch_disease_anomalies, for immunization coverage drop-offs."""
    
    query = f"""
        SELECT
            a.report_date,
            SPLIT(a.series_id, '::')[OFFSET(0)] AS village,
            SPLIT(a.series_id, '::')[OFFSET(1)] AS vaccine,
            SPLIT(a.series_id, '::')[OFFSET(0)] AS block,
            SPLIT(a.series_id, '::')[OFFSET(0)] AS district,
            ROUND(a.coverage_rate, 3) AS coverage_rate,
            ROUND((a.lower_bound + a.upper_bound) / 2, 3) AS expected_baseline,
            ROUND(SAFE_DIVIDE(
                a.coverage_rate - (a.lower_bound + a.upper_bound) / 2,
                NULLIF((a.upper_bound - a.lower_bound) / 4, 0)
            ), 2) AS z_score_approx,
            ROUND(a.anomaly_probability, 3) AS anomaly_probability
        FROM ML.DETECT_ANOMALIES(
            MODEL `{project_id}.{dataset}.immunization_dropoff_model`,
            STRUCT(0.95 AS anomaly_prob_threshold)) a
        WHERE a.is_anomaly = TRUE
        ORDER BY a.anomaly_probability DESC
        LIMIT 200
    """
    return client.query(query).to_dataframe()


@st.cache_data(ttl=600, show_spinner=False)
def fetch_known_entities(project_id: str, dataset: str):
    """Distinct disease/vaccine/village names, used to detect what a free-text
    NL question is actually asking about."""
    diseases = client.query(
        f"SELECT DISTINCT disease FROM `{project_id}.{dataset}.symptom_reports`"
    ).to_dataframe()["disease"].dropna().tolist()
    vaccines = client.query(
        f"SELECT DISTINCT vaccine FROM `{project_id}.{dataset}.immunization_records`"
    ).to_dataframe()["vaccine"].dropna().tolist()
    villages = client.query(
        f"""SELECT DISTINCT village FROM `{project_id}.{dataset}.symptom_reports`
            UNION DISTINCT
            SELECT DISTINCT village FROM `{project_id}.{dataset}.immunization_records`"""
    ).to_dataframe()["village"].dropna().tolist()
    return diseases, vaccines, villages


def extract_entities_from_question(question: str, diseases, vaccines, villages):
    """Simple case-insensitive substring match — good enough to detect which
    disease/vaccine/village a free-text question refers to, without an extra
    Vertex AI call."""
    q = question.lower()

    def find_match(candidates):
        for c in sorted(candidates, key=len, reverse=True):  
            if c and re.search(r"\b" + re.escape(c.lower()) + r"\b", q):
                return c
        return None

    return find_match(diseases), find_match(vaccines), find_match(villages)


def build_selected_anomaly_from_row(row, kind: str):
    """kind: 'disease' or 'immunization' — normalizes a row from either
    anomaly dataframe into the dict shape Tab 3's form expects."""
    if kind == "disease":
        return {
            "village": row["village"], "block": row["block"], "district": row["district"],
            "disease": row["disease"],
            "cases_this_week": float(row["cases_this_week"]),
            "expected_baseline": float(row["expected_baseline"]),
            "z_score": float(row["z_score_approx"]) if pd.notna(row["z_score_approx"]) else 0.0,
        }
    return {
        "village": row["village"], "block": row["block"], "district": row["district"],
        "disease": "",  
        "vaccine": row["vaccine"],
        "cases_this_week": float(row["coverage_rate"]),
        "expected_baseline": float(row["expected_baseline"]),
        "z_score": float(row["z_score_approx"]) if pd.notna(row["z_score_approx"]) else 0.0,
    }


tab1, tab2, tab3 = st.tabs(["💬 Ask (NL Q&A)", "📈 Anomalies (BigQuery ML)", "🤖 Agent Recommendations"])


with tab1:
    st.subheader("Ask a question in plain English")
    question = st.text_input(
        "e.g. Which village has the highest number of Dengue cases overall?"
    )

    if "nlqa" not in st.session_state:
        st.session_state.nlqa = None 
    if st.button("Ask", key="ask_btn") and question:
        with st.spinner("Vertex AI is translating your question to SQL and running it on BigQuery..."):
            try:
                result = nl2sql_vertex.ask(question)
            except Exception as e:
                st.error(f"Something went wrong: {e}")
                result = None

        nlqa_state = {"question": question, "result": result, "hit": None, "no_hit_label": None,
                      "no_match": False}

        
        if result is not None:
            try:
                diseases, vaccines, villages = fetch_known_entities(PROJECT_ID, DATASET)
                matched_disease, matched_vaccine, matched_village = extract_entities_from_question(
                    question, diseases, vaccines, villages
                )

                if not matched_disease and not matched_vaccine:
                    nlqa_state["no_match"] = True
                else:
                    hit_row = None
                    hit_kind = None

                    if matched_disease:
                        df_d = fetch_disease_anomalies(PROJECT_ID, DATASET)
                        candidates = df_d[df_d["disease"].str.lower() == matched_disease.lower()]
                        if matched_village:
                            candidates = candidates[candidates["village"].str.lower() == matched_village.lower()]
                        if not candidates.empty:
                            hit_row = candidates.sort_values("anomaly_probability", ascending=False).iloc[0]
                            hit_kind = "disease"

                    if hit_row is None and matched_vaccine:
                        df_v = fetch_immunization_anomalies(PROJECT_ID, DATASET)
                        candidates = df_v[df_v["vaccine"].str.lower() == matched_vaccine.lower()]
                        if matched_village:
                            candidates = candidates[candidates["village"].str.lower() == matched_village.lower()]
                        if not candidates.empty:
                            hit_row = candidates.sort_values("anomaly_probability", ascending=False).iloc[0]
                            hit_kind = "immunization"

                    label = matched_disease or matched_vaccine
                    if hit_row is not None:
                        nlqa_state["hit"] = {
                            "label": label,
                            "row_dict": hit_row.to_dict(),
                            "kind": hit_kind,
                            "matched_village": matched_village,
                        }
                    else:
                        nlqa_state["no_hit_label"] = {"label": label, "matched_village": matched_village}
            except Exception as e:
                nlqa_state["anomaly_check_error"] = str(e)

        st.session_state.nlqa = nlqa_state

    # ---- Render from session_state (survives the rerun triggered by the Send-to-Agent button) ----
    nlqa = st.session_state.nlqa
    if nlqa and nlqa["result"] is not None:
        result = nlqa["result"]
        st.success(result["answer"])
        with st.expander("Show generated SQL + raw result"):
            st.code(result["sql"], language="sql")
            st.dataframe(result["dataframe"])

        st.divider()
        st.markdown("**🔎 Anomaly check**")

        if nlqa.get("no_match"):
            st.caption(
                "Couldn't identify a specific disease or vaccine in your question, "
                "so skipping the anomaly check. Try mentioning one by name "
                "(e.g. 'Dengue', 'Acute Diarrhea', 'MMR')."
            )
        elif nlqa.get("anomaly_check_error"):
            st.warning(f"Anomaly cross-check failed (model may not be trained yet): {nlqa['anomaly_check_error']}")
        elif nlqa.get("hit"):
            hit = nlqa["hit"]
            row_dict = hit["row_dict"]
            st.error(
                f"🚨 **{hit['label']}** IS currently flagged as an anomaly"
                + (f" in **{row_dict['village']}**" if hit["matched_village"] is None else "")
                + f" (probability {row_dict['anomaly_probability']:.0%})."
            )
            st.dataframe(pd.DataFrame([row_dict]), use_container_width=True)
            if st.button("🤖 Send this to the Agent for a recommendation", key="nlqa_send_to_agent"):
                st.session_state.selected_anomaly = build_selected_anomaly_from_row(row_dict, hit["kind"])
                st.success("Loaded ✅ — open the '🤖 Agent Recommendations' tab above to dispatch it.")
        elif nlqa.get("no_hit_label"):
            info = nlqa["no_hit_label"]
            st.success(
                f"✅ **{info['label']}** is NOT currently flagged as an anomaly"
                + (f" in **{info['matched_village']}**" if info["matched_village"] else " anywhere")
                + " — tracking within its BigQuery ML forecast range."
            )

# ---------------- Tab 2: Anomalies (BigQuery ML) ----------------
with tab2:
    st.subheader("Anomalies detected by BigQuery ML (ARIMA_PLUS)")
    st.caption(
        "Model trained via sql/train_anomaly_model.sql. Run that once "
        "(and re-run periodically) before this tab will show results."
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Disease case spikes**")
        try:
            df = fetch_disease_anomalies(PROJECT_ID, DATASET)

            filt_col1, filt_col2 = st.columns(2)
            with filt_col1:
                disease_options = ["All"] + sorted(df["disease"].unique().tolist()) if not df.empty else ["All"]
                disease_filter = st.selectbox("Filter by disease", disease_options, key="disease_filter")
            with filt_col2:
                village_search = st.text_input("Search by village", "", key="village_search_disease")

            filtered_df = df.copy()
            if disease_filter != "All":
                filtered_df = filtered_df[filtered_df["disease"] == disease_filter]
            if village_search:
                filtered_df = filtered_df[filtered_df["village"].str.contains(village_search, case=False, na=False)]

            st.dataframe(filtered_df, use_container_width=True)
            if filtered_df.empty and not df.empty:
                st.caption("No anomalies match this filter — try a different disease or village.")

            df = filtered_df  # downstream selectbox/button operate on the filtered set

            if not df.empty:
                options = [
                    f"{r.village} / {r.disease} ({r.report_date}) — {r.cases_this_week} cases"
                    for r in df.itertuples()
                ]
                idx = st.selectbox(
                    "Pick a disease anomaly to send to the agent:",
                    range(len(options)), format_func=lambda i: options[i], key="disease_pick"
                )
                if st.button("➡️ Send to Agent tab", key="send_disease"):
                    row = df.iloc[idx]
                    st.session_state.selected_anomaly = build_selected_anomaly_from_row(row, "disease")
                    st.success("Loaded. Open the '🤖 Agent Recommendations' tab above to dispatch it.")
        except Exception as e:
            st.warning(f"Model not ready yet or query failed: {e}")

    with col2:
        st.markdown("**Immunization drop-offs**")
        try:
            df2 = fetch_immunization_anomalies(PROJECT_ID, DATASET)

            filt_col3, filt_col4 = st.columns(2)
            with filt_col3:
                vaccine_options = ["All"] + sorted(df2["vaccine"].unique().tolist()) if not df2.empty else ["All"]
                vaccine_filter = st.selectbox("Filter by vaccine", vaccine_options, key="vaccine_filter")
            with filt_col4:
                village_search2 = st.text_input("Search by village", "", key="village_search_immun")

            filtered_df2 = df2.copy()
            if vaccine_filter != "All":
                filtered_df2 = filtered_df2[filtered_df2["vaccine"] == vaccine_filter]
            if village_search2:
                filtered_df2 = filtered_df2[filtered_df2["village"].str.contains(village_search2, case=False, na=False)]

            st.dataframe(filtered_df2, use_container_width=True)
            if filtered_df2.empty and not df2.empty:
                st.caption("No anomalies match this filter — try a different vaccine or village.")

            df2 = filtered_df2  # downstream selectbox/button operate on the filtered set

            if not df2.empty:
                options2 = [
                    f"{r.village} / {r.vaccine} ({r.report_date}) — {r.coverage_rate:.0%} coverage"
                    for r in df2.itertuples()
                ]
                idx2 = st.selectbox(
                    "Pick an immunization anomaly to send to the agent:",
                    range(len(options2)), format_func=lambda i: options2[i], key="immun_pick"
                )
                if st.button("➡️ Send to Agent tab", key="send_immun"):
                    row = df2.iloc[idx2]
                    st.session_state.selected_anomaly = build_selected_anomaly_from_row(row, "immunization")
                    st.success("Loaded. Open the '🤖 Agent Recommendations' tab above to dispatch it.")
        except Exception as e:
            st.warning(f"Model not ready yet or query failed: {e}")

# ---------------- Tab 3: Agent ----------------
with tab3:
    st.subheader("Generate a recommendation + auto-dispatch notification")
    st.caption("Powered by an ADK Agent (Gemini + tool-use) writing to BigQuery.")

    sel = st.session_state.selected_anomaly
    if sel:
        st.info("✅ Real anomaly loaded (from NL Q&A or the Anomalies tab) — edit if needed, then run the agent.")
    else:
        st.caption(
            "No anomaly selected yet — showing sample values. Either ask about "
            "a specific disease/vaccine on the Ask tab, or pick a real row on "
            "the Anomalies tab, for a live demo."
        )

    with st.form("anomaly_form"):
        st.write("Paste an anomaly row (or pick one from the Anomalies/Ask tab) to act on:")
        village = st.text_input("Village", sel["village"] if sel else "Sikandra")
        block = st.text_input("Block", sel["block"] if sel else "Block C")
        district = st.text_input("District", sel["district"] if sel else "District 1")
        disease = st.text_input(
            "Disease (leave blank if immunization issue)",
            sel["disease"] if sel else "Dengue"
        )
        cases_this_week = st.number_input(
            "Cases this week (or coverage rate, if immunization)",
            value=sel["cases_this_week"] if sel else 19.0
        )
        expected_baseline = st.number_input(
            "Expected baseline", value=sel["expected_baseline"] if sel else 1.3
        )
        z_score = st.number_input(
            "Z-score / anomaly strength", value=sel["z_score"] if sel else 11.5
        )
        submitted = st.form_submit_button("Run agent")

    if submitted:
        details = {
            "village": village, "block": block, "district": district,
            "disease": disease, "cases_this_week": cases_this_week,
            "expected_baseline": expected_baseline, "z_score": z_score,
        }
        with st.spinner("Agent is reasoning and dispatching..."):
            try:
                from modules.agent_adk import run_agent_on_anomaly
                response_text = run_agent_on_anomaly(details)
                st.success("Agent finished. Response:")
                st.write(response_text)
                st.caption("Check the `notifications_log` BigQuery table for the audit-trail row.")
                st.session_state.selected_anomaly = None  # clear after successful dispatch
            except Exception as e:
                st.error(f"Agent run failed: {e}")
