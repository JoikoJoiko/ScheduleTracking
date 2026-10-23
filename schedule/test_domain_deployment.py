"""Domain aliases must coexist with the existing IP deployment and real CSRF checks."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import Client,SimpleTestCase,TestCase,override_settings
from .test_http_deployment import transport_override

ROOT=Path(__file__).resolve().parent.parent
IP='185.171.83.215'
HOSTS=[IP,'legacy.example.test','127.0.0.1','nodecor.ru','www.nodecor.ru']
ORIGINS=[f'http://{IP}:5555','http://legacy.example.test','http://nodecor.ru','http://www.nodecor.ru']


class DomainSettingsTests(SimpleTestCase):
    def test_csv_lists_keep_existing_values_and_ignore_empty_entries_and_spaces(self):
        with patch.dict(os.environ,{'DEBUG':'0','DEPLOY_SCHEME':'http','SECRET_KEY':'domain-test-secret-ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                'ALLOWED_HOSTS':', '+', '.join(HOSTS)+',,',
                'CSRF_TRUSTED_ORIGINS':', '+', '.join(ORIGINS)+',,'},clear=True):
            values=runpy.run_path(str(ROOT/'college'/'settings.py'))
        self.assertEqual(values['ALLOWED_HOSTS'],HOSTS)
        self.assertEqual(values['CSRF_TRUSTED_ORIGINS'],ORIGINS)
        self.assertFalse(values['DEBUG']);self.assertFalse(values['SECURE_SSL_REDIRECT'])

    def test_compose_resolves_old_env_with_custom_hosts_without_rewriting_it(self):
        if not shutil.which('docker'):
            self.skipTest('Docker Compose config is exercised in Linux CI')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.env.nginx'
            original=('DOMAIN='+IP+'\nSECRET_KEY=domain-compose-test-secret\nPOSTGRES_PASSWORD='+'a'*64+
                '\nDEPLOY_SCHEME=http\nPUBLIC_ORIGIN='+ORIGINS[0]+
                '\nALLOWED_HOSTS=legacy.example.test,127.0.0.1\nCSRF_TRUSTED_ORIGINS=http://legacy.example.test\n')
            path.write_text(original,encoding='utf-8')
            # Shell variables have precedence over --env-file; isolate this fixture from the host.
            env={k:v for k,v in os.environ.items() if k not in ['DOMAIN','SECRET_KEY','POSTGRES_PASSWORD',
                'DEPLOY_SCHEME','PUBLIC_ORIGIN','ALLOWED_HOSTS','CSRF_TRUSTED_ORIGINS','HOST_PORT','SCHEDULE_PROJECT']}
            result=subprocess.run(['docker','compose','--env-file',str(path),'-f',str(ROOT/'compose.nginx.yaml'),
                'config','--format','json'],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            services=json.loads(result.stdout)['services'];values=services['app']['environment']
            self.assertEqual(values['ALLOWED_HOSTS'].split(','),HOSTS)
            self.assertEqual(values['CSRF_TRUSTED_ORIGINS'].split(','),ORIGINS+['https://nodecor.ru','https://www.nodecor.ru'])
            self.assertEqual(values['DEBUG'],'0');self.assertEqual(values['DEPLOY_SCHEME'],'http')
            self.assertEqual(values['PUBLIC_ORIGIN'],ORIGINS[0])
            self.assertFalse(services['db'].get('ports'));self.assertFalse(services['app'].get('ports'))
            self.assertEqual(services['web']['ports'][0]['host_ip'],'127.0.0.1')
            self.assertEqual(path.read_text(encoding='utf-8'),original)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DomainHttpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser(username='domain-admin',password='domain-test-password')

    def test_ip_and_both_domains_serve_public_pages_without_tls_redirect(self):
        with transport_override(),override_settings(ALLOWED_HOSTS=HOSTS,CSRF_TRUSTED_ORIGINS=ORIGINS):
            for host in [f'{IP}:5555','nodecor.ru','www.nodecor.ru','legacy.example.test']:
                for path in ['/','/healthz','/api/meta','/admin/login/']:
                    with self.subTest(host=host,path=path):
                        response=self.client.get(path,HTTP_HOST=host,HTTP_X_FORWARDED_PROTO='http')
                        self.assertEqual(response.status_code,200)
                        self.assertFalse(response.wsgi_request.is_secure())
                        self.assertNotIn('Strict-Transport-Security',response)
            self.assertEqual(self.client.get('/',HTTP_HOST='untrusted.example.test').status_code,400)

    def test_https_origin_login_through_existing_http_proxy_hop(self):
        origins=ORIGINS+['https://nodecor.ru','https://www.nodecor.ru']
        with transport_override(),override_settings(ALLOWED_HOSTS=HOSTS,CSRF_TRUSTED_ORIGINS=origins):
            for host in ['nodecor.ru','www.nodecor.ru']:
                with self.subTest(host=host):
                    client=Client(enforce_csrf_checks=True)
                    headers={'HTTP_HOST':host,'HTTP_X_FORWARDED_PROTO':'http','HTTP_ORIGIN':'https://'+host}
                    client.get('/admin/login/',**headers)
                    data={'username':'domain-admin','password':'domain-test-password','next':'/admin/'}
                    self.assertEqual(client.post('/admin/login/',data,**headers).status_code,403)
                    data['csrfmiddlewaretoken']=client.cookies['schedule_csrftoken'].value
                    self.assertEqual(client.post('/admin/login/',data,**{**headers,'HTTP_ORIGIN':'https://untrusted.example.test'}).status_code,403)
                    self.assertEqual(client.post('/admin/login/',data,**headers).status_code,302)
                    self.assertEqual(client.get('/admin/',**headers).status_code,200)

    def test_domain_logins_keep_csrf_even_if_the_intermediate_proxy_adds_port_5555(self):
        with transport_override(),override_settings(ALLOWED_HOSTS=HOSTS,CSRF_TRUSTED_ORIGINS=ORIGINS):
            for host in ['nodecor.ru','www.nodecor.ru']:
                with self.subTest(host=host):
                    client=Client(enforce_csrf_checks=True)
                    headers={'HTTP_HOST':host+':5555','HTTP_X_FORWARDED_PROTO':'http','HTTP_ORIGIN':'http://'+host}
                    self.assertEqual(client.get('/admin/login/',**headers).status_code,200)
                    data={'username':'domain-admin','password':'domain-test-password','next':'/admin/'}
                    self.assertEqual(client.post('/admin/login/',data,**headers).status_code,403)
                    data['csrfmiddlewaretoken']=client.cookies['schedule_csrftoken'].value
                    self.assertEqual(client.post('/admin/login/',data,**{**headers,'HTTP_ORIGIN':'http://untrusted.example.test'}).status_code,403)
                    response=client.post('/admin/login/',data,**headers)
                    self.assertEqual(response.status_code,302)
                    self.assertFalse(response.cookies['schedule_sessionid']['secure'])
                    self.assertFalse(response.cookies['schedule_csrftoken']['secure'])
                    self.assertEqual(client.get('/admin/',**headers).status_code,200)
