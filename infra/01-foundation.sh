#!/usr/bin/env bash
# Step 1. Creates what the two container apps need, in this order:
#
#   1. THE BUDGET ALERT on the subscription, before anything else (only the cost
#      providers, which are free, are registered first: the budget needs them);
#   2. the resource group (free);
#   3. the remaining resource providers (free);
#   4. Log Analytics, the Container Apps environment, the storage account with its file
#      share, the container registry and the identity that pulls images.
#
#   bash infra/01-foundation.sh --emails you@example.com,owner@example.com
#
# Options:
#   --emails a,b         Who receives the budget alerts. Two addresses. Not stored anywhere.
#   --skip-budget        Only after creating the budget by hand in the portal (see below).
#   --recreate-budget    Delete and create the budget again (to change the addresses).
#   --yes                Do not ask for confirmation.
#
# Safe to run again: what exists is left as it is or updated in place.
#
# The budget is on the whole SUBSCRIPTION: it warns about any spending in the account,
# not only this project's. It is therefore NOT inside the resource group, and deleting
# the group (teardown.sh) does not delete it. To remove it by hand after the demo:
#
#   Portal: Cost Management > Budgets > scope: the subscription > the budget > Delete.
#   CLI:    az consumption budget delete --budget-name <BUDGET_NAME>
#           (the name is BUDGET_NAME in infra/lib.sh; with no resource group given, the
#           command acts on the subscription the CLI is signed in to)
#
# If the budget cannot be created (the Consumption API refuses some new or free
# subscriptions), the script stops BEFORE creating anything else. Create it in the portal
# (Cost Management > Budgets, scope: the subscription, 50% and 100% of the actual cost)
# and run again with --skip-budget.

# shellcheck source=infra/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

EMAILS=""
SKIP_BUDGET=0
RECREATE_BUDGET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --emails) EMAILS="${2:-}"; shift 2 ;;
    --skip-budget) SKIP_BUDGET=1; shift ;;
    --recreate-budget) RECREATE_BUDGET=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    *) die "Unknown option: $1" ;;
  esac
done

# ---------------------------------------------------------------- checks, before anything

require_tools
load_subscription

# Persist the suffix so every subsequent script resolves the same resource names without
# recomputing it. Written once; never overwrites a value already in the file.
ENV_DEPLOY="$INFRA_DIR/.env.deploy"
if ! grep -q "^SUFFIX=" "$ENV_DEPLOY" 2>/dev/null; then
  printf 'SUFFIX=%s\n' "$SUFFIX" >> "$ENV_DEPLOY"
  info "Saved resource-name suffix to $ENV_DEPLOY (SUFFIX=$SUFFIX)."
fi

EMAIL_JSON=""
if [ "$SKIP_BUDGET" = "0" ]; then
  [ -n "$EMAILS" ] || die "Pass --emails with the two addresses that receive the budget alerts."
  IFS=',' read -r -a EMAIL_LIST <<< "$EMAILS"
  [ "${#EMAIL_LIST[@]}" -ge 2 ] || die "--emails needs two addresses, separated by a comma (yours and the account owner's)."
  for email in "${EMAIL_LIST[@]}"; do
    [[ "$email" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "Not an e-mail address: $email"
    EMAIL_JSON="${EMAIL_JSON:+$EMAIL_JSON, }\"$email\""
  done
fi

step "Subscription"
show_subscription
require_free_offer
info "Region: $LOCATION. Resource group: $RESOURCE_GROUP."
confirm "Create or update the resources in THIS subscription?" || die "Cancelled. Nothing was created."

register_providers() {
  local namespace
  for namespace in "$@"; do
    if [ "$(provider_state "$namespace")" = "Registered" ]; then
      info "$namespace: already registered"
    else
      info "$namespace: registering (free; it can take a few minutes)..."
      az provider register --namespace "$namespace" --wait --only-show-errors
    fi
  done
}

step "Cost providers (free)"
register_providers "${COST_PROVIDERS[@]}"

# ---------------------------------------------------------------- 1. budget

# On the whole subscription, not on the resource group: it warns about any spending in
# the account, including something created outside the group by mistake.
BUDGET_URL="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/providers/Microsoft.Consumption/budgets/$BUDGET_NAME?api-version=2023-11-01"

step "1. Budget alert on the subscription, before anything else is created"
if [ "$SKIP_BUDGET" = "1" ]; then
  warn "--skip-budget: assuming you created the budget for the subscription in the portal."
else
  if [ "$RECREATE_BUDGET" = "1" ] && succeeds az rest --method get --url "$BUDGET_URL"; then
    az rest --method delete --url "$BUDGET_URL" --only-show-errors --output none
    info "Existing budget deleted."
  fi
  if succeeds az rest --method get --url "$BUDGET_URL"; then
    info "Already exists: $BUDGET_NAME, amount $(az rest --method get --url "$BUDGET_URL" --query properties.amount -o tsv)."
    info "Left as it is. To change the addresses run again with --recreate-budget."
  else
    BODY="$(temp_file)"
    # A monthly budget has to start on the first day of a month.
    cat > "$BODY" << EOF
{
  "properties": {
    "category": "Cost",
    "amount": $BUDGET_AMOUNT,
    "timeGrain": "Monthly",
    "timePeriod": {
      "startDate": "$(date -u +%Y-%m-01)T00:00:00Z",
      "endDate": "$(($(date -u +%Y) + 1))-$(date -u +%m)-01T00:00:00Z"
    },
    "notifications": {
      "actual_50_percent": {
        "enabled": true,
        "operator": "GreaterThanOrEqualTo",
        "threshold": 50,
        "thresholdType": "Actual",
        "contactEmails": [$EMAIL_JSON]
      },
      "actual_100_percent": {
        "enabled": true,
        "operator": "GreaterThanOrEqualTo",
        "threshold": 100,
        "thresholdType": "Actual",
        "contactEmails": [$EMAIL_JSON]
      }
    }
  }
}
EOF
    if ! az rest --method put --url "$BUDGET_URL" --body "@$(native_path "$BODY")" \
      --headers "Content-Type=application/json" --only-show-errors --output none; then
      die "The budget could not be created, so nothing else was created: no resource group and no
resource. Create it in the portal (Cost Management > Budgets, scope: the subscription
$SUBSCRIPTION_NAME, amount $BUDGET_AMOUNT, alerts at 50% and 100% of the actual cost) and run this
script again with --skip-budget."
    fi
    info "Created: $BUDGET_NAME on the subscription, amount $BUDGET_AMOUNT per month, alerts at 50% and 100% to ${#EMAIL_LIST[@]} addresses."
    info "The amount is in the billing currency of the account, which may not be USD: check it in the portal."
    info "It covers everything spent in the subscription, not only this project."
  fi
fi

# ---------------------------------------------------------------- 2. resource group

step "2. Resource group (free)"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" \
  --tags project=contoso-invoice-matching purpose=demo --only-show-errors --output none
info "$RESOURCE_GROUP"
RESOURCE_GROUP_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"

# ---------------------------------------------------------------- 3. providers

step "3. Resource providers (free)"
register_providers "${RESOURCE_PROVIDERS[@]}"
if ! succeeds az extension show --name containerapp; then
  info "Installing the Azure CLI extension 'containerapp' on this machine..."
  az extension add --name containerapp --only-show-errors
fi

# ---------------------------------------------------------------- 4. resources

step "4a. Log Analytics (first 5 GB a month are free; capped at $LOG_DAILY_CAP_GB GB a day)"
az monitor log-analytics workspace create --resource-group "$RESOURCE_GROUP" --workspace-name "$LOG_WORKSPACE" \
  --location "$LOCATION" --retention-time 30 --quota "$LOG_DAILY_CAP_GB" --only-show-errors --output none
info "$LOG_WORKSPACE"

step "4b. Container Apps environment (no cost by itself; apps pay per second of use)"
if succeeds az containerapp env show --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP"; then
  info "Already exists: $CONTAINER_ENV"
else
  info "Creating $CONTAINER_ENV (this takes several minutes)..."
  WORKSPACE_ID="$(az monitor log-analytics workspace show --resource-group "$RESOURCE_GROUP" \
    --workspace-name "$LOG_WORKSPACE" --query customerId -o tsv)"
  WORKSPACE_KEY="$(az monitor log-analytics workspace get-shared-keys --resource-group "$RESOURCE_GROUP" \
    --workspace-name "$LOG_WORKSPACE" --query primarySharedKey -o tsv)"
  az containerapp env create --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" \
    --logs-destination log-analytics --logs-workspace-id "$WORKSPACE_ID" --logs-workspace-key "$WORKSPACE_KEY" \
    --only-show-errors --output none
  unset WORKSPACE_KEY
fi
# Only the Consumption profile is free of fixed cost. A dedicated profile bills per hour.
PROFILES="$(az containerapp env show --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" \
  --query "properties.workloadProfiles[].workloadProfileType" -o tsv | sort -u | tr '\n' ' ')"
case "$PROFILES" in
  "" | "Consumption ") info "Workload profiles: ${PROFILES:-none (consumption only)}" ;;
  *) die "The environment has a dedicated workload profile ($PROFILES), which is billed by the hour. Remove it." ;;
esac

step "4c. Storage account and file share (about USD 0.06 per GiB stored per month)"
if succeeds az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP"; then
  info "Already exists: $STORAGE_ACCOUNT"
else
  az storage account create --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" \
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --https-only true \
    --allow-blob-public-access false --only-show-errors --output none
  info "Created: $STORAGE_ACCOUNT"
fi
if succeeds az storage share-rm show --storage-account "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --name "$FILE_SHARE"; then
  info "Already exists: file share $FILE_SHARE"
else
  az storage share-rm create --storage-account "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
    --name "$FILE_SHARE" --quota 1 --access-tier TransactionOptimized --only-show-errors --output none
  info "Created: file share $FILE_SHARE (1 GiB)"
fi
# The environment keeps the account key to mount the share. It is never printed.
STORAGE_KEY="$(az storage account keys list --account-name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
  --query "[0].value" -o tsv)"
az containerapp env storage set --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" \
  --storage-name "$ENV_STORAGE_NAME" --storage-type AzureFile --azure-file-account-name "$STORAGE_ACCOUNT" \
  --azure-file-account-key "$STORAGE_KEY" --azure-file-share-name "$FILE_SHARE" --access-mode ReadWrite \
  --only-show-errors --output none
unset STORAGE_KEY
info "The environment can mount the share as '$ENV_STORAGE_NAME'."

step "4d. Container registry, Basic (about USD 0.17 a day while it exists, from the credit)"
if succeeds az acr show --name "$REGISTRY" --resource-group "$RESOURCE_GROUP"; then
  info "Already exists: $REGISTRY"
else
  az acr create --name "$REGISTRY" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" \
    --sku Basic --admin-enabled false --only-show-errors --output none
  info "Created: $REGISTRY"
fi

step "4e. Identity the container apps use to pull images (free)"
if succeeds az identity show --name "$PULL_IDENTITY" --resource-group "$RESOURCE_GROUP"; then
  info "Already exists: $PULL_IDENTITY"
else
  az identity create --name "$PULL_IDENTITY" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" \
    --only-show-errors --output none
  info "Created: $PULL_IDENTITY"
fi
PRINCIPAL_ID="$(az identity show --name "$PULL_IDENTITY" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)"
REGISTRY_ID="$(az acr show --name "$REGISTRY" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
ASSIGNED="$(az role assignment list --assignee "$PRINCIPAL_ID" --scope "$REGISTRY_ID" --role AcrPull --query "length(@)" -o tsv 2> /dev/null || printf '0')"
if [ "$ASSIGNED" != "0" ] && [ -n "$ASSIGNED" ]; then
  info "Already allowed to pull from $REGISTRY."
else
  # A new identity takes a moment to be visible to role assignments.
  for attempt in 1 2 3 4 5 6; do
    if az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
      --role AcrPull --scope "$REGISTRY_ID" --only-show-errors --output none 2> /dev/null; then
      info "Allowed to pull from $REGISTRY (role AcrPull, on the registry only)."
      break
    fi
    [ "$attempt" = "6" ] && die "Could not give $PULL_IDENTITY the AcrPull role on $REGISTRY. Run the script again in a minute."
    sleep 10
  done
fi

# ---------------------------------------------------------------- summary

step "Done"
info "Resource group:     $RESOURCE_GROUP ($LOCATION)"
info "Environment:        $CONTAINER_ENV"
info "Registry:           $REGISTRY.azurecr.io"
info "Storage and share:  $STORAGE_ACCOUNT / $FILE_SHARE"
info "Frontend address:   https://$(web_app_fqdn)   (nothing is deployed there yet)"
info "Costs while idle:   the registry, about USD 0.17 a day. Everything else is free or per use."
info "Budget:             $BUDGET_NAME, on the subscription. It is outside the resource group:"
info "                    teardown.sh does not delete it. To delete it by hand, see the top of this file."
info "Next: bash infra/02-entra.sh"
