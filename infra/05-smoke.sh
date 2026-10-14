#!/usr/bin/env bash
# Step 5. Checks the deployment from the outside, as someone on the internet who has not
# signed in. READ-ONLY: it changes nothing. It does not open a browser.
#
#   bash infra/05-smoke.sh
#
#   1. The public address does not serve the app without a sign-in: it answers 401 or
#      redirects to Microsoft's sign-in page.
#   2. The backend does not answer from the internet, by either of its names.
#   3. A forged identity gets nowhere: /api/me with a made-up X-MS-CLIENT-PRINCIPAL (and
#      with the development headers) is refused like any anonymous request.
#
# What it cannot check is that the three roles work after a real sign-in: that needs a
# browser. The steps are printed at the end.

# shellcheck source=infra/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_tools
command -v curl > /dev/null 2>&1 || die "curl is not on the PATH."
load_subscription
app_exists "$WEB_APP" || die "The frontend $WEB_APP does not exist. Run 04-deploy.sh first."
app_exists "$API_APP" || die "The backend $API_APP does not exist. Run 04-deploy.sh first."

WEB_FQDN="$(app_fqdn "$WEB_APP")"
API_FQDN="$(app_fqdn "$API_APP")"
ENV_DOMAIN="$(az containerapp env show --name "$CONTAINER_ENV" --resource-group "$RESOURCE_GROUP" \
  --query properties.defaultDomain -o tsv)"
WEB="https://$WEB_FQDN"

FAILED=0
pass() { printf '   PASS  %s\n' "$*"; }
fail() {
  FAILED=$((FAILED + 1))
  printf '   FAIL  %s\n' "$*" >&2
}

# request <url> [curl options...] -> "<status> <redirect address>"; "000" when there is no answer.
# The first request after a pause wakes the app up, which can take a while.
request() {
  local url="$1"
  shift
  curl --silent --output /dev/null --max-time 90 --write-out '%{http_code} %{redirect_url}' "$@" "$url" 2> /dev/null || true
}

# An anonymous request is refused when it gets 401/403, or a redirect to a sign-in page.
refused() { # refused "<status> <redirect>"
  local status="${1%% *}" target="${1#* }"
  case "$status" in
    401 | 403) return 0 ;;
    301 | 302 | 303 | 307)
      case "$target" in *login.microsoftonline.com* | */.auth/login/*) return 0 ;; esac
      ;;
  esac
  return 1
}

step "1. The public address asks for a sign-in"
info "$WEB"
for path in "/" "/invoices" "/review" "/parameters" "/api/me" "/api/invoices"; do
  answer="$(request "$WEB$path")"
  if refused "$answer"; then pass "GET $path -> ${answer%% *} (sign-in required)"; else fail "GET $path -> $answer   (expected 401 or a redirect to the sign-in page)"; fi
done
answer="$(request "$WEB/api/invoices" --request POST --form "file=@$(native_path "$INFRA_DIR/lib.sh")")"
if refused "$answer"; then pass "POST /api/invoices -> ${answer%% *} (an upload without a sign-in is refused)"; else fail "POST /api/invoices -> $answer"; fi

step "2. The backend does not answer from the internet"
info "Internal address: $API_FQDN"
INGRESS="$(az containerapp show --name "$API_APP" --resource-group "$RESOURCE_GROUP" --query properties.configuration.ingress.external -o tsv)"
if [ "$INGRESS" = "false" ]; then pass "Its ingress is internal (external=false)."; else fail "Its ingress is external=$INGRESS."; fi
for host in "$API_FQDN" "$API_APP.$ENV_DOMAIN"; do
  for path in "/health" "/api/me"; do
    answer="$(curl --silent --output /dev/null --max-time 15 --write-out '%{http_code}' "https://$host$path" 2> /dev/null || true)"
    case "${answer:-000}" in
      000) pass "https://$host$path -> no answer" ;;
      404 | 403) pass "https://$host$path -> $answer from Azure's edge, not from the application" ;;
      *) fail "https://$host$path -> $answer   (the backend must not be reachable)" ;;
    esac
  done
done

step "3. A forged identity is refused"
FORGED="$(printf '{"auth_typ":"aad","claims":[{"typ":"preferred_username","val":"mallory@example.com"},{"typ":"roles","val":"AP_MANAGER"}]}' | base64 | tr -d '\r\n')"
answer="$(request "$WEB/api/me" --header "X-MS-CLIENT-PRINCIPAL: $FORGED" --header "X-MS-CLIENT-PRINCIPAL-NAME: mallory@example.com")"
if refused "$answer"; then pass "/api/me with a made-up X-MS-CLIENT-PRINCIPAL -> ${answer%% *}"; else fail "/api/me with a made-up X-MS-CLIENT-PRINCIPAL -> $answer   (it must not be 200)"; fi
answer="$(request "$WEB/api/me" --header "X-Dev-User: mallory" --header "X-Dev-Role: AP_MANAGER")"
if refused "$answer"; then pass "/api/me with the development headers -> ${answer%% *}"; else fail "/api/me with the development headers -> $answer   (it must not be 200)"; fi
answer="$(request "$WEB/api/parameters" --header "X-MS-CLIENT-PRINCIPAL: $FORGED" --header "X-Dev-User: mallory" --header "X-Dev-Role: AP_MANAGER")"
if refused "$answer"; then pass "/api/parameters with both -> ${answer%% *}"; else fail "/api/parameters with both -> $answer"; fi

step "Result"
if [ "$FAILED" -gt 0 ]; then
  die "$FAILED check(s) failed. Do not share the address until they pass."
fi
info "All checks passed. Nothing was changed."
cat << EOF

   What only a browser can confirm. Use a private window for each user, so one session
   does not carry over to the next. Address: $WEB

   With each of the three test users (${DEMO_USER_PREFIX}-intake, -analyst, -manager):
     a. Open the address. You are sent to Microsoft's sign-in page, not to the app.
     b. Sign in. The first time you may be asked to set up MFA: finish it.
     c. You land in the app. The top bar shows the user's sign-in name and the right role,
        and there is NO "Development login" notice.

   INTAKE:      sees only "Invoices". Upload a test invoice; it appears as "Processing" and
                then gets its status. Opening $WEB/review or /parameters says the
                page is not for this role.
   AP_ANALYST:  sees "Review queue", "Invoices" and "Parameters" (read-only, no form).
                Opens a held invoice: the document shows, decisions are offered. The
                "Audit trail" card is not there.
   AP_MANAGER:  the same, plus the "Audit trail" on an invoice and the form to change the
                parameters. Save a change: the history names this user.

   Then:
     d. Sign out (top right). Opening the address again asks for a sign-in.
     e. Sign in with an account of the directory that has NO role in the application
        (your own, if you did not assign yourself one): Microsoft refuses it, because the
        application requires an assigned role.
     f. Signed in as INTAKE, open the browser's developer tools and run:
          fetch("/api/parameters", {headers: {"X-MS-CLIENT-PRINCIPAL": "$FORGED"}}).then(r => r.status)
        It must answer 403: Azure replaces the header with the real identity, so the
        forged manager is ignored. If it answers 200, stop and do not share the address.
EOF
