from django.test import TestCase,override_settings
from . import tests as fixtures
from .models import ImageImport,Change


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ImageBulkReviewTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson

    def batch(self,payload):
        self.client.force_login(self.admin)
        return ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin,payload=payload)

    def test_bulk_buttons_and_hidden_delete_fields_are_only_available_during_review(self):
        batch=self.batch([{'raw':'Не разобрано','source_box':[0,0,10,10]}])
        url=f'/admin/schedule/imageimport/{batch.pk}/review/'
        response=self.client.get(url)
        self.assertContains(response,'Выбрать все');self.assertContains(response,'Удалить пустые строки')
        self.assertContains(response,'name="form-0-DELETE"')
        self.assertContains(response,'data-has-source="1"')
        self.assertFalse(response.context['formset'].forms[0]['use'].value())
        batch.status='published';batch.save()
        response=self.client.get(url)
        self.assertNotContains(response,'data-review-select-all');self.assertNotContains(response,'data-review-remove-empty')

    def test_deleted_empty_or_invalid_rows_do_not_block_selected_rows_or_shift_their_indices(self):
        batch=self.batch([{}, {'group_id':self.group.pk,'slot':1}])
        url=f'/admin/schedule/imageimport/{batch.pk}/review/'
        data={'form-TOTAL_FORMS':3,'form-INITIAL_FORMS':2,
            'form-0-DELETE':'on','form-0-use':'on','form-0-slot':'invalid',
            'form-1-use':'on','form-1-group':self.group.pk,'form-1-slot':1,'form-1-kind':'replace',
            'form-1-subject':self.subject.pk,'form-1-teacher':self.teacher.pk,'form-1-room':self.room.pk,
            'form-2-DELETE':'on'}
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        batch.refresh_from_db();self.assertEqual(batch.status,'published')
        self.assertEqual(Change.objects.count(),1)
        self.assertEqual(Change.objects.get().original_id,self.lesson.pk)

    def test_deletion_marks_survive_validation_errors_without_publishing(self):
        batch=self.batch([{}, {'group_id':self.group.pk,'slot':1}])
        url=f'/admin/schedule/imageimport/{batch.pk}/review/'
        data={'form-TOTAL_FORMS':2,'form-INITIAL_FORMS':2,'form-0-DELETE':'on',
              'form-1-use':'on','form-1-group':self.group.pk,'form-1-slot':1,'form-1-kind':'replace'}
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['formset'].forms[0]['DELETE'].value())
        self.assertTrue(response.context['formset'].forms[1].errors)
        self.assertFalse(Change.objects.exists())
        batch.refresh_from_db();self.assertEqual(batch.status,'review')
