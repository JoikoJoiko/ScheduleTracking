from datetime import date,time,timedelta
from django.test import TestCase,Client,override_settings
from django.core.exceptions import ValidationError
from django.conf import settings
from . import tests as fixtures,services as svc
from .models import ShortenedDay,PracticePeriod,TimeSlot,WeekdayBell,Subject,Participant,AuditLog
from .timing import bells_for
from .curriculum import curriculum_for


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OperationsTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def short_day(self,**kwargs):
        values={'date':self.day,'lesson_minutes':45}
        values.update(kwargs)
        rule=ShortenedDay(**values);rule.full_clean();rule.save()
        return rule

    def practice(self,**kwargs):
        values={'group':self.group,'start_date':self.day,'end_date':self.day+timedelta(days=7)}
        values.update(kwargs)
        row=PracticePeriod(**values);row.full_clean();row.save()
        return row

    def test_short_day_preserves_weekday_breaks(self):
        WeekdayBell.objects.create(day_of_week=0,time_slot=self.slot,start_time=time(9),end_time=time(10))
        self.short_day()
        bells=bells_for(self.day)
        self.assertEqual([(b['start'],b['end']) for b in bells],[('09:00','09:45'),('09:55','10:40')])
        self.assertEqual(svc.schedule_on(self.day)[0]['start'],'09:00')
        self.assertEqual(svc.schedule_on(self.day)[0]['end'],'09:45')
        meta=self.client.get('/api/meta',{'date':str(self.day)}).json()
        self.assertEqual(meta['shortened_day']['minutes'],45)
        self.assertEqual(meta['slots'],bells)
        room=self.client.get('/api/rooms/availability',{'date':str(self.day),'grid':1,'slot':1}).json()
        self.assertEqual(room['end'],'09:45')

    def test_short_day_custom_start_breaks_and_inactive_fallback(self):
        rule=self.short_day(start_time=time(9,15),keep_breaks=False,break_minutes=5)
        self.assertEqual([(b['start'],b['end']) for b in bells_for(self.day)],[('09:15','10:00'),('10:05','10:50')])
        self.assertEqual(bells_for(self.day+timedelta(days=1))[0]['start'],'08:30')
        rule.is_active=False;rule.save()
        self.assertEqual(bells_for(self.day)[0]['end'],'10:00')

    def test_short_day_never_overrides_explicit_lesson_time_or_slot_zero(self):
        TimeSlot.objects.create(name='Классный час',sort_order=0)
        self.lesson.start_time=time(7);self.lesson.end_time=time(8);self.lesson.save()
        self.short_day(start_time=time(9))
        self.assertEqual(svc.schedule_on(self.day)[0]['start'],'07:00')
        self.assertEqual(len(bells_for(self.day)),2)

    def test_short_day_requires_parameters_and_rejects_overnight(self):
        for values in [{'lesson_minutes':0},{'lesson_minutes':45,'keep_breaks':False},
                       {'lesson_minutes':90,'start_time':time(23)},{'lesson_minutes':45,'date':date(2026,10,25)}]:
            rule=ShortenedDay(date=values.pop('date',self.day),**values)
            with self.assertRaises(ValidationError):rule.full_clean()
        self.client.force_login(self.admin)
        response=self.client.get('/admin/bells/')
        self.assertIsNone(response.context['short_form']['lesson_minutes'].value())

    def test_breaks_are_not_windows(self):
        self.make_lesson(time_slot=self.slot2)
        lessons=svc.schedule_on(self.day)
        self.assertEqual(svc.lesson_windows(lessons,self.day),[])
        self.assertEqual(svc.free_intervals(lessons,self.day),[{'start':'10:00','end':'10:10'}])
        result=self.client.get(f'/api/teachers/{self.teacher.pk}/schedule',{'date':str(self.day)}).json()['days'][0]
        self.assertEqual(result['windows'],[])

    def test_only_full_missing_pairs_between_classes_are_windows(self):
        third=TimeSlot.objects.create(name='3 пара',sort_order=3,start_time=time(12),end_time=time(13,30))
        self.make_lesson(time_slot=third)
        self.assertEqual(svc.lesson_windows(svc.schedule_on(self.day),self.day),[{'slot':2,'start':'10:10','end':'11:40'}])
        self.assertEqual(svc.lesson_windows([svc.schedule_on(self.day)[0]],self.day),[])

    def test_cancellation_creates_a_window_but_not_break_padding(self):
        third=TimeSlot.objects.create(name='3 пара',sort_order=3,start_time=time(12),end_time=time(13,30))
        middle=self.make_lesson(time_slot=self.slot2);self.make_lesson(time_slot=third)
        change=self.change(original=middle,kind='cancel');svc.publish_change(change,self.admin)
        self.assertEqual(svc.lesson_windows(svc.schedule_on(self.day),self.day),[{'slot':2,'start':'10:10','end':'11:40'}])

    def test_practice_dates_are_inclusive_and_regular_schedule_returns(self):
        self.practice()
        before=self.client.get(f'/api/groups/{self.group.pk}/schedule',{'date':str(self.day-timedelta(days=7))}).json()['days'][0]
        self.assertIsNone(before['practice']);self.assertEqual(len(before['lessons']),1)
        for day in [self.day,self.day+timedelta(days=7)]:
            row=self.client.get(f'/api/groups/{self.group.pk}/schedule',{'date':str(day)}).json()['days'][0]
            self.assertEqual(row['lessons'],[])
            self.assertEqual(row['practice']['label'],'Практика до 26.10.2026')
            self.assertTrue(row['has_data'])
        self.assertEqual(len(svc.schedule_on(self.day+timedelta(days=14))),1)
        self.assertEqual(svc.availability(self.day,time(8,30),time(10),[self.room])[0]['status'],'free')
        teachers=self.client.get(f'/api/teachers/{self.teacher.pk}/schedule',{'date':str(self.day)}).json()['days'][0]
        self.assertEqual(teachers['lessons'],[])

    def test_practice_keeps_other_groups_in_joint_lessons(self):
        Participant.objects.create(lesson=self.lesson,group=self.group2)
        self.practice()
        self.assertEqual([g['id'] for g in svc.schedule_on(self.day)[0]['groups']],[self.group2.pk])
        self.assertEqual(svc.availability(self.day,time(8,30),time(10),[self.room])[0]['status'],'busy')

    def test_overlapping_practice_or_reversed_dates_are_rejected(self):
        self.practice()
        with self.assertRaises(ValidationError):self.practice(start_date=self.day+timedelta(days=7))
        with self.assertRaises(ValidationError):self.practice(start_date=self.day+timedelta(days=9))
        self.practice(start_date=self.day+timedelta(days=8),end_date=self.day+timedelta(days=9))

    def test_class_hours_remain_in_timetable_and_leave_curriculum(self):
        subject=Subject.objects.create(name='Разговоры о важном',is_curricular=False)
        self.make_lesson(subject=subject,time_slot=self.slot2)
        self.assertEqual([s['id'] for s in curriculum_for(self.group,self.day)['subjects']],[self.subject.pk])
        row=next(l for l in svc.schedule_on(self.day) if l['subject_id']==subject.pk)
        self.assertEqual(row['event_kind'],'class_hour')

    def test_subgroup_is_exposed_and_week_has_only_six_teaching_days(self):
        Participant.objects.filter(lesson=self.lesson).update(subgroup=self.sub1)
        result=self.client.get(f'/api/groups/{self.group.pk}/schedule',{'date':str(self.day),'view':'week'}).json()
        self.assertEqual(len(result['days']),6)
        self.assertEqual(result['days'][-1]['date'],'2026-10-24')
        self.assertEqual(result['days'][0]['lessons'][0]['groups'][0]['subgroup'],'1')
        self.assertEqual(self.client.get(f'/api/groups/{self.group.pk}/schedule',{'date':'2026-10-25'}).json()['days'],[])

    def bell_post(self,**changes):
        values={'action':'weekday','weekday':0,'form-TOTAL_FORMS':2,'form-INITIAL_FORMS':2,
                'form-0-slot':self.slot.pk,'form-0-override':'on','form-0-start':'09:00','form-0-end':'10:00',
                'form-1-slot':self.slot2.pk,'form-1-override':'on','form-1-start':'10:15','form-1-end':'11:15'}
        values.update(changes)
        return self.client.post('/admin/bells/',values)

    def test_bells_editor_saves_only_selected_weekday_and_resets_to_default(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.bell_post().status_code,302)
        self.assertEqual(WeekdayBell.objects.count(),2)
        self.assertEqual(bells_for(self.day)[0]['start'],'09:00')
        self.assertEqual(bells_for(self.day+timedelta(days=1))[0]['start'],'08:30')
        self.assertEqual(self.bell_post(**{'form-0-override':'','form-1-override':''}).status_code,302)
        self.assertEqual(WeekdayBell.objects.count(),0)
        self.assertTrue(AuditLog.objects.filter(action='reset_bells').exists())

    def test_bells_editor_rejects_overlap_and_tampered_formsets_atomically(self):
        self.client.force_login(self.admin)
        for changes in [{'form-1-start':'09:30'},{'form-0-slot':self.slot2.pk},
                        {'form-TOTAL_FORMS':1,'form-INITIAL_FORMS':0,'form-0-slot':''}]:
            self.assertEqual(self.bell_post(**changes).status_code,200)
            self.assertFalse(WeekdayBell.objects.exists())

    def test_operations_require_root_and_keep_csrf_protection(self):
        for path in ['/admin/bells/','/admin/practices/','/admin/advanced/']:
            self.assertEqual(self.client.get(path).status_code,302)
            self.client.force_login(self.dispatcher)
            self.assertEqual(self.client.get(path).status_code,403)
            self.client.logout()
        client=Client(enforce_csrf_checks=True);client.force_login(self.admin)
        self.assertEqual(client.post('/admin/bells/',{'action':'short_day'}).status_code,403)
        self.assertEqual(client.post('/admin/practices/',{}).status_code,403)

    def test_short_day_can_be_created_edited_and_disabled_in_own_admin_block(self):
        self.client.force_login(self.admin)
        values={'action':'short_day','date':str(self.day),'lesson_minutes':40,'start_time':'09:10',
                'keep_breaks':'on','is_active':'on'}
        self.assertEqual(self.client.post('/admin/bells/',values).status_code,302)
        rule=ShortenedDay.objects.get()
        self.assertEqual(self.client.get(f'/admin/bells/?short={rule.pk}').context['rule'],rule)
        self.assertEqual(self.client.get(f'/admin/bells/?short={rule.pk}').context['short_form']['date'].value(),self.day)
        invalid={**values,'rule_id':rule.pk,'start_time':'23:30'}
        self.assertEqual(self.client.post('/admin/bells/',invalid).status_code,200)
        rule.refresh_from_db();self.assertEqual(rule.start_time,time(9,10))
        values.update(rule_id=rule.pk,lesson_minutes=35)
        self.assertEqual(self.client.post('/admin/bells/',values).status_code,302)
        self.assertEqual(ShortenedDay.objects.get().lesson_minutes,35)
        self.assertEqual(self.client.post('/admin/bells/',{'action':'disable_short','rule_id':rule.pk}).status_code,302)
        self.assertFalse(ShortenedDay.objects.get().is_active)

    def test_practice_admin_form_and_cancellation(self):
        self.client.force_login(self.admin)
        response=self.client.post('/admin/practices/',{'group':self.group.pk,'start_date':str(self.day),
            'end_date':str(self.day+timedelta(days=2)),'title':'Практика','is_active':'on'})
        self.assertEqual(response.status_code,302)
        record=PracticePeriod.objects.get()
        self.assertEqual(self.client.get(f'/admin/practices/?edit={record.pk}').context['form']['start_date'].value(),self.day)
        self.assertEqual(svc.schedule_on(self.day),[])
        self.assertEqual(self.client.post('/admin/practices/',{'action':'disable','record_id':record.pk}).status_code,302)
        self.assertEqual(len(svc.schedule_on(self.day)),1)

    def test_version_and_advanced_settings_are_separate_from_upload_dashboard(self):
        self.assertContains(self.client.get('/'),f'v{settings.SITE_VERSION}')
        self.client.force_login(self.admin)
        home=self.client.get('/admin/')
        self.assertContains(home,'Дни недели и сокращение звонков')
        self.assertContains(home,'Добавить или изменить практику')
        self.assertNotContains(home,'dashboard-directories')
        self.assertContains(self.client.get('/admin/advanced/'),'Пользователи')
