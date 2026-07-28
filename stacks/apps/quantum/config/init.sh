#!/bin/sh
set -e

sed -e "s/\${DOMAIN_PUBLIC}/${DOMAIN_PUBLIC}/g" \
    -e "s/\${DOMAIN_PRIVATE}/${DOMAIN_PRIVATE}/g" /tmp/config.yaml.tpl > "${FILEBROWSER_CONFIG}"

exec ./filebrowser "$@"
