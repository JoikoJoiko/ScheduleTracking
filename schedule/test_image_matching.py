from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image,ImageDraw
from django.apps import apps
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError
from . import tests as fixtures
from .models import Subject,StudentGroup,TimeSlot,ImageImport,PracticePeriod,Change
from . import services as svc
from .image_import import infer_row,decode_cell,split_cell,analyze,suggestion,publish_review


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ImageMatchingTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson

    def test_damaged_abbreviation_uses_group_subject_and_its_teacher(self):
        subject=Subject.objects.create(name='Основы менеджмента и планирование профессиональной деятельности')
        self.make_lesson(subject=subject,teacher=self.teacher2,time_slot=self.slot2)
        result=infer_row(self.group.pk,self.day,{'subject_text':'Осн менехэ','slot':2,'teacher_text':'Смирнов П.И.'})
        self.assertEqual(result['subject_id'],subject.pk)
        self.assertEqual(result['teacher_id'],self.teacher2.pk)
        self.assertEqual(result['room_id'],self.room.pk)

    def test_unreadable_teacher_and_room_receive_flagged_context_proposals(self):
        result=infer_row(self.group.pk,self.day,{'subject_text':'Базы дан','teacher_text':'','slot':1})
        self.assertEqual(result['subject_id'],self.subject.pk)
        self.assertEqual(result['teacher_id'],self.teacher.pk)
        self.assertEqual(result['room_id'],self.room.pk)
        self.assertTrue(result['inferred'])
        self.assertLessEqual(result['confidence'],55)
        self.assertTrue(any('Кабинет' in note for note in result['inferences']))

    def test_unreadable_subject_prefers_recognized_teachers_discipline(self):
        subject=Subject.objects.create(name='Основы черчения')
        self.make_lesson(subject=subject,teacher=self.teacher2,time_slot=self.slot2)
        result=infer_row(self.group.pk,self.day,{'subject_text':'Инж грф','teacher_text':'Смирнов П.И.','slot':1})
        self.assertEqual(result['subject_id'],subject.pk)
        self.assertEqual(result['teacher_id'],self.teacher2.pk)
        self.assertTrue(result['inferred'])

    def test_ambiguous_subjects_still_propose_best_option_and_flag_review(self):
        self.make_lesson(subject=Subject.objects.create(name='Базы данных и SQL'),time_slot=self.slot2)
        result=infer_row(self.group.pk,self.day,{'subject_text':'Базы дан','slot':1})
        self.assertEqual(result['subject_id'],self.subject.pk)
        self.assertTrue(result['inferred'])

    def test_unknown_group_does_not_become_a_different_groups_timetable(self):
        result=infer_row(None,self.day,{'subject_text':self.subject.name,'teacher_text':self.teacher.name})
        self.assertIsNone(result['subject_id']);self.assertIsNone(result['teacher_id'])
        self.assertIsNone(suggestion(StudentGroup,'ИС-1-24'))

    def test_group_ocr_corrections_keep_explicit_group_number_and_admission_year(self):
        group=StudentGroup.objects.create(name='ГД-1-25',building=self.building,admission_year=2025,graduation_year=2029)
        self.assertEqual(suggestion(StudentGroup,'ГД-1-2Б'),group.pk)
        self.assertIsNone(suggestion(StudentGroup,'ГД-7-25'))
        self.assertIsNone(suggestion(StudentGroup,'ГД-1-24'))

    def test_nonmatching_global_catalog_is_never_used(self):
        outsider=Subject.objects.create(name='Совершенно чужая дисциплина')
        result=infer_row(self.group.pk,self.day,{'subject_text':outsider.name,'slot':1})
        self.assertEqual(result['subject_id'],self.subject.pk)
        self.assertTrue(result['inferred'])

    def test_fuzzy_physical_education_and_multiline_room_before_teacher(self):
        subject=Subject.objects.create(name='Физическая культура')
        self.make_lesson(subject=subject,teacher=self.teacher2,time_slot=self.slot2)
        row=decode_cell('ФЕ3-ра 102\nСмирнов П.И.')
        self.assertEqual(row['room_text'],'102')
        self.assertEqual(row['teacher_text'],'Смирнов П.И.')
        result=infer_row(self.group.pk,self.day,{**row,'slot':2})
        self.assertEqual(result['subject_id'],subject.pk)
        self.assertEqual(result['room_id'],self.room2.pk)

    def test_geometry_detects_subgroup_split_and_selects_matching_dictionary_entry(self):
        image=Image.new('RGB',(100,50),'white');ImageDraw.Draw(image).line((50,0,50,49),fill='black')
        self.assertEqual(len(split_cell(image,(0,0,100,50))),2)
        subject1=Subject.objects.create(name='Базы данных, п/г 1')
        subject2=Subject.objects.create(name='Базы данных, п/г 2')
        self.make_lesson(subject=subject1,subgroup=self.sub1)
        self.make_lesson(subject=subject2,subgroup=self.sub2,teacher=self.teacher2,room=self.room2)
        result=infer_row(self.group.pk,self.day,{'subject_text':'Базы дан','subgroup_number':2,'slot':1})
        self.assertEqual(result['subgroup_id'],self.sub2.pk)
        self.assertNotEqual(result['subject_id'],subject1.pk)

    def test_markers_and_room_only_cells_never_cancel_or_invent_pairs(self):
        for value in ['−','По расп 216п','Ло рзсл','216п','101п6','По расписанию']:
            self.assertIsNone(decode_cell(value),value)

    def test_data_migration_marks_slot_zero_and_misspelled_class_hours(self):
        zero=TimeSlot.objects.create(name='Классный час',sort_order=0)
        event=Subject.objects.create(name='Разговоры о важнос')
        other=Subject.objects.create(name='Час общения')
        self.make_lesson(subject=other,time_slot=zero)
        migration=import_module('schedule.migrations.0006_shortenedday_subject_is_curricular_practiceperiod')
        migration.mark_class_hours(apps,SimpleNamespace(connection=SimpleNamespace(alias='default')))
        event.refresh_from_db();other.refresh_from_db();self.subject.refresh_from_db()
        self.assertFalse(event.is_curricular);self.assertFalse(other.is_curricular)
        self.assertTrue(self.subject.is_curricular)

    def test_yellow_layout_detects_colored_cells_and_keeps_pair_number(self):
        image=Image.new('RGB',(240,140),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((35,58,65,66),fill='#fff2cc')
        xs=[0,10,30,70,110,150,190,230];ys=[10,25,40,55,70,85,100,115,130]
        def cell(words,box):
            return self.group.name if box[1]==25 else 'Базы дан Пигачева А.С. 101'
        with patch('schedule.image_import.table_edges',return_value=(xs,ys)), \
             patch('schedule.image_import.run_ocr',return_value=[]), \
             patch('schedule.image_import.in_box',side_effect=cell), \
             patch('schedule.image_import.recognize_boxes',return_value=[self.group.name,'Базы дан Пигачева А.С. 101']):
            rows,_=analyze(image,'yellow',self.day)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['slot'],2)
        self.assertEqual(rows[0]['group_id'],self.group.pk)

    def test_inferred_fields_render_without_automatically_selecting_publication(self):
        row={'group_id':self.group.pk,'slot':1,'subject_text':'Базы дан','raw':'Базы дан',
             'inferred':True,'confidence':50,'inferences':['Кабинет: из расписания']}
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin,payload=[row])
        self.client.force_login(self.admin)
        response=self.client.get(f'/admin/schedule/imageimport/{batch.pk}/review/')
        self.assertContains(response,'Подобрано · оценка 50%')
        self.assertFalse(response.context['formset'].forms[0]['use'].value())
        self.assertFalse(Change.objects.exists())

    def test_image_publication_rejects_practice_date(self):
        PracticePeriod.objects.create(group=self.group,start_date=self.day,end_date=self.day+timedelta(days=2))
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)
        with self.assertRaisesMessage(ValidationError,'практика'):
            publish_review(batch,[{'group_id':self.group.pk,'slot':1,'kind':'replace'}],self.admin)
        self.assertFalse(Change.objects.exists())

    def test_replacement_for_one_subgroup_keeps_the_other_original_pair(self):
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)
        publish_review(batch,[{'group_id':self.group.pk,'slot':1,'subgroup_id':self.sub1.pk,'kind':'replace',
            'subject_id':self.subject.pk,'teacher_id':self.teacher2.pk,'room_id':self.room2.pk}],self.admin)
        rows=svc.schedule_on(self.day)
        self.assertEqual(len(rows),2)
        original=next(l for l in rows if l['status']=='regular')
        changed=next(l for l in rows if l['status']=='changed')
        self.assertEqual(original['groups'][0]['subgroup_id'],self.sub2.pk)
        self.assertEqual(original['room_id'],self.room.pk)
        self.assertEqual(changed['groups'][0]['subgroup_id'],self.sub1.pk)

    def test_two_subgroup_changes_in_one_batch_both_survive_publication(self):
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)
        rows=[{'group_id':self.group.pk,'slot':1,'subgroup_id':subgroup.pk,'kind':'replace',
               'subject_id':self.subject.pk,'teacher_id':teacher.pk,'room_id':room.pk}
              for subgroup,teacher,room in [(self.sub1,self.teacher,self.room),(self.sub2,self.teacher2,self.room2)]]
        publish_review(batch,rows,self.admin)
        published=svc.schedule_on(self.day)
        self.assertEqual(len(published),2)
        self.assertEqual({l['groups'][0]['subgroup_id'] for l in published},{self.sub1.pk,self.sub2.pk})
        self.assertTrue(all(l['status']=='changed' for l in published))

    def test_conflicting_subgroup_batch_rolls_back_all_changes(self):
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)
        rows=[{'group_id':self.group.pk,'slot':1,'subgroup_id':subgroup.pk,'kind':'replace',
               'subject_id':self.subject.pk,'teacher_id':self.teacher.pk,'room_id':self.room.pk}
              for subgroup in [self.sub1,self.sub2]]
        with self.assertRaises(ValidationError):publish_review(batch,rows,self.admin)
        self.assertFalse(Change.objects.exists())
        batch.refresh_from_db();self.assertEqual(batch.status,'review')
