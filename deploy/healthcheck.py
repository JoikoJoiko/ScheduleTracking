"""Use the real host and HTTPS proxy headers to check production readiness."""
import json
import os
from urllib.request import Request, urlopen

request=Request('http://127.0.0.1:8000/healthz',headers={
    'Host':os.environ['ALLOWED_HOSTS'].split(',')[0], 'X-Forwarded-Proto':'https'})
with urlopen(request,timeout=3) as response:
    if response.status!=200 or json.load(response).get('status')!='ok':
        raise SystemExit(1)
