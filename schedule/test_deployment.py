import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch
from django.core.wsgi import get_wsgi_application
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from waitress.proxy_headers import proxy_headers_middleware
from deploy.prepare import prepare, valid_domain
from deploy.serve import server_options

ROOT=Path(__file__).resolve().parent.parent
BASH=shutil.which('bash') or ('C:/Program Files/Git/bin/bash.exe' if Path('C:/Program Files/Git/bin/bash.exe').exists() else None)


class DeploymentConfigTests(SimpleTestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        shutil.copytree(ROOT/'deploy'/'nginx',self.root/'deploy'/'nginx')

    def test_generated_config_has_valid_nginx_variables_and_private_random_secrets(self):
        domain,output=prepare('Schedule.Example.Org',9080,self.root)
        self.assertEqual(domain,'schedule.example.org')
        values=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        self.assertGreaterEqual(len(values['SECRET_KEY']),64)
        self.assertRegex(values['POSTGRES_PASSWORD'],r'^[a-f0-9]{64}$')
        site=(output/'nginx.conf').read_text()
        self.assertIn('server_name schedule.example.org;',site)
        self.assertIn('127.0.0.1:9080',site)
        self.assertIn('X-Forwarded-Proto $scheme;',site)
        self.assertIn('X-Forwarded-For $remote_addr;',site)
        self.assertNotIn('__LOCATION__',site)
        if os.name!='nt':
            self.assertEqual((self.root/'.env.nginx').stat().st_mode & 0o777,0o600)

    def test_rerun_preserves_database_password_and_secret(self):
        prepare('schedule.example.org',root=self.root)
        original=(self.root/'.env.nginx').read_bytes()
        prepare('schedule.example.org',root=self.root)
        self.assertEqual((self.root/'.env.nginx').read_bytes(),original)
        with self.assertRaises(ValueError):prepare('other.example.org',root=self.root)
        with self.assertRaises(ValueError):prepare('schedule.example.org',9000,self.root)
        self.assertEqual((self.root/'.env.nginx').read_bytes(),original)

    def test_invalid_domain_cannot_inject_nginx_or_env_directives(self):
        for domain in ['https://example.org','example.org:80','example.org/path','*.example.org',
                       'example.org;return 200;','example.org\nSECRET_KEY=evil','-bad.example.org','a..org']:
            with self.subTest(domain=domain), self.assertRaises(ValueError):prepare(domain,root=self.root)
        self.assertFalse((self.root/'.env.nginx').exists())
        with self.assertRaises(ValueError):prepare('schedule.example.org',80,self.root)
        self.assertTrue(valid_domain('расписание.рф').startswith('xn--'))

    def test_proxy_trust_requires_explicit_configuration(self):
        self.assertNotIn('trusted_proxy',server_options({}))
        options=server_options({'TRUST_PROXY':'1'})
        self.assertEqual(options['trusted_proxy_headers'],{'x-forwarded-for','x-forwarded-proto'})
        self.assertEqual(options['max_request_body_size'],12*1024*1024)


class DeploymentScriptTests(SimpleTestCase):
    def setUp(self):
        if not BASH:self.skipTest('Bash is unavailable; shell scenarios run in Linux CI')
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        (self.root/'deploy').mkdir();(self.root/'bin').mkdir()
        shutil.copy(ROOT/'deploy'/'server.sh',self.root/'deploy'/'server.sh')
        (self.root/'.env.nginx').write_text('DOMAIN=example.test\nSECRET_KEY=test-only-secret\n')
        fake=self.root/'bin'/'docker'
        fake.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$DEPLOY_TEST_LOG"
case "$*" in
  "compose version") exit 0 ;;
  *" ps --status running --services") printf 'app\\nweb\\ndb\\n' ;;
  *" build app") [[ "${DEPLOY_TEST_FAIL:-}" != build ]] || exit 21 ;;
  *"pg_dump"*) [[ "${DEPLOY_TEST_FAIL:-}" != dump ]] || exit 22; printf 'test database dump' ;;
  *"--entrypoint tar"*) printf 'test media archive' ;;
esac
''',encoding='utf-8',newline='\n')
        fake.chmod(0o755)

    def run_script(self,command,fail=''):
        env=os.environ.copy()
        env.update(PATH=str(self.root/'bin')+os.pathsep+env['PATH'],DEPLOY_TEST_LOG=str(self.root/'calls.log'),DEPLOY_TEST_FAIL=fail)
        result=subprocess.run([BASH,str(self.root/'deploy'/'server.sh'),command],env=env,capture_output=True,timeout=30)
        calls=(self.root/'calls.log').read_text() if (self.root/'calls.log').exists() else ''
        return result,calls

    def test_initial_up_waits_for_health_and_runs_production_checks(self):
        result,calls=self.run_script('up')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('up -d --build --wait',calls)
        self.assertIn('check --deploy --fail-level WARNING',calls)

    def test_backup_includes_database_images_secrets_and_resumes_services(self):
        result,calls=self.run_script('backup')
        self.assertEqual(result.returncode,0,result.stderr)
        complete=list((self.root/'backups').rglob('COMPLETE'))
        self.assertEqual(len(complete),1)
        target=complete[0].parent
        self.assertEqual((target/'production.env').read_bytes(),(self.root/'.env.nginx').read_bytes())
        self.assertTrue((target/'database.dump').stat().st_size)
        self.assertTrue((target/'media.tar.gz').stat().st_size)
        self.assertIn('stop web app',calls)
        self.assertIn('start --wait --wait-timeout 240 app web',calls)

    def test_failed_backup_resumes_services_without_a_complete_marker(self):
        result,calls=self.run_script('backup','dump')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('start --wait --wait-timeout 240 app web',calls)
        self.assertEqual(list((self.root/'backups').rglob('COMPLETE')),[])

    def test_failed_update_build_keeps_running_site_available(self):
        result,calls=self.run_script('update','build')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn(' stop ',calls)
        self.assertNotIn('pg_dump',calls)

    def test_failed_update_backup_does_not_apply_migrations(self):
        result,calls=self.run_script('update','dump')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('start --wait --wait-timeout 240 app web',calls)
        self.assertNotIn(' up ',calls)


class HealthTests(TestCase):
    def test_health_checks_database_and_disables_caching(self):
        response=self.client.get('/healthz')
        self.assertEqual(response.json(),{'status':'ok'})
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual(self.client.post('/healthz').status_code,405)

    def test_unavailable_database_returns_503_without_connection_details(self):
        with patch('schedule.views.connection.cursor',side_effect=DatabaseError('private credentials')):
            response=self.client.get('/healthz')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json(),{'status':'unavailable'})

    @override_settings(DEBUG=False,SECURE_SSL_REDIRECT=True,ALLOWED_HOSTS=['schedule.example.test'],
                       SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https'))
    def test_real_waitress_proxy_middleware_preserves_https_and_client_ip(self):
        options=server_options({'TRUST_PROXY':'1'})
        app=proxy_headers_middleware(get_wsgi_application(),trusted_proxy=options['trusted_proxy'],
            trusted_proxy_headers=options['trusted_proxy_headers'],trusted_proxy_count=1)
        environ=self.client._base_environ(PATH_INFO='/healthz',HTTP_HOST='schedule.example.test',
            REMOTE_ADDR='172.18.0.4',HTTP_X_FORWARDED_FOR='198.51.100.22',HTTP_X_FORWARDED_PROTO='https')
        status=[]
        response=app(environ,lambda value,headers:status.append(value))
        try:body=b''.join(response)
        finally:response.close()
        self.assertEqual(status,['200 OK'])
        self.assertEqual(json.loads(body),{'status':'ok'})
        self.assertEqual(environ['REMOTE_ADDR'],'198.51.100.22')
        self.assertEqual(environ['wsgi.url_scheme'],'https')
