#!/bin/sh
# Ensure that webapp.baseurl in ~/.intermine/<mine>.properties is the PUBLIC
# origin of the site, taken from $WEBAPP_BASEURL (PUBLIC_URL in .env).
#
# Why: InterMine's URLGenerator (used by /service/query/code, i.e. the
# "Generate code" dialog, and by permalinks, e-mails, OAuth2 ...) uses
# webapp.baseurl verbatim whenever it is set.  If it is left at the
# Docker-internal value (the same value as webapp.deploy.url, e.g.
# http://tomcat:8080 or http://localhost:8080) every generated script points at
# a host that only exists inside the Docker network.  webapp.deploy.url must
# keep the internal value: it is the Tomcat manager URL Gradle deploys to.
#
# The change only takes effect after the webapp is rebuilt and redeployed
# (./gradlew cargoRedeploy), because the properties are copied into the WAR.
#
# Exit codes: 0 ok (changed or already correct), 1 bad input, 2 no properties file.
set -eu

mine="${MINE_NAME:?MINE_NAME is not set}"
props="${HOME:-/home/intermine}/.intermine/${mine}.properties"
url="${WEBAPP_BASEURL:-}"

if [ -z "$url" ]; then
    echo "set-webapp-baseurl: WEBAPP_BASEURL is empty; set PUBLIC_URL in .env" >&2
    exit 1
fi

# Strip one trailing slash, then insist on scheme://host[:port] and nothing
# else: InterMine itself appends "/" + webapp.path (+ "/service").
url="${url%/}"
case "$url" in
    http://*|https://*) ;;
    *) echo "set-webapp-baseurl: '$url' must start with http:// or https://" >&2; exit 1 ;;
esac
hostpart="${url#*://}"
case "$hostpart" in
    ""|*/*|*[!A-Za-z0-9.:-]*)
        echo "set-webapp-baseurl: '$url' must be scheme://host[:port] with no path" >&2
        exit 1 ;;
esac

if [ ! -f "$props" ]; then
    echo "set-webapp-baseurl: $props does not exist yet; nothing to do" >&2
    exit 2
fi

current="$(sed -n 's/^[[:space:]]*webapp\.baseurl[[:space:]]*=[[:space:]]*//p' "$props" | tail -n 1)"
if [ "$current" = "$url" ]; then
    echo "set-webapp-baseurl: webapp.baseurl is already $url in $props"
    exit 0
fi

if grep -q '^[[:space:]]*webapp\.baseurl[[:space:]]*=' "$props"; then
    sed -i "s|^[[:space:]]*webapp\.baseurl[[:space:]]*=.*|webapp.baseurl=$url|" "$props"
else
    printf '\n# Public URL of the site (scheme + host). Set from PUBLIC_URL in .env.\nwebapp.baseurl=%s\n' "$url" >> "$props"
fi
echo "set-webapp-baseurl: webapp.baseurl changed from '${current:-<unset>}' to '$url' in $props"
echo "set-webapp-baseurl: redeploy the webapp (./gradlew cargoRedeploy) for this to take effect"
