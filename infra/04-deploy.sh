#!/usr/bin/env bash
# Step 4. Creates or updates the two container apps from the images pushed by step 3.
#
#   bash infra/04-deploy.sh <tag>          the tag printed by 03-build-push.sh
#
# Options:
#   --yes   Do not ask for confirmation (it still asks for the model key when one is needed).
#
# Backend  (API_APP in lib.sh): reachable only from inside the environment, never from the
#   internet. The file share is mounted at /app/data with the "nobrl" option, which SQLite
#   needs on this kind of share. It runs with APP_ENV=entra. Its secrets are the model's
#   key and endpoint: typed here, stored as Container Apps secrets, never in the image,
#   the repository or this script's output.
# Frontend (WEB_APP in lib.sh): public, behind Azure's built-in authentication with the
#   Entra application registered by 02-entra.sh. Nobody reaches it without signing in. Its
#   proxy forwards /api to the backend over the environment's internal address.
#
# Both scale to zero when idle and never run more than one replica (the database is a
# single SQLite file).
#
# Safe to run again with the same or a new tag: each run applies the full definition, and
# a new revision replaces the running one only when something changed.

# shellcheck source=infra/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1; shift ;;
    -*) die "Unknown option: $1" ;;
    *) TAG="$1"; shift ;;
  esac
done
[ -n "$TAG" ] || die "Pass the image tag: bash infra/04-deploy.sh <tag>   (03-build-push.sh prints it)"
[[ "$TAG" =~ ^[A-Za-z0-9._-]+$ ]] || die "Not a valid tag: $TAG"

require_tools
load_subscription

# ---------------------------------------------------------------- checks

step "Subscription"
show_subscription
require_free_offer
succeeds az containerapp env show --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" \
  || die "The environment $CONTAINER_ENV does not exist. Run 01-foundation.sh first."

SERVER="$REGISTRY.azurecr.io"
for repository in "$API_IMAGE" "$WEB_IMAGE"; do
  az acr repository show-tags --name "$REGISTRY" --repository "$repository" --query "contains(@, '$TAG')" -o tsv 2> /dev/null \
    | grep -q true || die "The registry has no image $repository:$TAG. Run 03-build-push.sh first."
done
IDENTITY_ID="$(az identity show --name "$PULL_IDENTITY" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
ENVIRONMENT_ID="$(az containerapp env show --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
CLIENT_ID="$(entra_client_id)"
WEB_FQDN="$(web_app_fqdn)"
DEPLOYMENT_NAME="${AZURE_OPENAI_DEPLOYMENT:-}"
[[ "$DEPLOYMENT_NAME" =~ ^[A-Za-z0-9._-]*$ ]] || die "AZURE_OPENAI_DEPLOYMENT has characters a deployment name cannot have."

API_NEW=1
WEB_NEW=1
if app_exists "$API_APP"; then API_NEW=0; fi
if app_exists "$WEB_APP"; then WEB_NEW=0; fi

step "What this run does"
info "Backend:   $API_APP  <- $SERVER/$API_IMAGE:$TAG   ($([ "$API_NEW" = 1 ] && echo "new" || echo "update: a new revision replaces the running one"))"
info "Frontend:  $WEB_APP  <- $SERVER/$WEB_IMAGE:$TAG   ($([ "$WEB_NEW" = 1 ] && echo "new" || echo "update: a new revision replaces the running one"))"
info "Address:   https://$WEB_FQDN"
info "Sign-in:   Entra application $CLIENT_ID, directory $TENANT_ID"
info "While a revision is replaced, a request in progress may fail and a document being"
info "extracted at that moment is interrupted."
confirm "Go ahead?" || die "Cancelled. Nothing was changed."

# ---------------------------------------------------------------- the model's key and endpoint

step "Model credentials (stored as secrets of the backend only)"
OPENAI_KEY=""
OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-}"
if [ "$API_NEW" = "1" ]; then
  info "The backend does not exist yet, so both are needed."
else
  info "The backend already has them. Press Enter at each question to keep the stored value."
fi
if [ -z "$OPENAI_ENDPOINT" ]; then
  read -r -p "   Azure OpenAI endpoint (https://.../openai/v1): " OPENAI_ENDPOINT
  OPENAI_ENDPOINT="${OPENAI_ENDPOINT%$'\r'}"
fi
read -r -s -p "   Azure OpenAI API key (not shown while you type): " OPENAI_KEY
printf '\n'
OPENAI_KEY="${OPENAI_KEY%$'\r'}"
if [ "$API_NEW" = "1" ]; then
  [ -n "$OPENAI_KEY" ] || die "The API key is needed to create the backend."
  [ -n "$OPENAI_ENDPOINT" ] || die "The endpoint is needed to create the backend."
fi
if [ -n "$OPENAI_ENDPOINT" ]; then
  [[ "$OPENAI_ENDPOINT" =~ ^https://[A-Za-z0-9./_-]+$ ]] || die "The endpoint must be an https:// address."
fi

# ---------------------------------------------------------------- backend

step "Backend: $API_APP"
if [ "$API_NEW" = "1" ]; then
  # Created with what belongs to the app as a whole: internal ingress, the identity that
  # pulls the image, and the secrets. The template is applied right after, below.
  az containerapp create --name "$API_APP" --resource-group "$RESOURCE_GROUP" --environment "$CONTAINER_ENV" \
    --image "$SERVER/$API_IMAGE:$TAG" --container-name "$API_CONTAINER" \
    --ingress internal --target-port 8000 \
    --user-assigned "$IDENTITY_ID" --registry-server "$SERVER" --registry-identity "$IDENTITY_ID" \
    --cpu 0.5 --memory 1.0Gi --min-replicas 0 --max-replicas 1 \
    --secrets "$SECRET_OPENAI_KEY=$OPENAI_KEY" "$SECRET_OPENAI_ENDPOINT=$OPENAI_ENDPOINT" \
    --only-show-errors --output none
  info "Created, with internal ingress only."
else
  NEW_SECRETS=()
  if [ -n "$OPENAI_KEY" ]; then NEW_SECRETS+=("$SECRET_OPENAI_KEY=$OPENAI_KEY"); fi
  if [ -n "$OPENAI_ENDPOINT" ]; then NEW_SECRETS+=("$SECRET_OPENAI_ENDPOINT=$OPENAI_ENDPOINT"); fi
  if [ "${#NEW_SECRETS[@]}" -gt 0 ]; then
    confirm "Replace the stored model credentials with the ones you just typed?" || die "Cancelled before changing anything in the backend."
    az containerapp secret set --name "$API_APP" --resource-group "$RESOURCE_GROUP" \
      --secrets "${NEW_SECRETS[@]}" --only-show-errors --output none
    info "Secrets replaced."
  else
    info "Stored model credentials kept."
  fi
  INGRESS="$(az containerapp show --name "$API_APP" --resource-group "$RESOURCE_GROUP" \
    --query properties.configuration.ingress.external -o tsv)"
  [ "$INGRESS" = "false" ] || die "The backend's ingress is not internal (external=$INGRESS). It must not be reachable from the internet.
Fix it before going on: az containerapp ingress update --name $API_APP --resource-group $RESOURCE_GROUP --type internal"
fi
unset OPENAI_KEY NEW_SECRETS

API_TEMPLATE="$(temp_file)"
cat > "$API_TEMPLATE" << EOF
{
  "properties": {
    "template": {
      "containers": [
        {
          "name": "$API_CONTAINER",
          "image": "$SERVER/$API_IMAGE:$TAG",
          "resources": { "cpu": 0.5, "memory": "1Gi" },
          "env": [
            { "name": "APP_ENV", "value": "entra" },
            { "name": "FRONTEND_ORIGIN", "value": "https://$WEB_FQDN" },
            { "name": "AZURE_OPENAI_API_KEY", "secretRef": "$SECRET_OPENAI_KEY" },
            { "name": "AZURE_OPENAI_ENDPOINT", "secretRef": "$SECRET_OPENAI_ENDPOINT" },
$([ -n "$DEPLOYMENT_NAME" ] && printf '            { "name": "AZURE_OPENAI_DEPLOYMENT", "value": "%s" },\n' "$DEPLOYMENT_NAME")
            { "name": "DATABASE_URL", "value": "sqlite:///$DATA_MOUNT_PATH/contoso.db" },
            { "name": "UPLOADS_DIR", "value": "$DATA_MOUNT_PATH/uploads" },
            { "name": "EXTRACTION_CACHE_DIR", "value": "$DATA_MOUNT_PATH/cache/extractions" },
            { "name": "REFERENCE_DATA_DIR", "value": "$DATA_MOUNT_PATH/reference" }
          ],
          "volumeMounts": [ { "volumeName": "$DATA_VOLUME", "mountPath": "$DATA_MOUNT_PATH" } ]
        }
      ],
      "scale": { "minReplicas": 0, "maxReplicas": 1 },
      "volumes": [
        {
          "name": "$DATA_VOLUME",
          "storageType": "AzureFile",
          "storageName": "$ENV_STORAGE_NAME",
          "mountOptions": "nobrl"
        }
      ]
    }
  }
}
EOF
patch_app "$API_APP" "$API_TEMPLATE"
MOUNT_OPTIONS="$(az containerapp show --name "$API_APP" --resource-group "$RESOURCE_GROUP" \
  --query "properties.template.volumes[0].mountOptions" -o tsv)"
[ "$MOUNT_OPTIONS" = "nobrl" ] || die "The share is mounted without 'nobrl' (got '$MOUNT_OPTIONS'). SQLite would fail with 'database is locked'."
API_FQDN="$(app_fqdn "$API_APP")"
info "File share mounted at $DATA_MOUNT_PATH (nobrl). Internal address: $API_FQDN"

# ---------------------------------------------------------------- frontend

step "Frontend: $WEB_APP"
if [ "$WEB_NEW" = "1" ]; then
  az containerapp create --name "$WEB_APP" --resource-group "$RESOURCE_GROUP" --environment "$CONTAINER_ENV" \
    --image "$SERVER/$WEB_IMAGE:$TAG" --container-name "$WEB_CONTAINER" \
    --ingress external --target-port 3000 \
    --user-assigned "$IDENTITY_ID" --registry-server "$SERVER" --registry-identity "$IDENTITY_ID" \
    --cpu 0.25 --memory 0.5Gi --min-replicas 0 --max-replicas 1 \
    --only-show-errors --output none
  info "Created."
fi
ACTUAL_FQDN="$(app_fqdn "$WEB_APP")"
[ "$ACTUAL_FQDN" = "$WEB_FQDN" ] || die "The frontend's address is $ACTUAL_FQDN, not $WEB_FQDN as registered for sign-in.
Run 02-entra.sh again so the sign-in redirect matches, then this script."

step "Frontend: sign-in required (Microsoft Entra ID)"
# Configured before the application is given its real settings, so there is no moment in
# which the frontend serves the app without asking for a sign-in.
az containerapp auth microsoft update --name "$WEB_APP" --resource-group "$RESOURCE_GROUP" \
  --client-id "$CLIENT_ID" --tenant-id "$TENANT_ID" --allowed-audiences "$CLIENT_ID" "api://$CLIENT_ID" \
  --yes --only-show-errors --output none
az containerapp auth update --name "$WEB_APP" --resource-group "$RESOURCE_GROUP" \
  --enabled true --unauthenticated-client-action RedirectToLoginPage --redirect-provider azureactivedirectory \
  --only-show-errors --output none
AUTH_ACTION="$(az containerapp auth show --name "$WEB_APP" --resource-group "$RESOURCE_GROUP" \
  --query globalValidation.unauthenticatedClientAction -o tsv)"
AUTH_ENABLED="$(az containerapp auth show --name "$WEB_APP" --resource-group "$RESOURCE_GROUP" \
  --query platform.enabled -o tsv)"
if [ "$AUTH_ENABLED" != "true" ] || [ "$AUTH_ACTION" != "RedirectToLoginPage" ]; then
  die "Authentication is not in force on the frontend (enabled=$AUTH_ENABLED, action=$AUTH_ACTION). Do not share the address."
fi
info "Unauthenticated visitors are sent to Microsoft's sign-in page."

WEB_TEMPLATE="$(temp_file)"
cat > "$WEB_TEMPLATE" << EOF
{
  "properties": {
    "template": {
      "containers": [
        {
          "name": "$WEB_CONTAINER",
          "image": "$SERVER/$WEB_IMAGE:$TAG",
          "resources": { "cpu": 0.25, "memory": "0.5Gi" },
          "env": [
            { "name": "NEXT_PUBLIC_AUTH_MODE", "value": "entra" },
            { "name": "API_BASE_URL", "value": "/api" },
            { "name": "API_PROXY_TARGET", "value": "https://$API_FQDN" },
            { "name": "MAX_UPLOAD_MB", "value": "10" }
          ]
        }
      ],
      "scale": { "minReplicas": 0, "maxReplicas": 1 }
    }
  }
}
EOF
patch_app "$WEB_APP" "$WEB_TEMPLATE"

# ---------------------------------------------------------------- summary

step "Done"
info "Open:      https://$WEB_FQDN"
info "Backend:   $API_FQDN (internal: it does not answer from the internet)"
info "Version:   $TAG"
info "Idle cost: none for the two apps (they scale to zero). The first request after a pause"
info "           takes some seconds while they start."
info "Next:      bash infra/05-smoke.sh      then      bash infra/load-reference.sh"
