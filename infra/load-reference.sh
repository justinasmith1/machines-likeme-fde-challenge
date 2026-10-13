#!/usr/bin/env bash
# Loads the reference data (suppliers, purchase orders, goods received notes) into the
# deployed system. The files are not in the repository; you give the folder they are in.
#
#   bash infra/load-reference.sh [folder]        default: data/reference
#
# Options:
#   --keep-warm   Leave the backend running afterwards instead of letting it scale to zero.
#   --yes         Do not ask for confirmation.
#
# What it does:
#   1. Copies suppliers.json, purchase_orders.json and goods_received_notes.json to the
#      file share (folder "reference"), replacing the ones there.
#   2. Makes sure the backend is running (it scales to zero when idle).
#   3. Runs, INSIDE the backend's container, the same command used locally:
#        python -m app.cli load-reference --dir /app/data/reference
#      It runs there because the database is a SQLite file that only that container
#      should write to.
#
# Safe to run again: the load validates the files and replaces the reference data in one
# transaction, so loading the same files twice leaves the same result. Invoices, decisions
# and the audit trail are not touched.
#
# "az containerapp exec" needs an interactive terminal. If it fails in Git Bash, the
# script prints the same command for PowerShell and for the portal's console.

# shellcheck source=infra/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SOURCE=""
KEEP_WARM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep-warm) KEEP_WARM=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -*) die "Unknown option: $1" ;;
    *) SOURCE="$1"; shift ;;
  esac
done
REPO_ROOT="$(cd "$INFRA_DIR/.." && pwd)"
SOURCE="${SOURCE:-$REPO_ROOT/data/reference}"
FILES=(suppliers.json purchase_orders.json goods_received_notes.json)
REMOTE_COMMAND="python -m app.cli load-reference --dir $DATA_MOUNT_PATH/reference"

require_tools
load_subscription
[ -d "$SOURCE" ] || die "Not a folder: $SOURCE"
SOURCE="$(cd "$SOURCE" && pwd)"
for file in "${FILES[@]}"; do
  [ -f "$SOURCE/$file" ] || die "Missing $file in $SOURCE. The three files are needed."
done
app_exists "$API_APP" || die "The backend $API_APP does not exist. Run 04-deploy.sh first."

step "Reference data"
info "From:  $SOURCE"
info "To:    file share $FILE_SHARE/reference, then loaded by the backend $API_APP"
confirm "Replace the reference data of the deployed system with these files?" || die "Cancelled. Nothing was changed."

step "1. Copy the files to the share"
STORAGE_KEY="$(az storage account keys list --account-name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
  --query "[0].value" -o tsv)"
az storage directory create --name reference --share-name "$FILE_SHARE" --account-name "$STORAGE_ACCOUNT" \
  --account-key "$STORAGE_KEY" --only-show-errors --output none
for file in "${FILES[@]}"; do
  az storage file upload --share-name "$FILE_SHARE" --path "reference/$file" --source "$(native_path "$SOURCE/$file")" \
    --account-name "$STORAGE_ACCOUNT" --account-key "$STORAGE_KEY" --no-progress --only-show-errors --output none
  info "Copied $file"
done
unset STORAGE_KEY

# ---------------------------------------------------------------- a running replica

replicas() {
  az containerapp replica list --name "$API_APP" --resource-group "$RESOURCE_GROUP" --query "length(@)" -o tsv 2> /dev/null || printf '0'
}
set_min_replicas() { # set_min_replicas <n>: only the scale changes; the rest of the app is untouched
  local body
  body="$(temp_file)"
  printf '{"properties": {"template": {"scale": {"minReplicas": %s, "maxReplicas": 1}}}}\n' "$1" > "$body"
  az rest --method patch --url "$(app_url "$API_APP")" --body "@$(native_path "$body")" \
    --headers "Content-Type=application/json" --only-show-errors --output none
}

step "2. Make sure the backend is running"
WOKEN=0
if [ "$(replicas)" = "0" ] || [ -z "$(replicas)" ]; then
  info "It is scaled to zero. Starting one replica (covered by the monthly free grant)..."
  set_min_replicas 1
  WOKEN=1
  for attempt in $(seq 1 60); do
    if [ "$(replicas)" != "0" ] && [ -n "$(replicas)" ]; then break; fi
    if [ "$attempt" = "60" ]; then die "The backend did not start within 5 minutes. See: az containerapp logs show --name $API_APP --resource-group $RESOURCE_GROUP --tail 50"; fi
    sleep 5
  done
  sleep 10 # the container needs a moment after the replica appears
fi
info "Running."

restore_scale() {
  if [ "$WOKEN" = "1" ] && [ "$KEEP_WARM" = "0" ]; then
    set_min_replicas 0
    info "The backend may scale to zero again when idle."
  elif [ "$WOKEN" = "1" ]; then
    warn "--keep-warm: the backend keeps one replica running. To undo it, run this script again without the option, or 04-deploy.sh."
  fi
}

step "3. Load, inside the backend's container"
info "$REMOTE_COMMAND"
printf '\n'
# Not through the az() wrapper of lib.sh: this command talks to the terminal directly.
if command az containerapp exec --name "$API_APP" --resource-group "$RESOURCE_GROUP" --container "$API_CONTAINER" \
  --command "$REMOTE_COMMAND"; then
  printf '\n'
  restore_scale
  step "Done"
  info "Read the lines above: the loader prints the number of records per file and 'Errors: none'"
  info "when everything was accepted. If it printed errors, nothing was replaced: fix the files and run again."
else
  printf '\n'
  restore_scale
  die "The command could not be run from this terminal (Git Bash is often not accepted as interactive).
The files ARE already on the share. Run the load in one of these ways; the result is the same:

  PowerShell:
    az containerapp exec --name $API_APP --resource-group $RESOURCE_GROUP --container $API_CONTAINER --command \"$REMOTE_COMMAND\"

  Portal: Container Apps > $API_APP > Monitoring > Console > /bin/sh, then type:
    $REMOTE_COMMAND

If the backend has scaled to zero by then, open the application in the browser first, or
run this script again with --keep-warm."
fi
