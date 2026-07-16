#!/bin/bash
alias gcloud='/apps/opt/application/google-cloud-sdk/bin/gcloud'
set -x

WORKSPACE_DIR=$1
GIT_COMMIT_HASH=$2 

# --- AUTHENTICATION PRESTEP ---
set +x 
. ~/.bash_profile
set -x 

export http_proxy=http://proxy.ebiz.verizon.com:80
export https_proxy=http://proxy.ebiz.verizon.com:80

setDEVenv
# ------------------------------

AR_REGION="us-east4"
PROJECT_ID="vz-it-np-keiv-dev-dpev-0"
REPO_NAME="vz-it-keiv-dpev-0-docker"
BASE_IMAGE_URI="${AR_REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"
BUILD_SA="projects/${PROJECT_ID}/serviceAccounts/sa-dev-keiv-app-dpev-0@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Detecting changes in cloud_run directories..."
cd ${WORKSPACE_DIR}

# Ensure we have enough local commit history on this branch for HEAD~1 to work
git fetch --depth=5 2>/dev/null

# Clean directory-based tracking using HEAD~1
APPS_CHANGED=$(git diff --name-only HEAD~1 HEAD -- cloud_run/ 2>/dev/null | cut -d/ -f2 | sort | uniq)
TF_CHANGED=$(git diff --name-only HEAD~1 HEAD -- terraform_code/envs/dev/cloud_run/ 2>/dev/null | cut -d/ -f5 | sort | uniq)

# Global custom module check
SHARED_CR_CHANGED=$(git diff --name-only HEAD~1 HEAD -- terraform_code/custom_modules/cloudrun_job/ 2>/dev/null)
if [ ! -z "$SHARED_CR_CHANGED" ]; then
    echo "Shared Cloud Run module updated. Triggering catch-all rebuild."
    TF_CHANGED=$(ls -1 ${WORKSPACE_DIR}/cloud_run/ 2>/dev/null)
fi

CHANGED_FOLDERS=$(echo -e "$APPS_CHANGED\n$TF_CHANGED" | sed '/^$/d' | sort | uniq)

if [ -z "$CHANGED_FOLDERS" ]; then
    echo "No cloud_run applications modified in this commit. Skipping builds."
else
    for APP_NAME in $CHANGED_FOLDERS; do
        APP_DIR="${WORKSPACE_DIR}/cloud_run/${APP_NAME}"
        if [ -d "$APP_DIR" ]; then
            echo "Building image for: ${APP_NAME} using commit hash: ${GIT_COMMIT_HASH}"
            gcloud builds submit "${APP_DIR}" \
                --tag="${BASE_IMAGE_URI}/${APP_NAME}:${GIT_COMMIT_HASH}" \
                --project="${PROJECT_ID}" \
                --service-account="${BUILD_SA}" \
                --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET
                
            if [[ $? -ne 0 ]]; then 
                echo "Failed to build image for ${APP_NAME}"
                exit 1
            fi
        fi
    done
    echo "All applicable images built and pushed successfully!"
fi
