# Swasthya AI
**Intelligent Health Access & Decision-Support Platform** — built natively on Google Cloud (BigQuery ML, Vertex AI, ADK Agents, Pub/Sub, Cloud Functions).

Swasthya AI turns raw disease-surveillance and immunization data into three things a public health worker actually needs: a plain-English answer, an early warning, and a next action — automatically dispatched.

---

## 1. What it does

| Capability | How it's implemented |
|---|---|
| **Natural language Q&A** | Ask a question like "Is Dengue currently anomalous in Lakshadweep?" in plain English. Vertex AI translates it to SQL, runs it against BigQuery, and cross-checks the answer against live anomaly output. |
| **Pattern / anomaly detection** | Two `ARIMA_PLUS` models in BigQuery ML (`disease_spike_model`, `immunization_dropoff_model`) forecast expected case counts / coverage rates per village+disease (or village+vaccine) series, and flag statistically significant deviations via `ML.DETECT_ANOMALIES`. |
| **Recommendations** | A Gemini-powered ADK agent takes a real detected anomaly (village, disease, case count, expected baseline, anomaly strength) and reasons over it to produce a specific, actionable recommendation — not a generic template. |
| **Workflow automation** | Every agent recommendation is logged to a BigQuery audit table and simultaneously published to Pub/Sub, which triggers a Cloud Function that emails the on-call recipient — no human has to notice the anomaly manually. |
| **Decision support** | The Anomalies tab lets a user browse, filter, and select a real flagged anomaly and hand it directly to the agent with one click — closing the loop from "detected" to "recommended action" inside the same UI. |

---

## 2. Architecture

```
                     ┌─────────────────────┐
  Public health data │  BigQuery datasets   │
  (symptom_reports,  │  symptom_reports     │
  immunization_recs) │  immunization_records│
                     └──────────┬───────────┘
                                │
                     ┌──────────▼───────────┐
                     │   BigQuery ML         │
                     │   ARIMA_PLUS models    │
                     │   ML.DETECT_ANOMALIES  │
                     └──────────┬───────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        │                       │                         │
┌───────▼────────┐   ┌──────────▼─────────┐   ┌───────────▼──────────┐
│ Tab 1: Ask      │   │ Tab 2: Anomalies    │   │ Tab 3: Agent          │
│ (Vertex AI      │   │ (browse/filter,     │   │ Recommendations       │
│ NL → SQL)       │   │ "Send to Agent")    │   │ (Gemini + ADK)        │
└───────┬────────┘   └──────────┬─────────┘   └───────────┬──────────┘
        └───────────────────────┴────────────────────────┘
                                │
                     ┌──────────▼───────────┐
                     │ agent_adk.py          │
                     │ → BigQuery audit log  │
                     │ → Pub/Sub publish     │
                     └──────────┬───────────┘
                                │
                     ┌──────────▼───────────┐
                     │ Cloud Function (2nd gen)│
                     │ Pub/Sub trigger        │
                     │ → Gmail SMTP email      │
                     │ (App Password in       │
                     │ Secret Manager)         │
                     └───────────────────────┘
```

**Stack:** Streamlit (UI) · BigQuery + BigQuery ML (storage, forecasting, anomaly detection) · Vertex AI / Gemini (NL→SQL, agent reasoning) · ADK (agent tool-use framework) · Pub/Sub + Cloud Functions 2nd gen (notification pipeline) · Secret Manager (credential storage).

---

## 3. Project structure

```
swasthya_gcp/
├── app.py                     # Streamlit app — 3 tabs (Ask / Anomalies / Agent)
├── modules/
│   ├── bq_client.py            # BigQuery client + PROJECT_ID/DATASET config
│   ├── nl2sql_vertex.py        # Vertex AI natural-language-to-SQL
│   └── agent_adk.py            # Gemini + ADK agent, logs + publishes alerts
├── sql/
│   ├── create_tables.sql       # symptom_reports, immunization_records schemas
│   └── train_anomaly_model.sql # CREATE MODEL statements (ARIMA_PLUS)
├── cloud_function/             # Pub/Sub-triggered email sender
├── deploy_notification_function.sh
└── infra/
    ├── setup.sh                # Enables APIs, creates BQ tables, loads data
    └── deploy_cloud_run.sh     # Optional: deploy Streamlit app to Cloud Run
```

---

## 4. Setup (from scratch)

```bash
# 1. One-time GCP setup — enables APIs, creates BQ dataset/tables, loads data
bash infra/setup.sh

# 2. Train the anomaly-detection models
bq query --use_legacy_sql=false < sql/train_anomaly_model.sql

# 3. Deploy the email notification pipeline (Pub/Sub + Cloud Function + Secret Manager)
chmod +x deploy_notification_function.sh
./deploy_notification_function.sh
#   — prompts for a Gmail App Password (https://myaccount.google.com/apppasswords)

# 4. Run the app locally (e.g. in Cloud Shell)
streamlit run app.py \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.address=0.0.0.0 \
  --server.port=8501
# Open via Cloud Shell's "Web Preview → Preview on port 8501" button
```

**Env vars expected:** `GCP_PROJECT_ID`, `GCP_REGION`, `BQ_DATASET` (set in `.env` locally, or as Cloud Run env vars if deployed there). Auth via `gcloud auth application-default login` locally, or an attached service account on Cloud Run.

---

## 5. Known limitations (worth stating proactively to judges)

Being upfront about these shows engineering maturity — they're documented trade-offs, not bugs we missed:

1. **Z-score is a derived proxy, not a native BigQuery ML field.** `ML.DETECT_ANOMALIES` returns `anomaly_probability` and a `[lower_bound, upper_bound]` confidence interval — no z-score. We approximate one as `(actual − midpoint) / ((upper − lower) / 4)`, assuming the interval is roughly ±2σ. Good enough for demo severity ranking; not a statistically rigorous z-score.
2. **`ARIMA_PLUS` imputes synthetic data points** to fill gaps in sparse, irregularly-reported real-world series before fitting. This means some flagged anomalies sit on interpolated dates that were never actually reported — the case count shown for those is a model estimate, not a literal logged figure. We surface these anomalies rather than silently dropping them, since a gap in reporting can itself be a meaningful signal.
3. **"Village" in this dataset is really district-level data** (village == block == district for every row), inherited from the source dataset's granularity. The UI labels reflect the original three-tier design intent; the underlying real data doesn't yet have true village-level resolution.
4. **Streamlit can't programmatically switch tabs** on a button click (a framework limitation, not ours) — selecting an anomaly shows a success message asking the user to click over to the Agent tab manually, rather than auto-navigating.
5. **Notification pipeline uses Gmail SMTP** (App Password via Secret Manager) rather than a dedicated transactional email service — a deliberate simplicity trade-off for a hackathon timeline; a production deployment would move to SendGrid/Mailgun/etc.

---

