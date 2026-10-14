#!/usr/bin/env bash
# Step 3. Builds the two container images on this machine and pushes them to the registry.
#
#   bash infra/03-build-push.sh
#
# Options:
#   --allow-dirty   Build even with uncommitted changes (the tag then says "-dirty").
#   --force         Push even if the registry already has this tag.
#   --yes           Do not ask for confirmation.
#
# The tag is the short hash of the Git commit, so an image can always be traced to the
# code it was built from. Images are built with Docker here because "az acr build" is not
# available to free accounts.
#
# Before pushing, each image is checked: no .env file, no database, no data and no tests
# inside. If anything is found, nothing is pushed.
#
# Safe to run again: Docker reuses what it already built, and a tag that is already in
# the registry is not pushed again.

# shellcheck source=infra/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ALLOW_DIRTY=0
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --force) FORCE=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    *) die "Unknown option: $1" ;;
  esac
done

require_tools
command -v docker > /dev/null 2>&1 || die "Docker is not installed."
succeeds docker info || die "Docker is not running. Start Docker Desktop and run this again."
command -v git > /dev/null 2>&1 || die "git is not on the PATH."
load_subscription

step "Version"
pushd "$INFRA_DIR/.." > /dev/null
TAG="$(git rev-parse --short HEAD | tr -d '\r')"
DIRTY="$(git status --porcelain -- backend frontend)"
popd > /dev/null
if [ -n "$DIRTY" ]; then
  if [ "$ALLOW_DIRTY" = "1" ]; then
    TAG="$TAG-dirty"
    warn "backend/ or frontend/ has uncommitted changes. The images get the tag $TAG."
  else
    die "backend/ or frontend/ has uncommitted changes, so the commit hash would not describe the image.
Commit first, or pass --allow-dirty to build anyway (the tag then ends in -dirty)."
  fi
fi
succeeds az acr show --name "$REGISTRY" --resource-group "$RESOURCE_GROUP" \
  || die "The registry $REGISTRY does not exist. Run 01-foundation.sh first."
SERVER="$REGISTRY.azurecr.io"
API_REF="$SERVER/$API_IMAGE:$TAG"
WEB_REF="$SERVER/$WEB_IMAGE:$TAG"
info "Tag:      $TAG"
info "Backend:  $API_REF"
info "Frontend: $WEB_REF"

step "Build (on this machine)"
# Docker is a Windows program and lib.sh switches off Git Bash's path conversion, so the
# folder is resolved here and handed over as a native path.
docker build --platform linux/amd64 --tag "$API_REF" "$(native_path "$(cd "$INFRA_DIR/../backend" && pwd)")"
docker build --platform linux/amd64 --build-arg NEXT_PUBLIC_AUTH_MODE=entra --tag "$WEB_REF" "$(native_path "$(cd "$INFRA_DIR/../frontend" && pwd)")"

# ---------------------------------------------------------------- what must not be inside

# Runs a search inside an image and prints what it finds, then the line "__checked__".
# Anything printed before that line is a problem; the line itself proves the search ran.
# It never fails by itself: a container that does not start leaves no "__checked__".
inside() { # inside <image> <shell script>
  docker run --rm --platform linux/amd64 --entrypoint sh "$1" -c "$2"$'\n'"echo __checked__" 2>&1 || true
}

step "Check: no secrets, data or tests inside the images"
FOUND_API="$(inside "$API_REF" '
  site=$(python -c "import app, os; print(os.path.dirname(app.__file__))" 2>/dev/null) || true
  [ -n "$site" ] || echo "the installed application package was not found, so it was not searched"
  find /app /home /src "$site" /tmp \( -name ".env" -o -name ".env.*" -o -name "*.db" -o -name "*.sqlite" \) 2>/dev/null || true
  find /app/data -mindepth 1 2>/dev/null || true
  find "$site" -type d \( -name tests -o -name test-invoices -o -name uploads \) 2>/dev/null || true
  find /app -mindepth 1 -maxdepth 1 -not -name data 2>/dev/null || true
  find / -xdev -type f \( -name "suppliers.json" -o -name "purchase_orders.json" -o -name "goods_received_notes.json" \) 2>/dev/null || true
')"
FOUND_WEB="$(inside "$WEB_REF" '
  find /app -path /app/node_modules -prune -o \( -name ".env" -o -name ".env.*" -o -name "*.db" \) -print 2>/dev/null || true
  find /app -maxdepth 1 \( -name data -o -name tests -o -name "*.test.*" \) 2>/dev/null || true
')"
# An image the search could not run in proves nothing.
grep -q '__checked__' <<< "$FOUND_API" \
  || die "Backend image could not be inspected. Nothing was pushed."
grep -q '__checked__' <<< "$FOUND_WEB" \
  || die "Frontend image could not be inspected. Nothing was pushed."
# Docker's own notices are not findings.
CLEAN_API="$(grep -v '__checked__' <<< "$FOUND_API" | grep -v '^Error\|^Unable\|^WARNING' || true)"
CLEAN_WEB="$(grep -v '__checked__' <<< "$FOUND_WEB" | grep -v '^Error\|^Unable\|^WARNING' || true)"
if [ -n "$CLEAN_API" ] || [ -n "$CLEAN_WEB" ]; then
  [ -n "$CLEAN_API" ] && printf '   In the backend image:\n%s\n' "$CLEAN_API" >&2
  [ -n "$CLEAN_WEB" ] && printf '   In the frontend image:\n%s\n' "$CLEAN_WEB" >&2
  die "The files above must not be in an image. Nothing was pushed. Check the .dockerignore files."
fi
info "Backend image:  nothing found."
info "Frontend image: nothing found."

# ---------------------------------------------------------------- push

has_tag() { # has_tag <repository>
  az acr repository show-tags --name "$REGISTRY" --repository "$1" --query "contains(@, '$TAG')" -o tsv 2> /dev/null | grep -q true
}

step "Push to $SERVER"
confirm "Push the two images with the tag $TAG?" || die "Cancelled. The images are built locally; nothing was pushed."
az acr login --name "$REGISTRY" --only-show-errors
for pair in "$API_IMAGE|$API_REF" "$WEB_IMAGE|$WEB_REF"; do
  repository="${pair%%|*}"
  reference="${pair##*|}"
  if [ "$FORCE" = "0" ] && has_tag "$repository"; then
    info "Already in the registry, not pushed again: $reference"
  else
    docker push "$reference"
  fi
done

step "Done"
info "Tag: $TAG"
info "Next: bash infra/04-deploy.sh $TAG"
