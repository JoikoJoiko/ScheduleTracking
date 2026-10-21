"""Keep trusted proxy handling identical in both container deployment variants."""
import os
from pathlib import Path
import sys


def server_options(environ=None):
    env=os.environ if environ is None else environ
    options={'listen':'0.0.0.0:8000','threads':8,'channel_timeout':600,
             'max_request_body_size':12*1024*1024}
    if env.get('TRUST_PROXY')=='1':
        # Container port 8000 is private; only the gateway may reach it.
        options.update(trusted_proxy='*',trusted_proxy_count=1,
                       trusted_proxy_headers={'x-forwarded-for','x-forwarded-proto'})
    return options


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    from waitress import serve
    from college.wsgi import application
    serve(application,**server_options())
