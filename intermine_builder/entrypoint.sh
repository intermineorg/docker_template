#!/bin/sh
# Entrypoint of the InterMine builder image.
#
# Every time the container starts, keep the mine's webapp.baseurl in sync with
# PUBLIC_URL from .env (passed in by docker-compose as WEBAPP_BASEURL), then run
# the requested command (by default an interactive shell).  This makes it
# impossible for a freshly copied <mine>.properties (which ships with
# webapp.baseurl=http://localhost:8080) to reach the public site unnoticed.

# When the command *is* set-webapp-baseurl (make baseurl) do not run it twice.
case "${1:-}" in
    set-webapp-baseurl|*/set-webapp-baseurl) exec "$@" ;;
esac

if [ -n "${WEBAPP_BASEURL:-}" ]; then
    set-webapp-baseurl || true   # it has already explained itself; never block the shell
else
    echo "entrypoint: WEBAPP_BASEURL (PUBLIC_URL in .env) is not set; webapp.baseurl left unchanged" >&2
fi

exec "$@"
