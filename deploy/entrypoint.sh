#!/bin/sh
set -eu
if [ "${DEPLOY_SCHEME:-https}" = http ]; then
    echo 'WARNING: HTTP test mode. Logins and sessions are not encrypted; use only a trusted network.' >&2
fi
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec python deploy/serve.py
