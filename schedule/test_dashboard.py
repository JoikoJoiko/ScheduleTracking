import io
import tempfile
from unittest.mock import patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from .models import ImportBatch, ImageImport, Lesson, StudentGroup, ScheduleVersion
from .test_support import college_workbook


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DashboardTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='operator',password='test-password')
        self.client.force_login(self.user)

    def upload(self,course=4,publish=True,**file_options):
        return self.client.post(reverse('admin:upload_schedule'),{
            'course':course,'publish':'1' if publish else '',
            'file':SimpleUploadedFile('course.xlsx',college_workbook(**file_options)),
        })

    def test_home_puts_upload_forms_before_directories(self):
        response=self.client.get('/admin/')
        self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertEqual(html.count('data-kind="schedule"'),4)
        self.assertIn('data-kind="changes"',html)
        self.assertLess(html.index('Расписание по курсам'),html.index('Технические настройки и справочники'))
        self.assertNotIn('dashboard-directories',html)

    def test_upload_parses_publishes_and_updates_status(self):
        response=self.upload()
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(response.json()['state'],'published')
        self.assertTrue(Lesson.objects.exists())
        status=self.client.get(reverse('admin:upload_status')).json()
        course=next(row for row in status['courses'] if row['number']==4)
        self.assertEqual(course['groups'],1)
        self.assertIsNotNone(course['published_at'])
        self.assertEqual(status['history'][0]['label'],'Опубликовано')

    def test_wrong_course_is_rejected_without_import(self):
        response=self.upload(course=1)
        self.assertEqual(response.status_code,400)
        self.assertIn('Вы выбрали 1 курс',response.json()['error'])
        self.assertFalse(ImportBatch.objects.exists())
        self.assertFalse(StudentGroup.objects.exists())

    def test_preview_mode_does_not_publish(self):
        response=self.upload(publish=False)
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(response.json()['state'],'review')
        self.assertTrue(ImportBatch.objects.exists())
        self.assertFalse(Lesson.objects.exists())

    def test_course_number_has_server_validation(self):
        self.assertEqual(self.upload(course=5).status_code,400)

    def test_conflict_keeps_reviewable_draft_and_existing_schedule(self):
        self.assertEqual(self.upload().json()['state'],'published')
        response=self.upload(course=3,group='ДРУГ-1-24')
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(response.json()['state'],'review')
        self.assertTrue(response.json()['errors'])
        self.assertEqual(ScheduleVersion.objects.filter(status='published').count(),1)
        self.assertEqual(StudentGroup.objects.count(),1)

    def test_invalid_file_does_not_change_published_schedule(self):
        self.upload()
        count=Lesson.objects.count()
        response=self.client.post(reverse('admin:upload_schedule'),{'course':4,'publish':'1','file':SimpleUploadedFile('bad.xlsx',b'invalid')})
        self.assertEqual(response.status_code,400)
        self.assertEqual(Lesson.objects.count(),count)

    def test_image_upload_appears_in_review_history(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(MEDIA_ROOT=folder),patch('schedule.image_import.analyze',return_value=([],['Проверьте изображение'])):
            image=io.BytesIO();Image.new('RGB',(200,100),'white').save(image,format='PNG')
            response=self.client.post(reverse('admin:upload_changes'),{'target_date':'2026-10-22','layout':'yellow',
                'image':SimpleUploadedFile('changes.png',image.getvalue(),content_type='image/png')})
            self.assertEqual(response.status_code,201,response.content)
            self.assertEqual(response.json()['state'],'review')
            self.assertEqual(ImageImport.objects.get().status,'review')
            status=self.client.get(reverse('admin:upload_status')).json()
            self.assertEqual(status['pending_images'],1)
            self.assertEqual(status['history'][0]['kind'],'changes')

    def test_upload_routes_require_session(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse('admin:upload_schedule')).status_code,302)
        self.assertEqual(self.client.get(reverse('admin:upload_status')).status_code,302)

    def test_dispatcher_cannot_upload_course_or_see_others_history(self):
        self.upload()
        dispatcher=get_user_model().objects.create_user(username='dispatcher',password='test-password',is_staff=True)
        self.client.force_login(dispatcher)
        self.assertEqual(self.upload().status_code,403)
        self.assertEqual(self.client.get(reverse('admin:upload_status')).json()['history'],[])

    def test_csrf_is_required_on_dashboard_uploads(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(reverse('admin:upload_schedule'),{}).status_code,403)

    def test_upload_endpoints_do_not_accept_get(self):
        self.assertEqual(self.client.get(reverse('admin:upload_schedule')).status_code,405)
        self.assertEqual(self.client.get(reverse('admin:upload_changes')).status_code,405)
