#!/usr/bin/env bash
# Check that the metapaas control plane works on the realm, not only that the
# element is ACTIVE.
#
# Usage: check-metapaas.sh [timeout-seconds]
#
# Run with the CLI pointed at the realm, once metapaas is ACTIVE, as
# exordos_ci's element_realm_test workflow runs its check-script;
# REALM_CORE_URL comes from the environment it leaves.
#
# - An ACTIVE element only means the core finished reconciling it: the control
#   plane is a VM that may still be booting, and the realm's ingress answers
#   5xx for /api/metapaas until it is up.  Any answer the control plane gives
#   itself -- 401 or 403 included, the call is unauthenticated -- means it is.
# - Then list the registered types through the CLI: a login through the core's
#   IAM and a read from the control plane's own database.
set -uo pipefail

timeout="${1:-600}"

: "${REALM_CORE_URL:?REALM_CORE_URL is not set}"

cp_url="${REALM_CORE_URL%/core}/metapaas"
deadline=$((SECONDS + timeout))
while :; do
    code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' \
        "$cp_url/v1/types/")" || code=000
    if [ "$code" != "000" ] && [ "$code" -lt 500 ]; then
        echo "The metapaas control plane answers on $cp_url ($code)"
        break
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "The metapaas control plane never answered on $cp_url" \
             "within ${timeout}s (last: $code)" >&2
        exit 1
    fi
    echo "Waiting for the metapaas control plane... ($code)"
    sleep 5
done

# The realm's core can take longer than the CLI's ten seconds to answer, so
# give the authenticated call a few tries.
for i in 1 2 3 4 5; do
    if types="$(exordos metapaas types list -o json)" \
            && echo "$types" | jq -e 'type == "array"' > /dev/null; then
        echo "The metapaas API lists $(echo "$types" | jq length) type(s)"
        exordos metapaas types list
        exit 0
    fi
    echo "Listing the metapaas types failed, trying again... ($i/5)" >&2
    sleep 10
done

echo "The metapaas API did not list its types" >&2
exit 1
