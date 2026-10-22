import io
from datetime import date,time
from unittest.mock import patch
from PIL import Image,ImageDraw
from openpyxl import Workbook
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from .models import *
from .college_import import parse_college,install_bells
from .importing import preview_import,confirm_import,import_report
from .image_import import decode_cell,suggestion,upload_image,publish_review,yellow_fraction
from . import services as svc
from .test_support import college_workbook


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CollegeImportTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='reviewer',password='test-password')
        self.profile=ImportProfile.objects.get(kind='college')
    def workbook(self,group='ТЕСТ-1-23',teacher='Тестов А.А.',room='103п3'):
        return college_workbook(group,teacher,room)
    def batch(self,**kwargs):
        return preview_import(SimpleUploadedFile('course.xlsx',self.workbook(**kwargs)),self.profile,None,None,None,self.user)
    def test_actual_layout_two_rows_two_weeks(self):
        rows,metadata,errors=parse_college(self.workbook())
        self.assertEqual(errors,[]);self.assertEqual(len(rows),2)
        self.assertEqual({r['week_type'] for r in rows},{'odd','even'})
        self.assertEqual(rows[0]['room'],'103п3');self.assertEqual(rows[0]['teacher'],'Тестов А.А.')
        self.assertEqual(metadata['groups'][0]['course'],4)
    def test_course_cannot_exceed_four(self):
        with self.assertRaises(ValidationError):parse_college(self.workbook(group='ТЕСТ-1-22'))
    def test_preview_leaves_directories_unchanged(self):
        batch=self.batch();report=import_report(batch)
        self.assertEqual(report['conflicts'],[])
        self.assertFalse(StudentGroup.objects.exists());self.assertFalse(Lesson.objects.exists())
    def test_course_uploads_accumulate(self):
        a=confirm_import(self.batch(),self.user)
        b=confirm_import(self.batch(group='ТЕСТ-1-24',teacher='Другов Б.Б.',room='201п'),self.user)
        self.assertEqual(a.groups.count(),1);self.assertEqual(b.groups.count(),1)
        rows=svc.schedule_on(date(2026,10,26))
        self.assertEqual({g['name'] for r in rows for g in r['groups']},{'ТЕСТ-1-23','ТЕСТ-1-24'})
    def test_reimport_replaces_only_own_groups(self):
        confirm_import(self.batch(),self.user)
        confirm_import(self.batch(group='ТЕСТ-1-24',teacher='Другов Б.Б.',room='201п'),self.user)
        confirm_import(self.batch(room='202п'),self.user)
        rows=svc.schedule_on(date(2026,10,26))
        self.assertEqual(len(rows),2)
        self.assertEqual({r['room'] for r in rows},{'202п','201п'})
    def test_same_file_does_not_duplicate(self):
        data=self.workbook()
        first=preview_import(SimpleUploadedFile('same.xlsx',data),self.profile,None,None,None,self.user)
        confirm_import(first,self.user)
        second=preview_import(SimpleUploadedFile('same.xlsx',data),self.profile,None,None,None,self.user)
        with self.assertRaises(ValidationError):confirm_import(second,self.user)
        self.assertEqual(StudentGroup.objects.count(),1)
    def test_old_parser_allows_one_reviewed_reimport_with_corrected_parser(self):
        data=self.workbook()
        def batch():
            return preview_import(SimpleUploadedFile('same.xlsx',data),self.profile,None,None,None,self.user)
        first=batch();confirm_import(first,self.user)
        first.metadata.pop('parser_revision');first.save(update_fields=['metadata'])
        second=batch();version=confirm_import(second,self.user)
        self.assertNotEqual(version.pk,first.version_id)
        with self.assertRaises(ValidationError):confirm_import(batch(),self.user)
    def test_untimed_event_does_not_invent_occupancy(self):
        version=confirm_import(self.batch(),self.user)
        lesson=version.lessons.first();lesson.time_slot=TimeSlot.objects.get(sort_order=0);lesson.save()
        rows=svc.schedule_on(date(2026,10,26))
        self.assertIsNone(rows[0]['start'])
        self.assertEqual(svc.conflicts_for(date(2026,10,26),rows),[])
    def test_bells_and_admin_page(self):
        self.assertEqual(TimeSlot.objects.get(sort_order=1).start_time,time(9))
        self.assertEqual(self.client.get('/bells').status_code,200)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/admin/schedule/timeslot/').status_code,200)
    def test_dash_and_regular_markers_do_nothing(self):
        for text in ['-','—','−','По расп','По расписанию','По рас:','']:
            self.assertIsNone(decode_cell(text),text)
    def test_ocr_teacher_does_not_consume_subject(self):
        row=decode_cell('Сертиф Павлов С.С. 405')
        self.assertEqual(row['subject_text'],'Сертиф')
        self.assertEqual(row['teacher_text'],'Павлов С.С.')
        self.assertEqual(row['room_text'],'405')
    def test_unknown_group_is_not_guessed_from_another_year(self):
        confirm_import(self.batch(),self.user)
        self.assertIsNone(suggestion(StudentGroup,'ТЕСТ-1-24'))
    def test_yellow_detection_ignores_white_cells(self):
        image=Image.new('RGB',(100,30),'white');ImageDraw.Draw(image).rectangle((50,0,99,29),fill='#fff2cc')
        self.assertEqual(yellow_fraction(image,(0,0,49,29)),0)
        self.assertGreater(yellow_fraction(image,(50,0,99,29)),.9)
    def test_image_upload_does_not_publish(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder,override_settings(MEDIA_ROOT=folder),patch('schedule.image_import.analyze',return_value=([],['Проверка'])):
            output=io.BytesIO();Image.new('RGB',(100,50),'white').save(output,format='PNG')
            batch=upload_image(SimpleUploadedFile('sample.png',output.getvalue()),date(2026,10,26),'table',self.user)
            self.assertEqual(batch.status,'review');self.assertFalse(Change.objects.exists())
            self.client.force_login(self.user)
            self.assertEqual(self.client.get(f'/admin/schedule/imageimport/{batch.pk}/review/').status_code,200)
            response=self.client.get(f'/admin/schedule/imageimport/{batch.pk}/source/')
            self.assertEqual(response.status_code,200)
            response.close()
    def test_reviewed_changes_publish_and_keep_source(self):
        confirm_import(self.batch(),self.user)
        group=StudentGroup.objects.get();room=Room.objects.get();teacher=Teacher.objects.get();subject=Subject.objects.get()
        batch=ImageImport.objects.create(target_date=date(2026,10,26),layout='table',uploaded_by=self.user)
        created=publish_review(batch,[{'group_id':group.pk,'slot':1,'kind':'replace','subject_id':subject.pk,'teacher_id':teacher.pk,'room_id':room.pk}],self.user)
        self.assertEqual(created[0].status,'published')
        self.assertEqual(svc.schedule_on(batch.target_date)[0]['status'],'changed')
    def test_targeted_change_preserves_joint_participants(self):
        version=confirm_import(self.batch(),self.user)
        group=StudentGroup.objects.get();other=StudentGroup.objects.create(name='ДРУГ-1-23',building=group.building,admission_year=2023,graduation_year=2027)
        GroupYear.objects.create(group=other,year=version.period.year,course=4)
        version.groups.add(other)
        for lesson in version.lessons.all():Participant.objects.create(lesson=lesson,group=other)
        room=Room.objects.create(number='209',building=group.building,room_type=RoomType.objects.get())
        batch=ImageImport.objects.create(target_date=date(2026,10,26),layout='table',uploaded_by=self.user)
        old=version.lessons.first()
        # Cancellation affects only the selected group, leaving the joint participant intact.
        publish_review(batch,[{'group_id':group.pk,'slot':1,'kind':'cancel'}],self.user)
        rows=svc.schedule_on(batch.target_date)
        self.assertEqual(len(rows),2)
        self.assertEqual(next(r for r in rows if svc.live(r))['groups'][0]['id'],other.pk)
