#!/usr/bin/env bash
set -euo pipefail

# Starts Docker services required for WebArena.
# Separate from data download - this handles runtime infrastructure.
#
# Requires: docker, curl
# Env vars: DATA_ROOT (for image tarballs), WEBARENA_HOST (defaults to 127.0.0.1)

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DATA_ROOT=${DATA_ROOT:-$ROOT/data}
WEBARENA_HOST=${WEBARENA_HOST:-127.0.0.1}
WEBARENA_IMAGES_DIR="$DATA_ROOT/webarena-images"
SKIP_WEBARENA_SERVICES=${SKIP_WEBARENA_SERVICES:-0}

log() { echo "[services] $*"; }

# WebArena Docker services
if [ "$SKIP_WEBARENA_SERVICES" = "1" ]; then
  log "Skipping WebArena services (SKIP_WEBARENA_SERVICES=1)"
  exit 0
fi

# Check if Docker is available
if ! command -v docker &>/dev/null; then
  log "Docker not found, skipping WebArena services"
  exit 0
fi

mkdir -p "$WEBARENA_IMAGES_DIR"

# Image definitions: name, port, image_name, tarball_url
declare -A IMAGES=(
  ["shopping"]="7770|shopping_final_0712|http://metis.lti.cs.cmu.edu/webarena-images/shopping_final_0712.tar"
  ["shopping_admin"]="7780|shopping_admin_final_0719|http://metis.lti.cs.cmu.edu/webarena-images/shopping_admin_final_0719.tar"
  ["forum"]="9999|postmill-populated-exposed-withimg|http://metis.lti.cs.cmu.edu/webarena-images/postmill-populated-exposed-withimg.tar"
  ["gitlab"]="8023|gitlab-populated-final-port8023|http://metis.lti.cs.cmu.edu/webarena-images/gitlab-populated-final-port8023.tar"
)

# Wikipedia uses a different setup (kiwix with zim file)
WIKI_ZIM_URL="http://metis.lti.cs.cmu.edu/webarena-images/wikipedia_en_all_maxi_2022-05.zim"
WIKI_ZIM_FILE="$WEBARENA_IMAGES_DIR/wikipedia_en_all_maxi_2022-05.zim"

download_and_load_image() {
  local name=$1
  local image_name=$2
  local url=$3
  local tarball="$WEBARENA_IMAGES_DIR/${image_name}.tar"

  # Check if image already loaded
  if docker images --format '{{.Repository}}' | grep -q "^${image_name}$"; then
    log "Image $image_name already loaded"
    return 0
  fi

  # Download if not present
  if [ ! -f "$tarball" ]; then
    log "Downloading $name image..."
    curl -fSL "$url" -o "$tarball"
  fi

  # Load image
  log "Loading $name image..."
  docker load --input "$tarball"
}

start_container() {
  local name=$1
  local port=$2
  local image_name=$3
  local extra_args=${4:-}

  # Check if already running
  if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
    log "Container $name already running"
    return 0
  fi

  # Remove if exists but stopped
  if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
    log "Removing stopped container $name"
    docker rm "$name"
  fi

  log "Starting $name on port $port..."
  if [ -n "$extra_args" ]; then
    # extra_args is the command to run (comes after image name)
    docker run --name "$name" -p "${port}:${port}" -d "$image_name" $extra_args
  else
    # Most containers map to port 80 internally
    docker run --name "$name" -p "${port}:80" -d "$image_name"
  fi
}

configure_shopping() {
  local host=$1
  log "Configuring shopping for host $host..."
  docker exec shopping /var/www/magento2/bin/magento setup:store-config:set --base-url="http://${host}:7770" || true
  docker exec shopping mysql -u magentouser -pMyPassword magentodb -e "UPDATE core_config_data SET value='http://${host}:7770/' WHERE path = 'web/secure/base_url';" || true
  docker exec shopping /var/www/magento2/bin/magento cache:flush || true
}

configure_shopping_admin() {
  local host=$1
  log "Configuring shopping_admin for host $host..."
  docker exec shopping_admin /var/www/magento2/bin/magento setup:store-config:set --base-url="http://${host}:7780" || true
  docker exec shopping_admin mysql -u magentouser -pMyPassword magentodb -e "UPDATE core_config_data SET value='http://${host}:7780/' WHERE path = 'web/secure/base_url';" || true
  docker exec shopping_admin php /var/www/magento2/bin/magento config:set admin/security/password_is_forced 0 || true
  docker exec shopping_admin php /var/www/magento2/bin/magento config:set admin/security/password_lifetime 0 || true
  docker exec shopping_admin /var/www/magento2/bin/magento cache:flush || true
}

configure_gitlab() {
  local host=$1
  log "Configuring gitlab for host $host..."
  docker exec gitlab sed -i "s|^external_url.*|external_url 'http://${host}:8023'|" /etc/gitlab/gitlab.rb || true
  docker exec gitlab gitlab-ctl reconfigure || true
}

wait_for_service() {
  local name=$1
  local url=$2
  local max_attempts=${3:-60}
  local attempt=0

  log "Waiting for $name at $url..."
  while [ $attempt -lt $max_attempts ]; do
    if curl -sf -o /dev/null "$url" 2>/dev/null; then
      log "$name is ready"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  log "WARNING: $name did not become ready after $max_attempts attempts"
  return 1
}

# Download and load all images
for name in "${!IMAGES[@]}"; do
  IFS='|' read -r port image_name url <<< "${IMAGES[$name]}"
  download_and_load_image "$name" "$image_name" "$url"
done

# Wikipedia zim file
if [ ! -f "$WIKI_ZIM_FILE" ]; then
  log "Downloading Wikipedia zim file (this is large, ~90GB)..."
  curl -fSL "$WIKI_ZIM_URL" -o "$WIKI_ZIM_FILE"
fi

# Start containers
start_container "shopping" "7770" "shopping_final_0712"
start_container "shopping_admin" "7780" "shopping_admin_final_0719"
start_container "forum" "9999" "postmill-populated-exposed-withimg"
start_container "gitlab" "8023" "gitlab-populated-final-port8023" "/opt/gitlab/embedded/bin/runsvdir-start"

# Wikipedia (kiwix)
if ! docker ps --format '{{.Names}}' | grep -q "^wikipedia$"; then
  if docker ps -a --format '{{.Names}}' | grep -q "^wikipedia$"; then
    docker rm wikipedia
  fi
  log "Starting wikipedia on port 8888..."
  docker run -d --name=wikipedia --volume="${WEBARENA_IMAGES_DIR}:/data" -p 8888:80 ghcr.io/kiwix/kiwix-serve:3.3.0 wikipedia_en_all_maxi_2022-05.zim
fi

# Wait for services to start (they need time to boot)
log "Waiting for services to initialize (60s baseline for gitlab)..."
sleep 60

# Configure services with hostname
configure_shopping "$WEBARENA_HOST"
configure_shopping_admin "$WEBARENA_HOST"
configure_gitlab "$WEBARENA_HOST"

# Health checks
log "Running health checks..."
FAILED=()
wait_for_service "shopping" "http://${WEBARENA_HOST}:7770" 30 || FAILED+=("shopping")
wait_for_service "shopping_admin" "http://${WEBARENA_HOST}:7780/admin" 30 || FAILED+=("shopping_admin")
wait_for_service "forum" "http://${WEBARENA_HOST}:9999" 30 || FAILED+=("forum")
wait_for_service "gitlab" "http://${WEBARENA_HOST}:8023" 60 || FAILED+=("gitlab")
wait_for_service "wikipedia" "http://${WEBARENA_HOST}:8888" 30 || FAILED+=("wikipedia")

if [ ${#FAILED[@]} -gt 0 ]; then
  log "WARNING: Some services failed health checks: ${FAILED[*]}"
  log "Continuing anyway - services may still be starting"
fi

log "WebArena services started"
