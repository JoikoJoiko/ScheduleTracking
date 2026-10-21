"""Check readiness using the same host and transport as the public site."""
import json
import os
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

def probe_headers(environ=None):
    env=os.environ if environ is None else environ
    host=urlsplit(env.get('PUBLIC_ORIGIN','')).netloc or env['ALLOWED_HOSTS'].split(',')[0]
    return {'Host':host,'X-Forwarded-Proto':env.get('DEPLOY_SCHEME','https')}


if __name__=='__main__':
    request=Request('http://127.0.0.1:8000/healthz',headers=probe_headers())
    with urlopen(request,timeout=3) as response:
        if response.status!=200 or json.load(response).get('status')!='ok':
            raise SystemExit(1)
