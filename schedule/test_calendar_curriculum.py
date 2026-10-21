from datetime import date, time, timedelta
from unittest.mock import patch
from PIL import Image
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from . import tests as fixtures
from . import services as svc
from .models import Period, CalendarDay, WeekdayBell, Subject, Teacher, ScheduleVersion, ImageImport
from .timing import week_info, bells_for
from .curriculum import curriculum_for
from .image_import import resolve_candidates, suggestion, analyze
from .admin import ImageRowForm


class CalendarCurriculumTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def test_numbered_weeks_alternate_at_monday_and_across_new_year(self):
        self.assertEqual(week_info(date(2026,10,21))['number'],2)
        self.assertEqual(week_info(date(2026,10,25))['number'],2)
        self.assertEqual(week_info(date(2026,10,26))['number'],1)
        self.assertNotEqual(week_info(date(2026,12,28))['number'],week_info(date(2027,1,4))['number'])

    def test_explicit_reference_controls_schedule_and_metadata(self):
        self.year.week_anchor_date=self.day
        self.year.week_anchor_number=1
        self.year.save()
        self.lesson.week_type='odd';self.lesson.save()
        self.assertEqual(len(svc.schedule_on(self.day)),1)
        self.assertEqual(svc.schedule_on(self.day+timedelta(days=7)),[])
        data=self.client.get('/api/meta',{'date':str(self.day)}).json()
        self.assertEqual(data['week']['number'],1)
        self.assertEqual(data['week']['anchor_date'],str(self.day))
        self.assertIn('current_week',data)

    def test_reference_requires_monday(self):
        self.year.week_anchor_date=self.day+timedelta(days=1)
        with self.assertRaises(ValidationError):self.year.full_clean()

    def test_day_exception_does_not_shift_following_weeks(self):
        CalendarDay.objects.create(year=self.year,date=self.day,week_type='odd')
        self.assertEqual(week_info(self.day)['number'],1)
        self.assertTrue(week_info(self.day)['overridden'])
        self.assertEqual(week_info(self.day+timedelta(days=1))['number'],2)
        self.assertEqual(week_info(self.day+timedelta(days=7))['number'],1)

    def test_no_year_has_no_guessed_week_number(self):
        self.assertIsNone(week_info(date(2030,1,1))['number'])

    def monday_bells(self):
        return WeekdayBell.objects.create(day_of_week=0,time_slot=self.slot,start_time=time(9),end_time=time(10))

    def test_weekday_bells_apply_to_lessons_and_fallback_other_days(self):
        self.monday_bells()
        self.make_lesson(day_of_week=1)
        self.assertEqual(svc.schedule_on(self.day)[0]['start'],'09:00')
        self.assertEqual(svc.schedule_on(self.day+timedelta(days=1))[0]['start'],'08:30')
        self.assertEqual(self.client.get('/api/bells',{'date':str(self.day),'weekday':1}).json()['slots'][0]['start'],'08:30')
        self.assertEqual(self.client.get('/api/bells',{'weekday':7}).status_code,400)

    def test_explicit_lesson_time_takes_priority(self):
        self.monday_bells()
        self.lesson.start_time=time(8);self.lesson.end_time=time(9);self.lesson.save()
        self.assertEqual(svc.schedule_on(self.day)[0]['start'],'08:00')

    def test_replacement_slot_uses_day_bells(self):
        self.monday_bells()
        change=self.change(time_slot=self.slot)
        self.assertEqual(svc.schedule_on(self.day,preview=change)[0]['start'],'09:00')

    def test_move_uses_destination_bells_without_explicit_slot(self):
        self.monday_bells()
        change=self.change(kind='move',new_date=self.day+timedelta(days=1))
        self.assertEqual(svc.schedule_on(change.new_date,preview=change)[0]['start'],'08:30')

    def test_free_intervals_use_day_bounds(self):
        self.monday_bells()
        self.assertEqual(svc.free_intervals([],self.day)[0]['start'],'09:00')
        self.assertEqual(svc.free_intervals(svc.schedule_on(self.day),self.day)[0]['start'],'10:00')

    def test_availability_grid_meta_and_bells_agree(self):
        self.monday_bells()
        data=self.client.get('/api/rooms/availability',{'date':str(self.day),'slot':1,'grid':1}).json()
        self.assertEqual(data['start'],'09:00')
        room=next(r for r in data['results'] if r['id']==self.room.pk)
        self.assertEqual(room['grid'][0]['status'],'busy')
        meta=self.client.get('/api/meta',{'date':str(self.day)}).json()
        self.assertEqual(meta['slots'],bells_for(self.day))
        self.assertEqual(self.client.get('/api/bells',{'date':str(self.day)}).json()['slots'],meta['slots'])

    def test_invalid_bell_interval_is_rejected_and_override_is_unique(self):
        bell=self.monday_bells()
        bell.end_time=time(8)
        with self.assertRaises(ValidationError):bell.full_clean()
        duplicate=WeekdayBell(day_of_week=0,time_slot=self.slot,start_time=time(9),end_time=time(10))
        with self.assertRaises(ValidationError):duplicate.full_clean()

    def test_monday_before_file_start_explains_range_and_links_next_monday(self):
        self.version.effective_from=date(2026,10,20);self.version.save()
        data=self.client.get(f'/api/groups/{self.group.pk}/schedule',{'date':str(self.day),'view':'week'}).json()
        monday=data['days'][0]
        self.assertEqual(monday['lessons'],[])
        self.assertFalse(monday['has_data'])
        self.assertIn('20.10.2026',monday['empty_reason'])
        self.assertEqual(monday['next_date'],'2026-10-26')
        valid=self.client.get(f'/api/groups/{self.group.pk}/schedule',{'date':monday['next_date']}).json()['days'][0]
        self.assertTrue(valid['has_data'])
        self.assertEqual(len(valid['lessons']),1)

    def test_coverage_is_specific_to_group(self):
        data=self.client.get(f'/api/groups/{self.group2.pk}/schedule',{'date':str(self.day)}).json()
        self.assertFalse(data['days'][0]['has_data'])

    def group_subjects(self):
        subject=Subject.objects.create(name='Основы менеджмента')
        teacher=Teacher.objects.create(name='Павлов С.С.')
        self.make_lesson(subject=subject,teacher=teacher,week_type='even',time_slot=self.slot2)
        return subject,teacher

    def test_curriculum_contains_both_weeks_and_group_teachers(self):
        subject,teacher=self.group_subjects()
        self.make_lesson(subject=Subject.objects.create(name='Чужая дисциплина'),teacher=self.teacher2,group=self.group2)
        data=curriculum_for(self.group,self.day)
        self.assertEqual({x['id'] for x in data['subjects']},{subject.pk,self.subject.pk})
        self.assertEqual({x['id'] for x in data['teachers']},{teacher.pk,self.teacher.pk})
        item=next(x for x in data['subjects'] if x['id']==subject.pk)
        self.assertEqual(item['weeks'],[2])
        self.assertEqual(item['teachers'],[{'id':teacher.pk,'name':teacher.name}])

    def test_subject_api_filters_by_period_and_search(self):
        subject,teacher=self.group_subjects()
        data=self.client.get('/api/subjects',{'group':self.group.pk,'date':str(self.day),'period':self.period.pk,'q':'менедж'}).json()
        self.assertEqual([x['id'] for x in data['subjects']],[subject.pk])
        self.assertEqual(data['period']['id'],self.period.pk)
        self.assertEqual(self.client.get('/subjects').status_code,200)
        self.assertEqual(self.client.get('/api/subjects',{'group':999999}).status_code,404)

    def test_future_snapshot_dictionary_is_available_before_file_start(self):
        self.version.effective_from=self.day+timedelta(days=1);self.version.save()
        self.assertEqual(curriculum_for(self.group,self.day)['subjects'][0]['id'],self.subject.pk)

    def test_semester_added_after_import_uses_overlapping_year_snapshot(self):
        semester=Period.objects.create(name='1 семестр',year=self.year,start_date=self.year.start_date,end_date=date(2026,12,31))
        data=curriculum_for(self.group,self.day)
        self.assertEqual(data['period']['id'],semester.pk)
        self.assertEqual(data['subjects'][0]['id'],self.subject.pk)
        spring=Period.objects.create(name='2 семестр',year=self.year,start_date=date(2027,2,1),end_date=self.year.end_date)
        self.assertEqual(curriculum_for(self.group,date(2027,2,1),spring)['subjects'],[])

    def test_complete_snapshot_removing_group_does_not_reuse_obsolete_dictionary(self):
        replacement=ScheduleVersion.objects.create(name='Новая общая',period=self.period,effective_from=self.day,effective_to=self.period.end_date,
            status='published',published_at=timezone.now())
        self.make_lesson(version=replacement,group=self.group2)
        self.assertEqual(curriculum_for(self.group,self.day)['subjects'],[])

    def test_selecting_future_semester_uses_its_snapshot_instead_of_today(self):
        self.period.end_date=self.year.end_date;self.period.save()
        self.version.effective_to=self.year.end_date;self.version.save()
        semester=Period.objects.create(name='2 семестр',year=self.year,start_date=date(2027,2,1),end_date=self.year.end_date)
        version=ScheduleVersion.objects.create(name='Весна',period=semester,effective_from=semester.start_date,effective_to=semester.end_date,
            status='published',published_at=timezone.now())
        version.groups.add(self.group)
        subject=Subject.objects.create(name='Весенняя дисциплина')
        self.make_lesson(version=version,subject=subject)
        self.assertEqual([x['id'] for x in curriculum_for(self.group,self.day,semester)['subjects']],[subject.pk])

    def test_superseded_snapshot_does_not_leak_obsolete_candidates(self):
        replacement=ScheduleVersion.objects.create(name='Новая',period=self.period,effective_from=self.day,effective_to=self.period.end_date,
            status='published',published_at=timezone.now())
        replacement.groups.add(self.group)
        subject=Subject.objects.create(name='Новая дисциплина')
        self.make_lesson(version=replacement,subject=subject)
        self.assertEqual([x['id'] for x in curriculum_for(self.group,self.day)['subjects']],[subject.pk])

    def test_abbreviations_choose_from_group_not_global_catalog(self):
        subject,teacher=self.group_subjects()
        foreign=Subject.objects.create(name='Осн менедж')
        self.make_lesson(subject=foreign,group=self.group2,teacher=self.teacher2)
        result=resolve_candidates(self.group.pk,self.day,'Осн менедж','Павлов С.С.')
        self.assertEqual(result,{'subject_id':subject.pk,'teacher_id':teacher.pk})
        self.assertIsNone(resolve_candidates(None,self.day,'Осн менедж','Павлов С.С.')['subject_id'])

    def test_image_analysis_resolves_group_before_subject_and_teacher(self):
        subject,teacher=self.group_subjects()
        self.make_lesson(subject=Subject.objects.create(name='Осн менедж'),group=self.group2)
        raw='Осн менедж Павлов С.С. 101'
        def cell(words,box):
            return self.group.name if box[0]==0 else raw if box[0]==10 else '-'
        with patch('schedule.image_import.table_edges',return_value=(list(range(0,81,10)),[0,10,30])), \
             patch('schedule.image_import.run_ocr',return_value=[]), \
             patch('schedule.image_import.in_box',side_effect=cell), \
             patch('schedule.image_import.recognize_boxes',return_value=[self.group.name,raw]):
            rows,warnings=analyze(Image.new('RGB',(100,40),'white'),'table',self.day)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['group_id'],self.group.pk)
        self.assertEqual(rows[0]['subject_id'],subject.pk)
        self.assertEqual(rows[0]['teacher_id'],teacher.pk)

    def test_image_review_suggestions_render_and_reviewed_form_publishes(self):
        subject,teacher=self.group_subjects()
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin,
            payload=[{'group_id':self.group.pk,'group_text':self.group.name,'subject_text':'Осн менедж',
                'teacher_text':'Павлов С.С.','raw':'Осн менедж Павлов С.С. 102','slot':1,'room_id':self.room2.pk}])
        self.client.force_login(self.admin)
        url=f'/admin/schedule/imageimport/{batch.pk}/review/'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'admin-image-review.js')
        self.assertEqual(response.context['formset'].forms[0].initial['subject'],subject.pk)
        response=self.client.post(url,{'form-TOTAL_FORMS':1,'form-INITIAL_FORMS':1,
            'form-0-use':'on','form-0-group':self.group.pk,'form-0-slot':1,'form-0-kind':'replace',
            'form-0-subject':subject.pk,'form-0-teacher':teacher.pk,'form-0-room':self.room2.pk})
        self.assertEqual(response.status_code,302)
        batch.refresh_from_db();self.assertEqual(batch.status,'published')
        self.assertEqual(svc.schedule_on(self.day)[0]['subject_id'],subject.pk)

    def test_ambiguous_abbreviations_and_unknown_teacher_require_review(self):
        self.group_subjects()
        self.make_lesson(subject=Subject.objects.create(name='Основы менеджмента организации'),time_slot=self.slot2)
        result=resolve_candidates(self.group.pk,self.day,'Осн менедж','Совсемдругой Н.Н.')
        self.assertIsNone(result['subject_id'])
        self.assertIsNone(result['teacher_id'])

    def test_module_codes_are_not_confused(self):
        subjects=[Subject.objects.create(name='МДК 01.01'),Subject.objects.create(name='МДК 01.02')]
        self.assertEqual(suggestion(Subject,'МДК01.01',subjects),subjects[0].pk)
        self.assertIsNone(suggestion(Subject,'МДК01.03',subjects))

    def test_review_form_choices_follow_bound_group_and_reject_foreign_subject(self):
        subject,teacher=self.group_subjects()
        foreign=Subject.objects.create(name='Чужая дисциплина')
        self.make_lesson(subject=foreign,group=self.group2,teacher=self.teacher2)
        form=ImageRowForm(initial={'group':self.group.pk},target_date=self.day)
        self.assertEqual(set(form.fields['subject'].queryset.values_list('pk',flat=True)),{subject.pk,self.subject.pk})
        form=ImageRowForm({'use':'on','group':self.group.pk,'slot':1,'kind':'replace','subject':foreign.pk,'teacher':teacher.pk,'room':self.room.pk},target_date=self.day)
        self.assertFalse(form.is_valid())
        self.assertIn('subject',form.errors)

    def test_staff_candidate_endpoint_scopes_dictionary_and_requires_login(self):
        subject,teacher=self.group_subjects()
        query={'group':self.group.pk,'date':str(self.day),'subject':'Осн менедж','teacher':'Павлов С.С.'}
        self.assertEqual(self.client.get('/api/admin/image-candidates',query).status_code,401)
        self.client.force_login(self.admin)
        data=self.client.get('/api/admin/image-candidates',query).json()
        self.assertEqual(data['suggestions']['subject_id'],subject.pk)
        self.assertEqual({x['id'] for x in data['teachers']},{self.teacher.pk,teacher.pk})
        self.client.force_login(self.dispatcher)
        self.group.building=self.other_building;self.group.save()
        self.assertEqual(self.client.get('/api/admin/image-candidates',query).status_code,403)

    def test_admin_dashboard_and_weekday_editor_are_available(self):
        self.client.force_login(self.admin)
        response=self.client.get('/admin/')
        self.assertContains(response,'Отсчёт недель')
        self.assertContains(response,'Звонки по дням')
        self.assertEqual(self.client.get('/admin/schedule/weekdaybell/').status_code,200)
