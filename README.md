# Swasthya AI — Real Google Cloud Version

This is the GCP-native rebuild of the local prototype. It uses **actual**
BigQuery, Vertex AI, BigQuery ML, and ADK — not stand-ins — so it's valid
for a hackathon submission that requires real Google Cloud usage.

I can't run these commands myself (this sandbox has no network access to
`googleapis.com`), so you'll run them from Cloud Shell or your own
terminal, where you're actually authenticated. Every command below is
copy-pasteable.

---

## 0. Prerequisites (10 min)

1. **Google Cloud account** — https://console.cloud.google.com
   New accounts get **$300 free credit / 90 days**, which is more than
   enough for a hackathon build. Add a billing account (required even
   for free credit, but you won't be charged unless you exceed it).
2. **Create a project**: Console → top bar → "New Project" → name it
   e.g. `swasthya-ai-hackathon`. Note the **Project ID** (not the display
   name — it's the lowercase-with-hyphens one).
3. Install the `gcloud` CLI if working locally, or just use **Cloud
   Shell** (icon top-right of the Console) which has everything
   pre-installed. I'll assume Cloud Shell below — it's the fastest path.
4. In Cloud Shell:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

---

## 1. Get the code onto your machine / Cloud Shell

Download `swasthya_ai_gcp.zip` (link at the end of this chat) and upload
it into Cloud Shell (⋮ menu → Upload), or `git clone` it if you push it
to your own repo first. Then:

```bash
unzip swasthya_ai_gcp.zip && cd swasthya_ai_gcp
```

---

## 2. Edit the placeholders

Open `infra/setup.sh` and `infra/deploy_cloud_run.sh` and replace:
```
PROJECT_ID="your-gcp-project-id"   →  your real project ID
```
(Region `asia-south1` = Mumbai is a good default if you're targeting
India; change if you like.)

---

## 3. Bootstrap the project (enables APIs, creates BigQuery dataset/tables,
   service account, storage bucket)

```bash
chmod +x infra/setup.sh
bash infra/setup.sh
```

This runs for a couple of minutes the first time (API enablement is
slow). It will also try to create the BigQuery tables from
`sql/create_tables.sql`.

**Note on Vertex AI access**: `aiplatform.googleapis.com` needs no extra
approval — it's enabled instantly like any other API, unlike the old
allowlisted Gemini API. No waiting.

---

## 4. Generate and load the data

```bash
pip install -r requirements.txt   # or: pip install pandas numpy
python3 data_gen.py --to-csv
```

This prints the exact `bq load` commands (also shown here for
convenience — fill in your project ID):

```bash
bq load --source_format=CSV --skip_leading_rows=1 \
  YOUR_PROJECT:swasthya_ai.symptom_reports data/symptom_reports.csv \
  report_date:DATE,district:STRING,block:STRING,village:STRING,disease:STRING,cases:INTEGER,population:INTEGER

bq load --source_format=CSV --skip_leading_rows=1 \
  YOUR_PROJECT:swasthya_ai.immunization_records data/immunization_records.csv \
  report_date:DATE,district:STRING,block:STRING,village:STRING,vaccine:STRING,children_due:INTEGER,children_covered:INTEGER
```

Verify it landed:
```bash
bq query --use_legacy_sql=false 'SELECT COUNT(*) FROM `swasthya_ai.symptom_reports`'
```

> **Swap in real data later**: once this works, replace
> `data/*.csv` with a real IDSP/Dataful/HMIS export shaped to the same
> columns, and reload with the same `bq load` commands. Everything
> downstream is unchanged.

---

## 5. Train the BigQuery ML anomaly model (this is your real "AI capability #2")

```bash
bq query --use_legacy_sql=false < sql/train_anomaly_model.sql
```

This creates two `ARIMA_PLUS` models directly inside BigQuery
(`disease_spike_model`, `immunization_dropoff_model`). Training takes
1-3 minutes. You can sanity-check anomalies show up with:

```bash
bq query --use_legacy_sql=false '
SELECT * FROM ML.DETECT_ANOMALIES(
  MODEL `swasthya_ai.disease_spike_model`,
  STRUCT(0.95 AS anomaly_prob_threshold))
WHERE is_anomaly = TRUE
ORDER BY anomaly_probability DESC
LIMIT 10'
```

You should see the seeded Dengue spike and vaccine-coverage drop show up.

---

## 6. Run the app locally against real GCP services

```bash
gcloud auth application-default login    # lets your laptop act as "you" for BigQuery/Vertex AI
export GCP_PROJECT_ID=YOUR_PROJECT_ID
export GCP_REGION=asia-south1
export BQ_DATASET=swasthya_ai
streamlit run app.py
```

Open the printed local URL. All three tabs (NL Q&A, Anomalies, Agent)
now hit real BigQuery + Vertex AI + ADK — no API key box needed anymore,
since Vertex AI auth comes from your `gcloud auth application-default login`.

---

## 7. Deploy it publicly on Cloud Run (so you have a live demo link for submission)

```bash
chmod +x infra/deploy_cloud_run.sh
bash infra/deploy_cloud_run.sh
```

This builds your container via Cloud Build and deploys to Cloud Run,
using the service account created in step 3 — so no key files are ever
uploaded or embedded. It prints a live `https://swasthya-ai-xxxxx.a.run.app`
URL at the end. That's your demo link.

---

## 8. (Optional, but strengthens the "decision-support" criterion) Looker Studio dashboard

1. Go to https://lookerstudio.google.com → **Create → Report**
2. Connector: **BigQuery** → select your project → `swasthya_ai` dataset
   → `symptom_reports` or a query-based custom table.
3. Add a time-series chart (cases by week, filter by village/disease) and
   a table of the ML.DETECT_ANOMALIES output (use a **Custom Query**
   connector with the SQL from step 5).
4. Share the report link alongside your Cloud Run demo — this is the
   "district officer decision-support" piece from the architecture doc,
   and it costs zero extra code.

---

## 9. What maps to what (for your submission writeup)

| Requirement | What's actually running |
|---|---|
| Understand & analyze data | BigQuery tables (`symptom_reports`, `immunization_records`) |
| NL Q&A | Vertex AI Gemini (`modules/nl2sql_vertex.py`) → generates SQL → runs on BigQuery → Gemini summarizes |
| Pattern/anomaly detection | **BigQuery ML** `ARIMA_PLUS` models + `ML.DETECT_ANOMALIES` (`sql/train_anomaly_model.sql`) |
| Recommendations | Gemini reasoning inside the ADK agent instruction |
| Workflow automation | ADK **tool call** (`dispatch_notification`) writes to BigQuery; wire to Cloud Functions + Twilio/SendGrid for a real SMS (see step 10) |
| Decision-support dashboard | Looker Studio on top of BigQuery |
| Explainability | `anomaly_probability` / model decomposition from BigQuery ML, shown directly in the UI |

---

## 10. Optional stretch: make the notification real (not just a BigQuery row)

Create a Cloud Function triggered on BigQuery table inserts (via
Eventarc) that calls Twilio's SMS API. That's a genuinely separate
piece of infra — happy to write that Cloud Function for you if you want
to add it; just ask, and tell me whether you have (or want to set up) a
Twilio trial account, since the function needs an API key for that.

---

## Cost note

At hackathon scale (a few thousand rows, a handful of Gemini calls,
Cloud Run's free tier of ~2M requests/month) this comfortably stays
inside the $300 free credit — likely under $1-2 total even with the ML
model training. The only easy way to rack up cost is running big BigQuery
scans repeatedly on an unpartitioned huge table, which doesn't apply here.
