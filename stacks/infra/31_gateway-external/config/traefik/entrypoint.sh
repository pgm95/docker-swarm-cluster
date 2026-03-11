#!/bin/sh
set -e

GEOBLOCK_DIR="/data/geoblock"
GEOBLOCK_DB="${GEOBLOCK_DIR}/IP2LOCATION-LITE-DB1.IPV6.BIN"
DOWNLOAD_URL="https://www.ip2location.com/download/?token=${GEOBLOCK_IP2LOCATION_TOKEN}&file=DB1LITEBINIPV6"

if [ -f "$GEOBLOCK_DB" ]; then
    echo "Geoblock database exists: $(ls -lh "$GEOBLOCK_DB" | awk '{print $5}')"
else
    echo "Geoblock database not found at ${GEOBLOCK_DB}, bootstrapping..."

    if [ -z "$GEOBLOCK_IP2LOCATION_TOKEN" ]; then
        echo "Warning: GEOBLOCK_IP2LOCATION_TOKEN not set, skipping download"
    else
        apk add --no-cache unzip >/dev/null 2>&1 || true
        TMPFILE=$(mktemp)
        if wget -q -O "$TMPFILE" "$DOWNLOAD_URL"; then
            if unzip -o -j "$TMPFILE" "*.BIN" -d "$GEOBLOCK_DIR" >/dev/null 2>&1; then
                echo "Geoblock database installed: $(ls -lh "$GEOBLOCK_DB" | awk '{print $5}')"
            else
                echo "Warning: failed to extract geoblock database from ZIP"
            fi
        else
            echo "Warning: failed to download geoblock database"
        fi
        rm -f "$TMPFILE"
    fi
fi

exec /entrypoint.sh "$@"
