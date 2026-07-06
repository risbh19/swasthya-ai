
set -euo pipefail

PROJECT_ID="swasthya-ai-hackathon"
REGION="asia-south1"
SERVICE_NAME="swasthya-ai"
SERVICE_ACCOUNT="swasthya-ai-sa@${PROJECT_ID}.iam.gserviceaccount.com"
BQ_DATASET="swasthya_ai"

gcloud config set project "$PROJECT_ID"

echo ">>> Building and deploying via Cloud Build + Cloud Run"
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$REGION" \
  --service-account "$SERVICE_ACCOUNT" \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION},BQ_DATASET=${BQ_DATASET}" \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 300

echo ""
echo "Deployed. Cloud Run will print the live HTTPS URL above."
echo "Note: --allow-unauthenticated makes the demo public. For a hackathon"
echo "demo link that's usually what you want; remove that flag + add"
echo "'gcloud run services add-iam-policy-binding' if you need it private."
