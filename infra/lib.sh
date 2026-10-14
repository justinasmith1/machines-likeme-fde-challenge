#!/usr/bin/env bash
# Shared by every script in this folder: names, checks and small helpers.
# It is sourced, never run. It changes nothing in Azure by itself.
#
# No secret and nothing that identifies the tenant or the subscription lives here. Names
# can be overridden from the environment or from infra/.env.deploy (git-ignored).

set -euo pipefail

# Git Bash rewrites arguments that start with "/" into Windows paths, which breaks Azure
# resource ids and scopes ("/subscriptions/..."). Switched off for these scripts. Files
# handed to the Azure CLI go through native_path instead.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

# On Windows the Azure CLI ends every line of its output with CR LF. Bash keeps the CR,
# so a value read from it ("Owner\r") never equals the text it is compared with, and a
# name built from it carries an invisible character. Every call to az in these scripts
# goes through this function, which removes the CR and keeps the CLI's exit code.
az() {
  command az "$@" | tr -d '\r'
  return "${PIPESTATUS[0]}"
}

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$INFRA_DIR/.env.deploy" ]; then
  # Read without the CR a Windows editor may have saved at the end of each line.
  # shellcheck disable=SC1090
  . <(tr -d '\r' < "$INFRA_DIR/.env.deploy")
fi

# ---------------------------------------------------------------- names

PREFIX="${PREFIX:-contoso-im}"
LOCATION="${LOCATION:-eastus2}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-$PREFIX-demo}"
# The budget is on the subscription, outside the resource group (see 01-foundation.sh).
BUDGET_NAME="${BUDGET_NAME:-budget-$PREFIX-demo}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-5}"
LOG_WORKSPACE="${LOG_WORKSPACE:-log-$PREFIX}"
LOG_DAILY_CAP_GB="${LOG_DAILY_CAP_GB:-0.1}"
CONTAINER_ENV="${CONTAINER_ENV:-cae-$PREFIX}"
PULL_IDENTITY="${PULL_IDENTITY:-id-$PREFIX-pull}"
FILE_SHARE="${FILE_SHARE:-appdata}"
ENV_STORAGE_NAME="${ENV_STORAGE_NAME:-appdata}"
WEB_APP="${WEB_APP:-ca-$PREFIX-web}"
API_APP="${API_APP:-ca-$PREFIX-api}"
# Images in the registry, and the container inside each app.
API_IMAGE="${API_IMAGE:-api}"
WEB_IMAGE="${WEB_IMAGE:-web}"
API_CONTAINER="api"
WEB_CONTAINER="web"
# Where the file share is mounted in the backend. Its Dockerfile puts the database, the
# uploaded documents, the extraction cache and the reference data under this path.
DATA_MOUNT_PATH="/app/data"
DATA_VOLUME="appdata"
# Secrets of the backend container app (names only; the values are typed at deploy time).
SECRET_OPENAI_KEY="azure-openai-api-key"
SECRET_OPENAI_ENDPOINT="azure-openai-endpoint"
# Version of the Container Apps management API used for what the CLI has no flag for
# (the volume with its mount options).
CONTAINERAPP_API="2024-03-01"
ENTRA_APP_NAME="${ENTRA_APP_NAME:-Contoso Invoice Matching (demo)}"
DEMO_USER_PREFIX="${DEMO_USER_PREFIX:-contoso-demo}"
# Unique suffix for globally-scoped resource names (storage account, registry). Set by
# 01-foundation.sh on the first run and persisted in infra/.env.deploy so every script uses
# the same value. Override here only if the resources were created with a different suffix.
SUFFIX="${SUFFIX:-}"

# Resource providers the deployment uses. Registering one is free.
COST_PROVIDERS=(Microsoft.Consumption Microsoft.CostManagement)
RESOURCE_PROVIDERS=(
  Microsoft.OperationalInsights Microsoft.App Microsoft.Storage
  Microsoft.ContainerRegistry Microsoft.ManagedIdentity
)

# App roles of the Entra application. The value is what the backend reads; it is the name
# of the role in the backend (app/domain/enums.py). The ids are fixed so that running the
# script again updates the same roles instead of creating new ones. They are not secrets.
APP_ROLE_VALUES=(INTAKE AP_ANALYST AP_MANAGER)
APP_ROLE_NAMES=("Intake" "AP Analyst" "AP Manager")
APP_ROLE_IDS=(
  "5b0c7c1e-3f6a-4d0b-9a57-2e3c1d7f8a01"
  "9d2f4e8b-6c1a-4b7e-8f35-7a4b2c9e6d02"
  "c7a1e5d3-8b2f-4e6c-a419-3f5d8e1b7c03"
)
APP_ROLE_DESCRIPTIONS=(
  "Uploads invoices and sees the status of their own documents."
  "Reviews held invoices, corrects extracted data and decides within the approval limit."
  "Everything the AP Analyst does, plus decisions above the limit, parameters and the audit trail."
)
DEMO_USER_SUFFIXES=(intake analyst manager)

# ---------------------------------------------------------------- output

step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '   WARNING: %s\n' "$*" >&2; }
die() {
  printf '\nSTOPPED: %s\n' "$*" >&2
  exit 1
}

# ---------------------------------------------------------------- helpers

# True when the command succeeds. Used to ask "does this resource exist?".
succeeds() { "$@" > /dev/null 2>&1; }

# A path the Azure CLI (a Windows program) can open, from a Git Bash path.
native_path() {
  if command -v cygpath > /dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

TEMP_FILES=()
temp_file() {
  local file
  file="$(mktemp)"
  TEMP_FILES+=("$file")
  printf '%s' "$file"
}
cleanup_temp_files() {
  local file
  for file in "${TEMP_FILES[@]:-}"; do
    [ -n "$file" ] && rm -f "$file"
  done
}
trap cleanup_temp_files EXIT

confirm() {
  # confirm "question"; ASSUME_YES=1 answers yes (for a second run, never the first).
  if [ "${ASSUME_YES:-0}" = "1" ]; then return 0; fi
  local answer
  read -r -p "$1 [y/N] " answer
  answer="${answer%$'\r'}"
  case "$answer" in y | Y | yes | YES) return 0 ;; *) return 1 ;; esac
}

require_tools() {
  command -v az > /dev/null 2>&1 || die "'az' is not installed or not on the PATH."
  succeeds az account show || die "The Azure CLI is not signed in. Run: az login"
}

# ---------------------------------------------------------------- subscription

SUBSCRIPTION_ID=""
SUBSCRIPTION_NAME=""
TENANT_ID=""
SIGNED_IN_AS=""
QUOTA_ID=""
SPENDING_LIMIT=""

load_subscription() {
  SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
  SUBSCRIPTION_NAME="$(az account show --query name -o tsv)"
  TENANT_ID="$(az account show --query tenantId -o tsv)"
  SIGNED_IN_AS="$(az account show --query user.name -o tsv)"
  local url="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID?api-version=2022-12-01"
  QUOTA_ID="$(az rest --method get --url "$url" --query subscriptionPolicies.quotaId -o tsv)"
  SPENDING_LIMIT="$(az rest --method get --url "$url" --query subscriptionPolicies.spendingLimit -o tsv)"

  # Globally unique names (storage account, registry) need a suffix that is the same on
  # every run for this subscription. SUFFIX is read from .env.deploy when it exists (written
  # there by 01-foundation.sh). On a first run it is computed here and 01-foundation.sh saves
  # it. sha256sum is tried first; python and PowerShell are fallbacks for Windows/Git Bash.
  if [ -z "$SUFFIX" ]; then
    local stripped="${SUBSCRIPTION_ID//-/}"
    if command -v sha256sum > /dev/null 2>&1; then
      SUFFIX="$(printf '%s' "$stripped" | sha256sum | cut -c1-8)"
    elif command -v python3 > /dev/null 2>&1; then
      SUFFIX="$(python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:8])" "$stripped")"
    elif command -v python > /dev/null 2>&1; then
      SUFFIX="$(python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:8])" "$stripped")"
    else
      SUFFIX="$(powershell -NoProfile -Command \
        "([System.BitConverter]::ToString(([System.Security.Cryptography.SHA256]::Create()).ComputeHash([System.Text.Encoding]::UTF8.GetBytes('$stripped'))).Replace('-','')).Substring(0,8).ToLower()" \
        | tr -d '\r')"
    fi
  fi
  local compact="${PREFIX//-/}"
  STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-st${compact}${SUFFIX}}"
  REGISTRY="${REGISTRY:-acr${compact}${SUFFIX}}"
}

# The whole plan rests on this: a free account charges nothing while its spending limit is
# on. If the subscription is anything else, creating resources could cost real money.
# Returns 0 when it is a free account with the limit on; prints why otherwise.
free_offer_problem() {
  case "$QUOTA_ID" in
    FreeTrial*) ;;
    *)
      if [ "${ALLOW_OFFER:-}" != "$QUOTA_ID" ]; then
        printf 'the subscription offer is "%s", not a free account (FreeTrial...)' "$QUOTA_ID"
        return 0
      fi
      ;;
  esac
  if [ "$SPENDING_LIMIT" != "On" ]; then
    printf 'the spending limit is "%s", not "On": the subscription may have been moved to pay-as-you-go' "$SPENDING_LIMIT"
    return 0
  fi
  return 1
}

require_free_offer() {
  local problem
  if problem="$(free_offer_problem)"; then
    die "$problem. Nothing was created. Resources here could be charged to the card on the account."
  fi
}

show_subscription() {
  info "Subscription:   $SUBSCRIPTION_NAME"
  info "Subscription id: $SUBSCRIPTION_ID"
  info "Tenant id:      $TENANT_ID"
  info "Signed in as:   $SIGNED_IN_AS"
  info "Offer:          $QUOTA_ID"
  info "Spending limit: $SPENDING_LIMIT"
}

provider_state() {
  az provider show --namespace "$1" --query registrationState -o tsv 2> /dev/null || printf 'Unknown'
}

# Address the frontend will have, known as soon as the environment exists.
web_app_fqdn() {
  local domain
  domain="$(az containerapp env show --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" \
    --query properties.defaultDomain -o tsv)"
  printf '%s.%s' "$WEB_APP" "$domain"
}

app_url() { # app_url <app name>: the management address of a container app
  printf 'https://management.azure.com/subscriptions/%s/resourceGroups/%s/providers/Microsoft.App/containerApps/%s?api-version=%s'     "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$1" "$CONTAINERAPP_API"
}

app_exists() { succeeds az containerapp show --name "$1" --resource-group "$RESOURCE_GROUP"; }

app_fqdn() {
  az containerapp show --name "$1" --resource-group "$RESOURCE_GROUP"     --query properties.configuration.ingress.fqdn -o tsv
}

# Applies a JSON merge patch (a file) to a container app and waits until Azure has
# finished with it. Used for the template, which the CLI's flags do not cover fully.
patch_app() { # patch_app <app name> <json file>
  az rest --method patch --url "$(app_url "$1")" --body "@$(native_path "$2")"     --headers "Content-Type=application/json" --only-show-errors --output none
  wait_for_app "$1"
}

wait_for_app() { # wait_for_app <app name>: until the last change is applied and ready
  local state latest ready attempt
  for attempt in $(seq 1 60); do
    state="$(az containerapp show --name "$1" --resource-group "$RESOURCE_GROUP" --query properties.provisioningState -o tsv)"
    latest="$(az containerapp show --name "$1" --resource-group "$RESOURCE_GROUP" --query properties.latestRevisionName -o tsv)"
    ready="$(az containerapp show --name "$1" --resource-group "$RESOURCE_GROUP" --query properties.latestReadyRevisionName -o tsv)"
    case "$state" in
      Succeeded) if [ -n "$latest" ] && [ "$latest" = "$ready" ]; then info "$1: revision $latest is ready."; return 0; fi ;;
      Failed | Canceled) die "$1: Azure reports the change as $state. See: az containerapp revision list --name $1 --resource-group $RESOURCE_GROUP -o table" ;;
    esac
    sleep 5
  done
  die "$1: the new revision is not ready after 5 minutes (latest: $latest, ready: $ready).
See its logs: az containerapp logs show --name $1 --resource-group $RESOURCE_GROUP --tail 50"
}

# The application 02-entra.sh registered, found by its name. Not stored anywhere.
entra_client_id() {
  local found
  found="$(az ad app list --display-name "$ENTRA_APP_NAME" --query "[?displayName=='$ENTRA_APP_NAME'].appId" -o tsv)"
  [ "$(printf '%s' "$found" | grep -c . || true)" = "1" ]     || die "Expected exactly one app registration named '$ENTRA_APP_NAME'. Run 02-entra.sh."
  printf '%s' "$found"
}
