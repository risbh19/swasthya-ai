
set -euo pipefail


PROJECT_ID="swasthya-ai-hackathon"
REGION="asia-south1"          
BQ_DATASET="swasthya_ai"
SERVICE_ACCOUNT="swasthya-ai-sa"
BUCKET_NAME="${PROJECT_ID}-swasthya-data"


echo ">>> Setting active project"
gcloud config set project "$PROJECT_ID"

echo ">>> Enabling required APIs (takes ~1-2 min the first time)"
gcloud services enable \
  bigquery.googleapis.com \
  aiplatform.googleapis.com \
  run.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  documentai.googleapis.com \
  storage.googleapis.com \
  pubsub.googleapis.com \
  eventarc.googleapis.com

echo ">>> Creating BigQuery dataset"
bq --location="$REGION" mk --dataset --description "Swasthya AI health surveillance data" \
  "${PROJECT_ID}:${BQ_DATASET}" || echo "(dataset may already exist, continuing)"

echo ">>> Creating Cloud Storage bucket for CSV staging"
gsutil mb -l "$REGION" "gs://${BUCKET_NAME}" || echo "(bucket may already exist, continuing)"

echo ">>> Creating service account for the app / Cloud Run"
gcloud iam service-accounts create "$SERVICE_ACCOUNT" \
  --display-name="Swasthya AI service account" || echo "(SA may already exist, continuing)"

SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"

echo ">>> Granting roles to the service account"
for ROLE in roles/bigquery.dataEditor roles/bigquery.jobUser roles/aiplatform.user roles/storage.objectAdmin roles/run.invoker; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" --quiet
done

echo ">>> Creating BigQuery tables"
bq query --use_legacy_sql=false < "$(dirname "$0")/../sql/create_tables.sql"

echo ""
echo "=================================================================="
echo "Done. Next steps:"
echo "1. Generate + load data:      python3 data_gen.py --to-csv"
echo "   bq load --source_format=CSV --skip_leading_rows=1 \\"
echo "     ${PROJECT_ID}:${BQ_DATASET}.symptom_reports data/symptom_reports.csv"
echo "   bq load --source_format=CSV --skip_leading_rows=1 \\"
echo "     ${PROJECT_ID}:${BQ_DATASET}.immunization_records data/immunization_records.csv"
echo "2. (Optional, for local dev) create a key for the service account:"
echo "   gcloud iam service-accounts keys create sa-key.json --iam-account=${SA_EMAIL}"
echo "   export GOOGLE_APPLICATION_CREDENTIALS=\$(pwd)/sa-key.json"
echo "3. Train the BigQuery ML anomaly model:"
echo "   bq query --use_legacy_sql=false < sql/train_anomaly_model.sql"
echo "4. Run locally:  streamlit run app.py"
echo "5. Deploy to Cloud Run: bash infra/deploy_cloud_run.sh"
echo "=================================================================="
