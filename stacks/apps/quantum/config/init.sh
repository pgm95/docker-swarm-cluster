#!/bin/sh
set -e

sed "s/\${DOMAIN_PUBLIC}/${DOMAIN_PUBLIC}/g" /tmp/config.yaml.tpl > "${FILEBROWSER_CONFIG}"

exec ./filebrowser "$@"
