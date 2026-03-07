#!/bin/sh
set -e

echo "Starting carpal configuration setup..."

# Check required environment variables
check_vars() {
    missing=""
    for var in LDAP_ADDRESS LDAP_BIND_USER LDAP_BIND_PASS LDAP_BASE_DN OIDC_URL; do
        eval "val=\$$var"
        if [ -z "$val" ]; then
            missing="$missing $var"
        fi
    done

    if [ -n "$missing" ]; then
        echo "Error: Missing required environment variables:"
        echo "$missing"
        echo "Please set these variables via --env-file or -e flags"
        exit 1
    fi
}

check_vars

# Ensure target directory exists
mkdir -p /etc/carpal

# Process config.yml - replace ${VAR} patterns using sed
if [ -f "/config/config.yml" ]; then
    echo "Processing config.yml..."

    # Escape sed metacharacters (&, |, \) in values to prevent silent corruption.
    # Without this, a password like "p&ss" would expand & to the matched text.
    escape_sed() { printf '%s' "$1" | sed 's/[&|\]/\\&/g'; }

    sed -e "s|\${LDAP_BIND_USER}|$(escape_sed "$LDAP_BIND_USER")|g" \
        -e "s|\${LDAP_BIND_PASS}|$(escape_sed "$LDAP_BIND_PASS")|g" \
        -e "s|\${LDAP_BASE_DN}|$(escape_sed "$LDAP_BASE_DN")|g" \
        -e "s|\${LDAP_ADDRESS}|$(escape_sed "$LDAP_ADDRESS")|g" \
        /config/config.yml > /etc/carpal/config.yml

    echo "✓ config.yml processed"
else
    echo "Error: /config/config.yml not found"
    exit 1
fi

# Process ldap.gotmpl - replace {{ env "VAR" }} patterns
if [ -f "/config/ldap.gotmpl" ]; then
    echo "Processing ldap.gotmpl..."

    # Replace {{ env "OIDC_URL" }} with actual values, keeping quotes
    sed -e "s|{{ env \"OIDC_URL\" }}|$(escape_sed "$OIDC_URL")|g" \
        /config/ldap.gotmpl > /etc/carpal/ldap.gotmpl

    echo "✓ ldap.gotmpl processed"
else
    echo "Error: /config/ldap.gotmpl not found"
    exit 1
fi

# Verify the processed files exist
echo ""
echo "Configuration files ready:"
echo "  - /etc/carpal/config.yml"
echo "  - /etc/carpal/ldap.gotmpl"

echo ""
echo "Configuration summary:"
echo "  LDAP Address: $LDAP_ADDRESS"
echo "  LDAP Base DN: $LDAP_BASE_DN"
echo "  OIDC URL: $OIDC_URL"
echo "  Bind user: cn=$LDAP_BIND_USER,ou=people,$LDAP_BASE_DN"

echo ""
echo "Starting carpal..."

# Execute the main application or any command passed to the container
exec "$@"
