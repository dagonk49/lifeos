#!/bin/sh
# Entry point of the prometheus service (infra/compose.yaml): writes the targets of the public
# probes from LIFEOS_DOMAIN, then starts Prometheus with the given arguments. Prometheus reads
# no environment variable in its configuration; the file lives in the container's /tmp only.
set -eu
: "${LIFEOS_DOMAIN:?LIFEOS_DOMAIN is required}"
case "$LIFEOS_DOMAIN" in
  *[!A-Za-z0-9.-]*) echo "LIFEOS_DOMAIN is not a host name" >&2; exit 1 ;;
esac
mkdir -p /tmp/targets
cat > /tmp/targets/public.yml.tmp <<EOF
# Written by entrypoint.sh from LIFEOS_DOMAIN.
- targets: ["https://$LIFEOS_DOMAIN/healthz"]
  labels: { target: api, module: https_reachable }
- targets: ["https://$LIFEOS_DOMAIN/login"]
  labels: { target: web, module: https_reachable }
- targets: ["https://$LIFEOS_DOMAIN/healthz"]
  labels: { target: api, module: https_verified }
EOF
mv /tmp/targets/public.yml.tmp /tmp/targets/public.yml
exec /bin/prometheus "$@"
