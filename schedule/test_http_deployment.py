"""The HTTP opt-in must keep production validation and real CSRF/session behavior."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.wsgi import get_wsgi_application
from django.test import Client, SimpleTestCase, TestCase, override_settings
from waitress.proxy_headers import proxy_headers_middleware
from deploy.prepare import prepare, valid_domain
from deploy.healthcheck import probe_headers
from deploy.serve import server_options
from . import test_deployment as base

ROOT=Path(__file__).resolve().parent.parent
IP='185.171.83.215'
ORIGIN=f'http://{IP}:5555'


def production_settings(scheme='http'):
    host=IP if scheme=='http' else 'schedule.example.test'
    origin=ORIGIN if scheme=='http' else f'https://{host}'
    with patch.dict(os.environ,{'DEBUG':'0','DEPLOY_SCHEME':scheme,'SECRET_KEY':'test-transport-settings-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789',
                               'ALLOWED_HOSTS':host,'CSRF_TRUSTED_ORIGINS':origin,'TRUST_PROXY':'1'}):
        return runpy.run_path(str(ROOT/'college'/'settings.py'))


def transport_override(scheme='http'):
    values=production_settings(scheme)
    keys=['DEBUG','DEPLOY_SCHEME','ALLOWED_HOSTS','CSRF_TRUSTED_ORIGINS','SECURE_PROXY_SSL_HEADER',
          'SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE','SECURE_SSL_REDIRECT','SECURE_HSTS_SECONDS',
          'SECURE_HSTS_INCLUDE_SUBDOMAINS','SILENCED_SYSTEM_CHECKS']
    return override_settings(**{key:values[key] for key in keys},
        SESSION_COOKIE_NAME=values.get('SESSION_COOKIE_NAME','sessionid'),
        CSRF_COOKIE_NAME=values.get('CSRF_COOKIE_NAME','csrftoken'))


class HttpPreparationTests(SimpleTestCase):
    setUp=base.DeploymentConfigTests.setUp

    def test_ip_http_generates_exact_ports_origin_and_no_tls_directives(self):
        host,output=prepare(IP,root=self.root,scheme='http',public_port=5555)
        self.assertEqual(host,IP)
        values=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        self.assertEqual(values['DEPLOY_SCHEME'],'http')
        self.assertEqual(values['PUBLIC_ORIGIN'],ORIGIN)
        self.assertEqual(values['HOST_PORT'],'8080')
        self.assertEqual(values['PUBLIC_PORT'],'5555')
        site=(output/'nginx.conf').read_text()
        self.assertIn('listen 5555;',site)
        self.assertIn(f'server_name {IP};',site)
        self.assertIn('proxy_pass http://127.0.0.1:8080;',site)
        self.assertIn('proxy_set_header Host $http_host;',site)
        for value in ['listen 80;','listen 3333;','listen 4444;','ssl_certificate','certbot','return 301']:
            self.assertNotIn(value,site)

    def test_ip_defaults_to_https_until_http_explicitly_selected(self):
        _,output=prepare(IP,root=self.root)
        values=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        self.assertEqual(values['DEPLOY_SCHEME'],'https')
        self.assertEqual(values['PUBLIC_ORIGIN'],f'https://{IP}')
        self.assertIn('listen 80;',(output/'nginx.conf').read_text())

    def test_http_default_external_port_is_5555_and_backend_can_be_changed(self):
        _,output=prepare(IP,9080,self.root,scheme='http')
        self.assertIn('listen 5555;',(output/'nginx.conf').read_text())
        self.assertIn('127.0.0.1:9080',(output/'nginx.conf').read_text())
        for port in [80,0,65536,8080]:
            with self.subTest(port=port), self.assertRaises(ValueError):
                prepare(IP,root=self.root,scheme='http',public_port=port)

    def test_invalid_ip_and_scheme_fail_before_creating_configuration(self):
        for host in ['185.171.83.256','999.171.83.215','185.171.083.215','http://185.171.83.215:5555','185.171.83.215\nlisten 80;']:
            with self.subTest(host=host), self.assertRaises(ValueError):valid_domain(host)
        with self.assertRaises(ValueError):prepare(IP,root=self.root,scheme='ftp')
        self.assertFalse((self.root/'.env.nginx').exists())

    def test_reconfiguration_preserves_passwords_project_and_unknown_settings(self):
        prepare('schedule.example.test',root=self.root)
        before=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        with self.assertRaises(ValueError):prepare(IP,root=self.root,scheme='http')
        with (self.root/'.env.nginx').open('a') as stream:stream.write('CUSTOM_SETTING=keep-me\n')
        prepare(IP,root=self.root,scheme='http',reconfigure=True)
        after=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        for key in ['SECRET_KEY','POSTGRES_PASSWORD','SCHEDULE_PROJECT']:
            self.assertEqual(after[key],before[key])
        self.assertEqual(after['CUSTOM_SETTING'],'keep-me')
        self.assertEqual(after['PUBLIC_ORIGIN'],ORIGIN)
        prepare('schedule.example.test',root=self.root,reconfigure=True)
        self.assertIn('DEPLOY_SCHEME=https',(self.root/'.env.nginx').read_text())

    def test_legacy_https_env_upgrades_without_changing_secrets(self):
        env=self.root/'.env.nginx'
        legacy={'DOMAIN':'schedule.example.test','HOST_PORT':'8080','SCHEDULE_PROJECT':'schedule-nginx',
                'SECRET_KEY':'existing-secret','POSTGRES_PASSWORD':'a'*64}
        env.write_text(''.join(f'{k}={v}\n' for k,v in legacy.items()))
        prepare('schedule.example.test',root=self.root)
        result=dict(line.split('=',1) for line in env.read_text().splitlines())
        self.assertEqual(result['SECRET_KEY'],legacy['SECRET_KEY'])
        self.assertEqual(result['POSTGRES_PASSWORD'],legacy['POSTGRES_PASSWORD'])
        self.assertEqual(result['DEPLOY_SCHEME'],'https')

    def test_cli_accepts_ip_http_arguments_and_warns_about_unencrypted_transport(self):
        shutil.copy(ROOT/'deploy'/'prepare.py',self.root/'deploy'/'prepare.py')
        result=subprocess.run([sys.executable,str(self.root/'deploy'/'prepare.py'),IP,'--scheme','http',
            '--public-port','5555','--port','8080'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(ORIGIN,result.stdout)
        self.assertIn('WARNING: HTTP',result.stdout)

    def test_health_checks_match_each_public_transport_and_preserve_port(self):
        self.assertEqual(probe_headers({'ALLOWED_HOSTS':IP,'DEPLOY_SCHEME':'http','PUBLIC_ORIGIN':ORIGIN}),
                         {'Host':f'{IP}:5555','X-Forwarded-Proto':'http'})
        self.assertEqual(probe_headers({'ALLOWED_HOSTS':'schedule.example.test'}),
                         {'Host':'schedule.example.test','X-Forwarded-Proto':'https'})


class ProductionTransportSettingsTests(SimpleTestCase):
    def test_http_is_production_with_only_tls_requirements_relaxed(self):
        settings=production_settings()
        self.assertFalse(settings['DEBUG'])
        for key in ['SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE','SECURE_SSL_REDIRECT','SECURE_HSTS_INCLUDE_SUBDOMAINS']:
            self.assertFalse(settings[key],key)
        self.assertEqual(settings['SECURE_HSTS_SECONDS'],0)
        self.assertIn('django.middleware.csrf.CsrfViewMiddleware',settings['MIDDLEWARE'])
        self.assertTrue(settings['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(settings['CSRF_TRUSTED_ORIGINS'],[ORIGIN])
        self.assertNotIn('security.W003',settings['SILENCED_SYSTEM_CHECKS'])
        self.assertNotIn('security.W009',settings['SILENCED_SYSTEM_CHECKS'])

    def test_https_remains_secure_by_default_in_production(self):
        with patch.dict(os.environ,{'DEBUG':'0','SECRET_KEY':'test-default-secret'},clear=True):
            settings=runpy.run_path(str(ROOT/'college'/'settings.py'))
        self.assertEqual(settings['DEPLOY_SCHEME'],'https')
        for key in ['SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE','SECURE_SSL_REDIRECT','SECURE_HSTS_INCLUDE_SUBDOMAINS']:
            self.assertTrue(settings[key],key)
        self.assertEqual(settings['SECURE_HSTS_SECONDS'],31536000)
        self.assertNotIn('security.W008',settings['SILENCED_SYSTEM_CHECKS'])
        self.assertNotIn('security.W016',settings['SILENCED_SYSTEM_CHECKS'])

    def test_invalid_scheme_does_not_silently_disable_tls(self):
        with self.assertRaises(ImproperlyConfigured):production_settings('httsp')


class HttpAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password='deployment-test-password'
        get_user_model().objects.create_superuser(username='deploy-admin',password=cls.password)

    def login(self,scheme='http'):
        client=Client(enforce_csrf_checks=True)
        host=f'{IP}:5555' if scheme=='http' else 'schedule.example.test'
        origin=ORIGIN if scheme=='http' else f'https://{host}'
        headers={'HTTP_HOST':host,'HTTP_X_FORWARDED_PROTO':scheme,'HTTP_ORIGIN':origin}
        get=client.get('/admin/login/',**headers)
        self.assertEqual(get.status_code,200)
        cookie='schedule_csrftoken' if scheme=='http' else 'csrftoken'
        response=client.post('/admin/login/',{'username':'deploy-admin','password':self.password,
            'csrfmiddlewaretoken':client.cookies[cookie].value,'next':'/admin/'},**headers)
        self.assertEqual(response.status_code,302)
        return client,response,headers

    def test_admin_login_sessions_api_and_health_work_over_http_with_port(self):
        with transport_override():
            client,response,headers=self.login()
            self.assertFalse(response.cookies['schedule_sessionid']['secure'])
            self.assertFalse(response.cookies['schedule_csrftoken']['secure'])
            self.assertTrue(response.cookies['schedule_sessionid']['httponly'])
            self.assertNotIn('Strict-Transport-Security',response)
            for path in ['/admin/','/api/meta','/healthz','/']:
                get=client.get(path,**headers)
                self.assertEqual(get.status_code,200,path)
                self.assertEqual(get.wsgi_request.get_host(),f'{IP}:5555')
                self.assertFalse(get.wsgi_request.is_secure())

    def test_http_mutations_still_require_csrf_and_the_correct_origin(self):
        with transport_override():
            client,response,headers=self.login()
            path='/api/admin/changes'
            self.assertEqual(client.post(path,data='{}',content_type='application/json',**headers).status_code,403)
            headers['HTTP_X_CSRFTOKEN']=client.cookies['schedule_csrftoken'].value
            wrong={**headers,'HTTP_ORIGIN':f'http://{IP}:4444'}
            self.assertEqual(client.post(path,data='{}',content_type='application/json',**wrong).status_code,403)
            # Validation, rather than CSRF, rejects this incomplete authenticated request.
            self.assertEqual(client.post(path,data='{}',content_type='application/json',**headers).status_code,400)

    def test_https_login_keeps_secure_cookies_hsts_and_http_redirect(self):
        with transport_override('https'):
            client,response,headers=self.login('https')
            self.assertTrue(response.cookies['sessionid']['secure'])
            self.assertTrue(response.cookies['csrftoken']['secure'])
            self.assertIn('Strict-Transport-Security',response)
            redirect=client.get('/admin/login/',HTTP_HOST='schedule.example.test',HTTP_X_FORWARDED_PROTO='http')
            self.assertEqual(redirect.status_code,301)
            self.assertTrue(redirect.headers['Location'].startswith('https://'))

    def test_wsgi_proxy_http_health_is_ready_without_a_redirect(self):
        with transport_override():
            options=server_options({'TRUST_PROXY':'1'})
            app=proxy_headers_middleware(get_wsgi_application(),trusted_proxy=options['trusted_proxy'],
                trusted_proxy_headers=options['trusted_proxy_headers'],trusted_proxy_count=1)
            environ=self.client._base_environ(PATH_INFO='/healthz',HTTP_HOST=f'{IP}:5555',REMOTE_ADDR='172.18.0.4',
                HTTP_X_FORWARDED_FOR='198.51.100.22',HTTP_X_FORWARDED_PROTO='http')
            status=[]
            response=app(environ,lambda value,headers:status.append(value))
            try:body=b''.join(response)
            finally:response.close()
            self.assertEqual(status,['200 OK'])
            self.assertEqual(json.loads(body),{'status':'ok'})
            self.assertEqual(environ['HTTP_HOST'],f'{IP}:5555')
            self.assertEqual(environ['wsgi.url_scheme'],'http')
            self.assertEqual(environ['REMOTE_ADDR'],'198.51.100.22')
