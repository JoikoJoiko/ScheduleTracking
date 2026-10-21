#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec python deploy/serve.py
