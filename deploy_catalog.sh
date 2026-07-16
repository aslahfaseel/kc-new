#!/bin/bash
alias gcloud='/apps/opt/application/google-cloud-sdk/bin/gcloud'
set -x
echo "Executing from deploy_catalog.sh"

WORKSPACE_DIR=$1
BRANCH_NAME=$2
GIT_COMMIT_HASH=$3
targetenv="dev"

# --- Refresh GCP Auth token ---
set +x 
. ~/.bash_profile
set -x

export http_proxy=http://proxy.ebiz.verizon.com:80
export https_proxy=http://proxy.ebiz.verizon.com:80
setDEVenv
# ------------------------------

cd ${WORKSPACE_DIR} 
echo "Detecting changes for deployment..."

# Ensure we have enough local commit history on this branch for HEAD~1 to work
git fetch --depth=5 2>/dev/null

# Clean directory-based tracking using HEAD~1
APPS_CHANGED=$(git diff --name-only HEAD~1 HEAD -- cloud_run/ 2>/dev/null | cut -d/ -f2 | sort | uniq)
TF_CHANGED=$(git diff --name-only HEAD~1 HEAD -- terraform_code/envs/${targetenv}/cloud_run/ 2>/dev/null | cut -d/ -f5 | sort | uniq)

# Global custom module check
SHARED_CR_CHANGED=$(git diff --name-only HEAD~1 HEAD -- terraform_code/custom_modules/cloudrun_job/ 2>/dev/null)
if [ ! -z "$SHARED_CR_CHANGED" ]; then
    echo "Shared Cloud Run module updated. Queueing all apps for fresh deployments."
    TF_CHANGED=$(ls -1 ${WORKSPACE_DIR}/cloud_run/ 2>/dev/null)
fi

ALL_CHANGED_CLOUDRUN=$(echo -e "$APPS_CHANGED\n$TF_CHANGED" | sed '/^$/d' | sort | uniq)

# Track Dataplex environment or shared module changes
DATAPLEX_ENV_CHANGED=$(git diff --name-only HEAD~1 HEAD -- terraform_code/envs/${targetenv}/dataplex/ 2>/dev/null)
SHARED_DP_CHANGED=$(git diff --name-only HEAD~1 HEAD -- terraform_code/custom_modules/ 2>/dev/null | grep "dataplex_")

if [ ! -z "$DATAPLEX_ENV_CHANGED" ] || [ ! -z "$SHARED_DP_CHANGED" ]; then
    DATAPLEX_CHANGED="true"
else
    DATAPLEX_CHANGED=""
fi

# --- DEPLOY DATAPLEX ---
if [ ! -z "$DATAPLEX_CHANGED" ]; then
    echo "Changes detected in Dataplex. Deploying..."
    cd "${WORKSPACE_DIR}/terraform_code/envs/${targetenv}/dataplex"
    cp "${WORKSPACE_DIR}/oidc_token.json" .
    
    rm -rf .terraform
    terraform init
    terraform plan -out=tfplan
    terraform apply -auto-approve tfplan
    
    if [[ $? -ne 0 ]]; then 
        echo "Dataplex deployment failed!"
        exit 1
    fi
else
    echo "No Dataplex changes detected."
fi

# --- DEPLOY CLOUD RUN JOBS ---
if [ -z "$ALL_CHANGED_CLOUDRUN" ]; then
    echo "No Cloud Run apps or terraform modules changed. Skipping."
    exit 0
fi

for APP_NAME in $ALL_CHANGED_CLOUDRUN; do
    TF_DIR="${WORKSPACE_DIR}/terraform_code/envs/${targetenv}/cloud_run/${APP_NAME}"
    
    if [ -d "$TF_DIR" ]; then
        echo "Deploying Terraform for: ${APP_NAME}"
        cd "${TF_DIR}"
        cp "${WORKSPACE_DIR}/oidc_token.json" .
        
        rm -rf .terraform
        terraform init
        
        echo "Clearing any failure taints on the resource..."
        terraform untaint 'module.cloudrun_job.google_cloud_run_v2_job.job' 2>/dev/null
        
        terraform plan -var="image_tag=${GIT_COMMIT_HASH}" -out=tfplan
        terraform apply -auto-approve tfplan
        
        if [[ $? -ne 0 ]]; then 
            echo "Terraform deployment failed for ${APP_NAME}!"
            exit 1
        fi
    else
        echo "Warning: No Terraform directory found at ${TF_DIR}. Skipping."
    fi
done

echo "All modified resources deployed successfully!"
