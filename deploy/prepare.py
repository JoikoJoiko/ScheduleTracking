"""Generate server secrets and host Nginx configuration without changing the system."""
import argparse
import os
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parent.parent


def valid_domain(value):
    try:
        domain = value.strip().rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError as exc:
        raise ValueError('Invalid domain') from exc
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(domain)>253 or not re.fullmatch(rf'{label}(?:\.{label})+', domain):
        raise ValueError('Specify a DNS domain without https://, port or path')
    return domain


def prepare(domain, port=8080, root=ROOT):
    domain = valid_domain(domain)
    if not 1024<=port<=65535:
        raise ValueError('Port must be between 1024 and 65535')
    env_path = root / '.env.nginx'
    if env_path.exists():
        values = dict(line.split('=',1) for line in env_path.read_text(encoding='utf-8').splitlines()
                      if line and not line.startswith('#'))
        if values.get('DOMAIN')!=domain or values.get('HOST_PORT')!=str(port):
            raise ValueError('.env.nginx already uses another domain/port; edit it explicitly before regenerating')
        if not values.get('SECRET_KEY') or not re.fullmatch(r'[a-zA-Z0-9_-]{32,}',values.get('POSTGRES_PASSWORD','')):
            raise ValueError('Existing .env.nginx has missing or invalid secrets')
    else:
        content = (f'DOMAIN={domain}\nHOST_PORT={port}\nSCHEDULE_PROJECT=schedule-nginx\n'
                   f'SECRET_KEY={secrets.token_urlsafe(64)}\nPOSTGRES_PASSWORD={secrets.token_hex(32)}\n')
        # Exclusive creation keeps reruns from replacing a database's password.
        descriptor = os.open(env_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(content)
    templates = root / 'deploy' / 'nginx'
    output = root / 'deploy' / 'generated'
    output.mkdir(parents=True,exist_ok=True)
    location = (templates/'location.conf.template').read_text(encoding='utf-8').replace('__PORT__',str(port))
    site = (templates/'site.conf.template').read_text(encoding='utf-8').replace('__DOMAIN__',domain)
    site = site.replace('__LOCATION__','\n'.join('    '+line if line else '' for line in location.splitlines()))
    (output/'location.conf').write_text(location,encoding='utf-8',newline='\n')
    (output/'nginx.conf').write_text(site,encoding='utf-8',newline='\n')
    return domain, output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain',help='schedule.example.org')
    parser.add_argument('--port',type=int,default=8080,help='Local backend port (default: 8080)')
    args=parser.parse_args()
    try:
        domain, output=prepare(args.domain,args.port)
    except (ValueError,OSError) as exc:
        parser.exit(1,f'Error: {exc}\n')
    print(f'Ready for {domain}. Secrets: .env.nginx (kept on rerun).')
    print('Start: bash deploy/server.sh up')
    print(f'New Nginx site: {output / "nginx.conf"}')
    print(f'Existing HTTPS site: use {output / "location.conf"} inside its server block.')


if __name__=='__main__':
    main()
