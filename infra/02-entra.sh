#!/usr/bin/env bash
# Step 2. Microsoft Entra ID: the application the frontend signs people in with, its three
# roles, and one test user per role. All of it is free. NONE of it is in the resource
# group: deleting the group does not remove it (teardown.sh --entra does).
#
#   bash infra/02-entra.sh
#
# Options:
#   --domain example.onmicrosoft.com   Domain for the test users (default: the directory's).
#   --reset-passwords                  Give existing test users a new password.
#   --yes                              Do not ask for confirmation.
#
# What it creates:
#   - App registration "Contoso Invoice Matching (demo)": this directory only, sign-in
#     redirect to the frontend's address, ID tokens enabled. No client secret.
#   - App roles INTAKE, AP_ANALYST, AP_MANAGER.
#   - Its enterprise application, with "assignment required": only people who were given
#     a role can sign in.
#   - Users <prefix>-intake, <prefix>-analyst, <prefix>-manager, each with one role.
#
# Passwords are generated here and shown ONCE at the end, on this terminal only. They are
# not written to any file. Safe to run again: existing users keep their password.
#
# Permissions: the app registration and its enterprise application need the directory
# role Application Administrator; the test users need User Administrator. A guest in the
# directory often cannot READ their own roles, so this script does not check them in
# advance: it tries each step and, if the directory refuses one, stops there and says
# which role is missing. Steps already done are kept, and running it again continues.
#
# Needs 01-foundation.sh to have run (the frontend's address comes from the environment).

# shellcheck source=infra/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOMAIN=""
RESET_PASSWORDS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --reset-passwords) RESET_PASSWORDS=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    *) die "Unknown option: $1" ;;
  esac
done

GRAPH="https://graph.microsoft.com/v1.0"
APP_ADMIN="Application Administrator (or Cloud Application Administrator)"
USER_ADMIN="User Administrator"

require_tools
command -v openssl > /dev/null 2>&1 || die "openssl is needed to generate the passwords."
load_subscription

# ---------------------------------------------------------------- permission errors

DENIED_MARK="$(temp_file)"
LAST_ERROR="$(temp_file)"
rm -f "$DENIED_MARK"

# directory_call "<what it does>" "<directory role it needs>" command...
# Runs a call to the directory. Its output goes through untouched. When the directory
# refuses it for lack of permission, it explains which role is missing and marks the run
# as denied; the caller then stops with stop_if_denied. Any other failure returns 1 with
# the error kept in LAST_ERROR, so a caller can retry what is only slow to propagate.
directory_call() {
  local what="$1" role="$2"
  shift 2
  if "$@" 2> "$LAST_ERROR"; then return 0; fi
  if grep -qiE "insufficient privileges|Authorization_RequestDenied|does not have (authorization|permission)|\b403\b|Forbidden" "$LAST_ERROR"; then
    {
      printf '\nSTOPPED: the directory did not allow this account to %s.\n' "$what"
      printf 'It needs the directory role: %s.\n' "$role"
      printf 'You are signed in as %s, in the directory %s.\n' "$SIGNED_IN_AS" "$TENANT_ID"
      printf 'Ask the owner of the directory to assign that role (Entra ID > Roles and administrators),\n'
      printf 'sign in again with "az login" so the new role takes effect, and run this script again.\n'
      printf 'What was already created is kept. The answer from the directory was:\n'
      sed 's/^/   /' "$LAST_ERROR" | tail -n 6
    } >&2
    : > "$DENIED_MARK"
  fi
  return 1
}

stop_if_denied() {
  if [ -e "$DENIED_MARK" ]; then exit 1; fi
}

# For a failure that is not about permissions and did not go away with retries.
fail_with_last_error() {
  stop_if_denied
  {
    printf '\nSTOPPED: %s\nThe answer from Azure was:\n' "$1"
    sed 's/^/   /' "$LAST_ERROR" | tail -n 8
  } >&2
  exit 1
}

# ---------------------------------------------------------------- directory

step "Directory"
show_subscription
succeeds az containerapp env show --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" \
  || die "The environment $CONTAINER_ENV does not exist. Run 01-foundation.sh first."
REDIRECT_URI="https://$(web_app_fqdn)/.auth/login/aad/callback"
if [ -z "$DOMAIN" ]; then
  DOMAIN="$(az rest --method get --url "$GRAPH/domains" --query "value[?isDefault].id | [0]" -o tsv 2> /dev/null || true)"
fi
[ -n "$DOMAIN" ] || die "The directory's default domain could not be read (usual for a guest account).
Look it up in the portal (Entra ID > Overview > Primary domain) and pass it:
  bash infra/02-entra.sh --domain <name>.onmicrosoft.com"
info "Test users will be:  $DEMO_USER_PREFIX-{intake,analyst,manager}@$DOMAIN"
info "Sign-in redirect:    $REDIRECT_URI"
confirm "Create or update the application and the test users in THIS directory?" || die "Cancelled. Nothing was created."

# ---------------------------------------------------------------- application and roles

step "1. App registration and its roles"
ROLES_FILE="$(temp_file)"
{
  printf '[\n'
  for i in 0 1 2; do
    if [ "$i" -gt 0 ]; then printf ',\n'; fi
    printf '  {"allowedMemberTypes": ["User"], "isEnabled": true, "id": "%s", "value": "%s", "displayName": "%s", "description": "%s"}' \
      "${APP_ROLE_IDS[$i]}" "${APP_ROLE_VALUES[$i]}" "${APP_ROLE_NAMES[$i]}" "${APP_ROLE_DESCRIPTIONS[$i]}"
  done
  printf '\n]\n'
} > "$ROLES_FILE"

MATCHES="$(directory_call "list the app registrations" "$APP_ADMIN" \
  az ad app list --display-name "$ENTRA_APP_NAME" --query "[?displayName=='$ENTRA_APP_NAME'].appId" -o tsv)" \
  || fail_with_last_error "The app registrations could not be listed."
MATCH_COUNT="$(printf '%s' "$MATCHES" | grep -c . || true)"
[ "$MATCH_COUNT" -le 1 ] || die "There are $MATCH_COUNT applications named '$ENTRA_APP_NAME'. Delete the extra ones in the portal."
CLIENT_ID="$MATCHES"
if [ -z "$CLIENT_ID" ]; then
  CLIENT_ID="$(directory_call "create the app registration" "$APP_ADMIN" \
    az ad app create --display-name "$ENTRA_APP_NAME" --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$REDIRECT_URI" --enable-id-token-issuance true \
    --app-roles "@$(native_path "$ROLES_FILE")" --query appId -o tsv --only-show-errors)" \
    || fail_with_last_error "The app registration could not be created."
  [ -n "$CLIENT_ID" ] || die "The app registration was created but its id was not returned. Run the script again."
  info "Created: $ENTRA_APP_NAME"
else
  directory_call "update the app registration" "$APP_ADMIN" \
    az ad app update --id "$CLIENT_ID" --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$REDIRECT_URI" --enable-id-token-issuance true \
    --app-roles "@$(native_path "$ROLES_FILE")" --only-show-errors --output none \
    || fail_with_last_error "The app registration could not be updated."
  info "Already existed; redirect address and roles brought up to date."
fi
info "Roles: ${APP_ROLE_VALUES[*]}"

step "2. Enterprise application: only people with a role can sign in"
SP_ID="$(az ad sp show --id "$CLIENT_ID" --query id -o tsv 2> /dev/null || true)"
if [ -z "$SP_ID" ]; then
  for attempt in 1 2 3 4 5 6; do
    SP_ID="$(directory_call "create the enterprise application" "$APP_ADMIN" \
      az ad sp create --id "$CLIENT_ID" --query id -o tsv --only-show-errors || true)"
    stop_if_denied
    if [ -n "$SP_ID" ]; then break; fi
    if [ "$attempt" = "6" ]; then fail_with_last_error "The enterprise application could not be created."; fi
    sleep 10 # a new app registration takes a moment to be usable
  done
  info "Created."
else
  info "Already exists."
fi
directory_call "require a role to sign in to the application" "$APP_ADMIN" \
  az ad sp update --id "$SP_ID" --set appRoleAssignmentRequired=true --only-show-errors --output none \
  || fail_with_last_error "'Assignment required' could not be switched on."
info "Assignment required: on."

# ---------------------------------------------------------------- users

# Letters and digits only, without look-alikes (0/O, 1/l/I): easy to type and to read out,
# and nothing a shell could misread. 20 characters with upper case, lower case and a digit.
new_password() {
  local candidate
  while :; do
    candidate="$(openssl rand -base64 64 | tr -dc 'A-HJ-NP-Za-km-z2-9' | cut -c1-20)"
    if [ "${#candidate}" -eq 20 ] && [[ "$candidate" =~ [A-Z] ]] && [[ "$candidate" =~ [a-z] ]] && [[ "$candidate" =~ [0-9] ]]; then
      printf '%s' "$candidate"
      return
    fi
  done
}

step "3. Test users, one per role"
CREDENTIALS=()
USER_IDS=()
for i in 0 1 2; do
  upn="$DEMO_USER_PREFIX-${DEMO_USER_SUFFIXES[$i]}@$DOMAIN"
  user_id="$(az ad user show --id "$upn" --query id -o tsv 2> /dev/null || true)"
  if [ -z "$user_id" ]; then
    password="$(new_password)"
    user_id="$(directory_call "create the test user $upn" "$USER_ADMIN" \
      az ad user create --display-name "Demo ${APP_ROLE_NAMES[$i]}" --user-principal-name "$upn" \
      --password "$password" --force-change-password-next-sign-in false --query id -o tsv --only-show-errors)" \
      || fail_with_last_error "The test user $upn could not be created."
    [ -n "$user_id" ] || die "The user $upn was created but its id was not returned. Run the script again with --reset-passwords."
    CREDENTIALS+=("$(printf '%-12s %-44s %s' "${APP_ROLE_VALUES[$i]}" "$upn" "$password")")
    info "Created: $upn"
  elif [ "$RESET_PASSWORDS" = "1" ]; then
    password="$(new_password)"
    directory_call "set a new password for $upn" "$USER_ADMIN" \
      az ad user update --id "$user_id" --password "$password" --force-change-password-next-sign-in false \
      --only-show-errors --output none \
      || fail_with_last_error "The password of $upn could not be changed."
    CREDENTIALS+=("$(printf '%-12s %-44s %s' "${APP_ROLE_VALUES[$i]}" "$upn" "$password")")
    info "Already existed; new password set: $upn"
  else
    CREDENTIALS+=("$(printf '%-12s %-44s %s' "${APP_ROLE_VALUES[$i]}" "$upn" "(unchanged; --reset-passwords gives a new one)")")
    info "Already exists, password unchanged: $upn"
  fi
  unset password
  USER_IDS+=("$user_id")
done

step "4. One role per user"
ASSIGNED="$(az rest --method get --url "$GRAPH/servicePrincipals/$SP_ID/appRoleAssignedTo" \
  --query "value[].[principalId, appRoleId]" -o tsv 2> /dev/null || true)"
for i in 0 1 2; do
  if printf '%s\n' "$ASSIGNED" | grep -q "${USER_IDS[$i]}[[:space:]]*${APP_ROLE_IDS[$i]}"; then
    info "Already assigned: ${APP_ROLE_VALUES[$i]}"
    continue
  fi
  BODY="$(temp_file)"
  printf '{"principalId": "%s", "resourceId": "%s", "appRoleId": "%s"}\n' "${USER_IDS[$i]}" "$SP_ID" "${APP_ROLE_IDS[$i]}" > "$BODY"
  for attempt in 1 2 3 4 5 6; do
    if directory_call "assign the role ${APP_ROLE_VALUES[$i]} to its test user" "$APP_ADMIN, or $USER_ADMIN" \
      az rest --method post --url "$GRAPH/servicePrincipals/$SP_ID/appRoleAssignedTo" \
      --body "@$(native_path "$BODY")" --headers "Content-Type=application/json" \
      --only-show-errors --output none; then
      info "Assigned: ${APP_ROLE_VALUES[$i]}"
      break
    fi
    stop_if_denied
    # The list above could not be read, and the role was in fact already assigned.
    if grep -qi "already exists" "$LAST_ERROR"; then
      info "Already assigned: ${APP_ROLE_VALUES[$i]}"
      break
    fi
    if [ "$attempt" = "6" ]; then fail_with_last_error "The role ${APP_ROLE_VALUES[$i]} could not be assigned. Nothing is duplicated if you run the script again."; fi
    sleep 10 # a new user takes a moment to be assignable
  done
done

# ---------------------------------------------------------------- summary

step "Done"
info "Tenant id:   $TENANT_ID"
info "Client id:   $CLIENT_ID   (not a secret; the deploy script reads it again by itself)"
info "No client secret was created: sign-in uses the ID token only."
printf '\n   Test users. Shown once and stored nowhere: copy them now, outside the repository.\n\n'
printf '   %-12s %-44s %s\n' "ROLE" "USER" "PASSWORD"
for line in "${CREDENTIALS[@]}"; do printf '   %s\n' "$line"; done
printf '\n'
info "The directory's security defaults stay on: each user may be asked to set up MFA at"
info "the first sign-in. Sign in once with each of them before the demo."
info "These objects are outside the resource group. teardown.sh --entra removes them."
