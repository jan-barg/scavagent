# Deploying Scavagent to Cloud Run

Status: this is the prepared procedure; see [STATUS.md](STATUS.md) for which steps have actually run. Nothing here asserts a live deployment.

One Google Cloud project serves the team: `agentic-ai-msds` (Columbia organization, billing enabled). Region `us-east1`.

## What runs where

| Piece | Local development | Cloud Run |
|---|---|---|
| App | `uv run app.py` on 127.0.0.1:8000 | `Dockerfile` image, `python app.py` on 0.0.0.0:$PORT |
| Sessions and progress | SQLite at `.data/scavagent.db` | Firestore (Native mode), collection `sessions` |
| Saved camera stills | SQLite `assets` table | Cloud Storage bucket, `assets/{asset_id}` |
| Model | Vertex AI via your gcloud Application Default Credentials | Vertex AI via the service's runtime service account |

Configuration is by environment variable; see [.env.example](../.env.example). The app does not read `.env` files itself.

## Org policies checked (September 28, 2026)

- `iam.allowedPolicyMemberDomains`: all values allowed, so `--allow-unauthenticated` (an `allUsers` invoker binding) is permitted and graders can reach the URL. Teammates outside `columbia.edu` could also be added.
- `run.managed.requireInvokerIam`: enforced, so `--no-invoker-iam-check` is not available. Use `--allow-unauthenticated` instead.
- No resource-location or service-account-creation restriction is in effect.

## One-time setup

```sh
PROJECT=agentic-ai-msds
REGION=us-east1
BUCKET=agentic-ai-msds-scavagent-photos
SA=scavagent-run@$PROJECT.iam.gserviceaccount.com

gcloud config set project $PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com

gcloud firestore databases create --location=$REGION --type=firestore-native
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access --public-access-prevention

gcloud iam service-accounts create scavagent-run --display-name="Scavagent Cloud Run runtime"
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/aiplatform.user
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/datastore.user
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin
```

The bucket stays private. Photos are served only through the app's `/media/{asset_id}` route.

## First deploy (from a local checkout)

```sh
gcloud run deploy scavagent --source . --region $REGION \
  --service-account $SA --allow-unauthenticated \
  --set-env-vars SCAVAGENT_STORE=firestore,SCAVAGENT_ASSET_BUCKET=$BUCKET
```

This builds the `Dockerfile` with Cloud Build and prints the service URL. Put that URL in `submission.json` only after it answers a real `/chat` request.

Cloud Run's request timeout (default 300 seconds) closes the connection but does not stop the code, so the app bounds each turn itself (`app.TURN_SECONDS`). Keep that under `state.IN_FLIGHT_TIMEOUT` if either changes.

## Continuous deployment from GitHub

The assignment requires Cloud Run continuous deployment from `jan-barg/scavagent`. Connecting GitHub needs Jan's browser approval (Cloud Build GitHub App), so this step is done in the console:

1. Cloud Run → service `scavagent` → **Set up continuous deployment** (or **Connect repo**).
2. Provider GitHub → authenticate and install the Cloud Build GitHub App on `jan-barg/scavagent`.
3. Branch `^main$`, build type **Dockerfile**, source location `/Dockerfile`.
4. Save. Confirm a push to `main` creates a Cloud Build run and a new Cloud Run revision.

Environment variables and the service account set on the service carry over to revisions the trigger deploys.

## Giving Kyle access

Kyle (`kc3936@columbia.edu`) already has `roles/aiplatform.user` and `roles/serviceusage.serviceUsageConsumer`, enough to run the app locally against Vertex AI with his own `gcloud auth application-default login` and `gcloud config set project agentic-ai-msds`. His local model calls bill to this project.

To let him also view logs and deploy:

```sh
gcloud projects add-iam-policy-binding agentic-ai-msds --member=user:kc3936@columbia.edu --role=roles/run.developer
gcloud projects add-iam-policy-binding agentic-ai-msds --member=user:kc3936@columbia.edu --role=roles/logging.viewer
```

Or grant `roles/editor` for broad access during the project.
