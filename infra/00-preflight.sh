#!/usr/bin/env bash
# Step 0. READ-ONLY: checks that the account, the permissions and the tools are what the
# deployment expects. It creates, changes and deletes nothing, in Azure or on this machine.
#
#   bash infra/00-preflight.sh
#
# Run it first. If it ends with problems, do not run the next scripts.

# shellcheck source=infra/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PROBLEMS=0
problem() {
  PROBLEMS=$((PROBLEMS + 1))
  printf '   PROBLEM: %s\n' "$*" >&2
}

step "Tools"
require_tools
info "Azure CLI $(az version --query '"azure-cli"' -o tsv)"
if command -v docker > /dev/null 2>&1; then
  info "$(docker --version)"
  succeeds docker info || warn "Docker is installed but not running. Start it before building the images."
else
  problem "Docker is not installed. Images are built on this machine: 'az acr build' is not available to free accounts."
fi
if succeeds az extension show --name containerapp; then
  info "Azure CLI extension 'containerapp': $(az extension show --name containerapp --query version -o tsv)"
else
  info "Azure CLI extension 'containerapp' is not installed; 01-foundation.sh installs it."
fi
command -v openssl > /dev/null 2>&1 || problem "openssl is not on the PATH (02-entra.sh uses it to generate passwords)."

step "Subscription"
load_subscription
show_subscription
if reason="$(free_offer_problem)"; then
  problem "$reason."
else
  info "OK: free account with the spending limit on. Nothing is charged while it stays that way."
fi

step "Permissions on the subscription"
USER_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv 2> /dev/null || true)"
if [ -z "$USER_OBJECT_ID" ]; then
  warn "Your own user could not be read from the directory, so the Owner role was not checked."
  warn "01-foundation.sh fails at the role assignment for the registry if you are not Owner."
else
  ROLES="$(az role assignment list --assignee "$USER_OBJECT_ID" --scope "/subscriptions/$SUBSCRIPTION_ID" \
    --include-inherited --include-groups --query "[].roleDefinitionName" -o tsv 2> /dev/null | sort -u | tr '\n' ',' | sed 's/,$//')"
  info "Azure roles: ${ROLES:-none found}"
  case ",$ROLES," in
    *,Owner,*) info "OK: Owner (needed to give the image-pull identity access to the registry)." ;;
    *) problem "You are not Owner of the subscription. Role assignments in 01-foundation.sh will fail." ;;
  esac
fi

step "Directory roles (Microsoft Entra ID)"
# Informative only. A guest in the directory often cannot read their own roles even when
# they have them, so nothing here counts as a problem: 02-entra.sh tries each step and
# says exactly which role is missing if the directory refuses one.
DIRECTORY_ROLES="$(az rest --method get --url "https://graph.microsoft.com/v1.0/me/memberOf/microsoft.graph.directoryRole" \
  --query "value[].displayName" -o tsv 2> /dev/null | tr '\n' ',' | sed 's/,$//' || true)"
if [ -z "$DIRECTORY_ROLES" ]; then
  warn "Your directory roles could not be read. This is usual for a guest account and does not"
  warn "mean you lack them. 02-entra.sh needs Application Administrator and User Administrator."
else
  info "Directory roles: $DIRECTORY_ROLES"
  for needed in "Application Administrator" "User Administrator"; do
    case ",$DIRECTORY_ROLES," in
      *,"$needed",* | *,"Global Administrator",*) info "OK: $needed." ;;
      *) warn "'$needed' is not in the list. 02-entra.sh will say so if a step is refused." ;;
    esac
  done
fi
DEFAULT_DOMAIN="$(az rest --method get --url "https://graph.microsoft.com/v1.0/domains" \
  --query "value[?isDefault].id | [0]" -o tsv 2> /dev/null || true)"
if [ -n "$DEFAULT_DOMAIN" ]; then
  info "Default domain of the directory (test users are created in it): $DEFAULT_DOMAIN"
else
  warn "The directory's domain could not be read (usual for a guest). Look it up in the portal"
  warn "(Entra ID > Overview > Primary domain) and pass it: bash infra/02-entra.sh --domain <name>"
fi

step "Resource providers (registering one is free; 01-foundation.sh does it)"
for namespace in "${COST_PROVIDERS[@]}" "${RESOURCE_PROVIDERS[@]}"; do
  info "$(printf '%-32s %s' "$namespace" "$(provider_state "$namespace")")"
done

step "What 01-foundation.sh would create"
info "Region:               $LOCATION"
info "Resource group:       $RESOURCE_GROUP   (everything below is inside it, except the budget)"
info "Budget:               $BUDGET_NAME ($BUDGET_AMOUNT, in the billing currency of the account), on the SUBSCRIPTION"
info "Log Analytics:        $LOG_WORKSPACE (daily cap $LOG_DAILY_CAP_GB GB)"
info "Container Apps env.:  $CONTAINER_ENV"
info "Storage account:      $STORAGE_ACCOUNT (file share '$FILE_SHARE')"
info "Container registry:   $REGISTRY (Basic)"
info "Pull identity:        $PULL_IDENTITY"
if succeeds az group show --name "$RESOURCE_GROUP"; then
  info "The resource group already exists; the scripts update what is there."
  if ! succeeds az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP"; then
    AVAILABLE="$(az storage account check-name --name "$STORAGE_ACCOUNT" --query nameAvailable -o tsv 2> /dev/null || true)"
    [ "$AVAILABLE" = "false" ] && problem "The storage account name $STORAGE_ACCOUNT is taken. Set STORAGE_ACCOUNT in infra/.env.deploy."
  fi
else
  info "The resource group does not exist yet."
  AVAILABLE="$(az storage account check-name --name "$STORAGE_ACCOUNT" --query nameAvailable -o tsv 2> /dev/null || true)"
  [ "$AVAILABLE" = "false" ] && problem "The storage account name $STORAGE_ACCOUNT is taken. Set STORAGE_ACCOUNT in infra/.env.deploy."
fi

step "Result"
info "Nothing was created or changed."
if [ "$PROBLEMS" -gt 0 ]; then
  die "$PROBLEMS problem(s) above. Fix them before running 01-foundation.sh."
fi
info "No problems found. Next: bash infra/01-foundation.sh --emails you@example.com,owner@example.com"
