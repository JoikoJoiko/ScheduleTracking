import shutil
import tempfile
from pathlib import Path
from django.test import SimpleTestCase,TestCase,Client,override_settings
from django.contrib.auth import get_user_model
from deploy.prepare import prepare
from .test_http_deployment import transport_override

ROOT=Path(__file__).resolve().parent.parent
IP='185.171.83.215'


class IpHttpsPreparationTests(SimpleTestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        shutil.copytree(ROOT/'deploy'/'nginx',self.root/'deploy'/'nginx')

    def test_tls_ip_on_5555_generates_certificate_paths_private_backend_and_only_an_acme_snippet(self):
        _,out=prepare(IP,root=self.root,scheme='https',public_port=5555)
        env=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        self.assertEqual(env['PUBLIC_ORIGIN'],f'https://{IP}:5555')
        self.assertEqual(env['DEPLOY_SCHEME'],'https')
        site=(out/'nginx.conf').read_text()
        for value in ['listen 5555 ssl;','127.0.0.1:8080','ssl_protocols TLSv1.2 TLSv1.3',f'schedule-ip-{IP}/fullchain.pem']:
            self.assertIn(value,site)
        for port in [80,3333,4444,443]:self.assertNotIn(f'listen {port}',site)
        snippet=(out/'acme-location.conf').read_text()
        self.assertIn('location ^~ /.well-known/acme-challenge/',snippet)
        self.assertNotIn('listen ',snippet);self.assertNotIn('proxy_pass',snippet)
        service=(out/'schedule-ip-certificate.service').read_text()
        self.assertIn(f'--cert-name schedule-ip-{IP}',service)
        self.assertIn('nginx -t && /usr/bin/systemctl reload nginx',service)
        self.assertIn('OnCalendar=hourly',(out/'schedule-ip-certificate.timer').read_text())

    def test_upgrading_http_preserves_secrets_and_requires_explicit_reconfiguration(self):
        prepare(IP,root=self.root,scheme='http',public_port=5555)
        before=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        with self.assertRaises(ValueError):prepare(IP,root=self.root,scheme='https',public_port=5555)
        prepare(IP,root=self.root,scheme='https',public_port=5555,reconfigure=True)
        after=dict(line.split('=',1) for line in (self.root/'.env.nginx').read_text().splitlines())
        for key in ['SECRET_KEY','POSTGRES_PASSWORD','SCHEDULE_PROJECT']:self.assertEqual(before[key],after[key])
        self.assertEqual(after['HOST_PORT'],'8080')

    def test_invalid_tls_ports_cannot_replace_existing_configuration(self):
        for port in [0,80,8080,65536]:
            with self.subTest(port=port),self.assertRaises(ValueError):
                prepare(IP,root=self.root,scheme='https',public_port=port)
        self.assertFalse((self.root/'.env.nginx').exists())


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class IpHttpsAdminTests(TestCase):
    def test_real_https_ip_login_csrf_cookies_and_original_port(self):
        get_user_model().objects.create_superuser(username='ip-admin',password='ip-https-test-password')
        origin=f'https://{IP}:5555'
        with transport_override('https'),override_settings(ALLOWED_HOSTS=[IP],CSRF_TRUSTED_ORIGINS=[origin]):
            client=Client(enforce_csrf_checks=True)
            headers={'HTTP_HOST':f'{IP}:5555','HTTP_X_FORWARDED_PROTO':'https','HTTP_ORIGIN':origin}
            page=client.get('/admin/login/',**headers);self.assertEqual(page.status_code,200)
            response=client.post('/admin/login/',{'username':'ip-admin','password':'ip-https-test-password',
                'csrfmiddlewaretoken':client.cookies['csrftoken'].value,'next':'/admin/'},**headers)
            self.assertEqual(response.status_code,302)
            self.assertTrue(response.cookies['sessionid']['secure']);self.assertTrue(response.cookies['csrftoken']['secure'])
            for url in ['/admin/','/api/meta','/healthz']:
                response=client.get(url,**headers);self.assertEqual(response.status_code,200)
                self.assertEqual(response.wsgi_request.get_host(),f'{IP}:5555')
            self.assertEqual(client.post('/api/admin/changes',data='{}',content_type='application/json',**headers).status_code,403)
