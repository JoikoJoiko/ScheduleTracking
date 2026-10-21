"""Generate server secrets and host Nginx configuration without changing the system."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def valid_domain(value):
    """Accept a DNS name or IPv4 literal; keep the existing helper API."""
    value=value.strip().rstrip('.')
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError:
        if re.fullmatch(r'[0-9.]+',value):
            raise ValueError('Invalid IPv4 address')
    try:
        domain = value.encode('idna').decode('ascii').lower()
    except UnicodeError as exc:
        raise ValueError('Invalid domain') from exc
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(domain)>253 or not re.fullmatch(rf'{label}(?:\.{label})+', domain):
        raise ValueError('Specify a DNS name or IPv4 address without scheme, port or path')
    return domain


def prepare(domain, port=8080, root=ROOT, *, scheme='https', public_port=None, reconfigure=False):
    domain = valid_domain(domain)
    if scheme not in ('http','https'):
        raise ValueError('Scheme must be http or https')
    public_port=public_port if public_port is not None else (5555 if scheme=='http' else 443)
    if not 1024<=port<=65535:
        raise ValueError('Local backend port must be between 1024 and 65535')
    if scheme=='http' and not 1024<=public_port<=65535:
        raise ValueError('HTTP test port must be between 1024 and 65535')
    if scheme=='https' and public_port!=443:
        raise ValueError('--public-port is for HTTP testing; configure custom HTTPS in your existing Nginx site')
    if port==public_port:
        raise ValueError('Public Nginx port must differ from the local Docker port')
    origin=f'{scheme}://{domain}'+(f':{public_port}' if (scheme,public_port)!=('https',443) else '')
    config={'DOMAIN':domain,'HOST_PORT':str(port),'DEPLOY_SCHEME':scheme,
            'PUBLIC_PORT':str(public_port),'PUBLIC_ORIGIN':origin}
    env_path = root / '.env.nginx'
    previous=None
    if env_path.exists():
        previous=env_path.read_text(encoding='utf-8')
        values = dict(line.split('=',1) for line in previous.splitlines()
                      if line and not line.startswith('#'))
        legacy={'DEPLOY_SCHEME':'https','PUBLIC_PORT':'443','PUBLIC_ORIGIN':f'https://{values.get("DOMAIN","")}'}
        if not reconfigure and any(values.get(key,legacy.get(key))!=value for key,value in config.items()):
            raise ValueError('.env.nginx uses another host/port/scheme; use --reconfigure to change settings while keeping secrets')
        if not values.get('SECRET_KEY') or not re.fullmatch(r'[a-zA-Z0-9_-]{32,}',values.get('POSTGRES_PASSWORD','')):
            raise ValueError('Existing .env.nginx has missing or invalid secrets')
    else:
        values={'SCHEDULE_PROJECT':'schedule-nginx','SECRET_KEY':secrets.token_urlsafe(64),
                'POSTGRES_PASSWORD':secrets.token_hex(32)}
    values.update(config)
    content=''.join(f'{key}={value}\n' for key,value in values.items())
    if previous is None:
        # Exclusive creation keeps reruns from replacing a database's password.
        descriptor = os.open(env_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(content)
    elif content!=previous:
        # Reconfiguration preserves passwords and the Compose project/volume identity.
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',dir=root,delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(content)
        try:
            os.replace(temporary,env_path)
        finally:
            temporary.unlink(missing_ok=True)
    templates = root / 'deploy' / 'nginx'
    output = root / 'deploy' / 'generated'
    output.mkdir(parents=True,exist_ok=True)
    location = (templates/'location.conf.template').read_text(encoding='utf-8').replace('__PORT__',str(port))
    site = (templates/'site.conf.template').read_text(encoding='utf-8').replace('__DOMAIN__',domain)
    comment=('# HTTP test mode: credentials are unencrypted; restrict access to a trusted network.' if scheme=='http'
             else f'# Enable this site, then use certbot --nginx -d {domain} for HTTPS.')
    site=site.replace('__LISTEN_PORT__',str(public_port if scheme=='http' else 80)).replace('__COMMENT__',comment)
    site = site.replace('__LOCATION__','\n'.join('    '+line if line else '' for line in location.splitlines()))
    (output/'location.conf').write_text(location,encoding='utf-8',newline='\n')
    (output/'nginx.conf').write_text(site,encoding='utf-8',newline='\n')
    return domain, output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain',help='DNS name or IPv4 address, without scheme or port')
    parser.add_argument('--port',type=int,default=8080,help='Local backend port (default: 8080)')
    parser.add_argument('--scheme',choices=['https','http'],default='https',help='Public transport; HTTP is for trusted-network tests')
    parser.add_argument('--public-port',type=int,help='Public HTTP test port (default: 5555)')
    parser.add_argument('--reconfigure',action='store_true',help='Change existing connection settings without replacing secrets')
    args=parser.parse_args()
    try:
        domain, output=prepare(args.domain,args.port,scheme=args.scheme,public_port=args.public_port,reconfigure=args.reconfigure)
    except (ValueError,OSError) as exc:
        parser.exit(1,f'Error: {exc}\n')
    print(f'Ready for {domain}. Secrets: .env.nginx (kept on rerun).')
    if args.scheme=='http':
        print(f'URL: http://{domain}:{args.public_port or 5555}')
        print('WARNING: HTTP exposes logins and sessions. Use only for testing in a trusted network.')
    print('Start: bash deploy/server.sh up')
    print(f'New Nginx site: {output / "nginx.conf"}')
    print(f'Existing site: use {output / "location.conf"} inside its server block.')


if __name__=='__main__':
    main()
