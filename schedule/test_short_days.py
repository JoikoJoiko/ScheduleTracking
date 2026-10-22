from datetime import date,time,timedelta
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError
from . import tests as fixtures,services as svc
from .models import ShortenedDay,ShortenedBell,TimeSlot,ImageImport,Change
from .timing import bells_for,short_day_info
from .image_import import publish_review


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ShortenedDaysTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def day_rule(self,**kwargs):
        values={'date':self.day,'lesson_minutes':40,'lesson_count':2}
        values.update(kwargs)
        rule=ShortenedDay(**values);rule.full_clean();rule.save()
        return rule

    def add_slots(self):
        for number in range(3,8):
            start=8*60+(number-1)*100
            TimeSlot.objects.create(name=f'{number} пара',sort_order=number,
                start_time=time(start//60,start%60),end_time=time((start+90)//60,(start+90)%60))

    def post_day(self,rule=None,**changes):
        slots=list(TimeSlot.objects.filter(sort_order__range=(1,7)))
        values={'action':'short_day','date':str(rule.date if rule else self.day),'lesson_minutes':40,'lesson_count':2,
                'start_time':'08:00','keep_breaks':'on','is_active':'on',
                'short-TOTAL_FORMS':len(slots),'short-INITIAL_FORMS':len(slots)}
        if rule:values['rule_id']=rule.pk
        for i,slot in enumerate(slots):values[f'short-{i}-slot']=slot.pk
        values.update(changes)
        return self.client.post('/admin/bells/',values)

    def test_different_dates_keep_independent_counts_durations_and_breaks(self):
        first=self.day_rule(lesson_count=1)
        second=self.day_rule(date=self.day+timedelta(days=7),lesson_count=2,lesson_minutes=30,start_time=time(9))
        ShortenedBell.objects.create(shortened_day=second,time_slot=self.slot,break_minutes=25)
        self.assertEqual([(b['start'],b['end']) for b in bells_for(first.date)],[('08:30','09:10')])
        self.assertEqual([(b['start'],b['end']) for b in bells_for(second.date)],[('09:00','09:30'),('09:55','10:25')])
        self.assertEqual(len(bells_for(self.day+timedelta(days=1))),2)

    def test_long_break_and_individual_duration_use_actual_sequence(self):
        self.add_slots()
        rule=self.day_rule(lesson_count=3,start_time=time(8))
        ShortenedBell.objects.create(shortened_day=rule,time_slot=self.slot,break_minutes=5)
        ShortenedBell.objects.create(shortened_day=rule,time_slot=self.slot2,break_minutes=30)
        third=TimeSlot.objects.get(sort_order=3)
        ShortenedBell.objects.create(shortened_day=rule,time_slot=third,lesson_minutes=30)
        self.assertEqual([(b['start'],b['end']) for b in bells_for(self.day)],
                         [('08:00','08:40'),('08:45','09:25'),('09:55','10:25')])
        info=short_day_info(self.day)
        self.assertEqual(info['lesson_count'],3);self.assertTrue(info['varied_duration'])
        self.assertNotIn('по 40',info['label'])

    def test_zero_break_is_preserved_and_last_pair_has_no_extra_break(self):
        rule=self.day_rule(start_time=time(8))
        ShortenedBell.objects.create(shortened_day=rule,time_slot=self.slot,break_minutes=0)
        ShortenedBell.objects.create(shortened_day=rule,time_slot=self.slot2,break_minutes=720)
        self.assertEqual([(b['start'],b['end']) for b in bells_for(self.day)],
                         [('08:00','08:40'),('08:40','09:20')])

    def test_fewer_pairs_hide_late_lessons_even_with_their_own_times(self):
        self.make_lesson(time_slot=self.slot2,start_time=time(9),end_time=time(10))
        rule=self.day_rule(lesson_count=1)
        self.assertEqual(len(svc.schedule_on(self.day)),1)
        result=self.client.get(f'/api/groups/{self.group.pk}/schedule',{'date':str(self.day)}).json()['days'][0]
        self.assertEqual([l['slot'] for l in result['lessons']],[1])
        self.assertEqual(result['shortened_day']['lesson_count'],1)
        self.assertEqual(self.client.get('/api/rooms/availability',{'date':str(self.day),'slot':2}).status_code,400)
        rule.is_active=False;rule.save()
        self.assertEqual(len(svc.schedule_on(self.day)),2)
        self.assertEqual(len(bells_for(self.day)),2)

    def test_short_day_count_rejects_out_of_range_values(self):
        for count in [0,8]:
            with self.assertRaises(ValidationError):self.day_rule(lesson_count=count)

    def test_change_and_photo_cannot_publish_an_excluded_pair(self):
        self.day_rule(lesson_count=1)
        change=self.change(original=None,kind='add',time_slot=self.slot2,group=self.group,
                           subject=self.subject,teacher=self.teacher,room=self.room2)
        with self.assertRaisesMessage(ValidationError,'только 1 пар'):svc.publish_change(change,self.admin)
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)
        with self.assertRaisesMessage(ValidationError,'только 1 пар'):
            publish_review(batch,[{'group_id':self.group.pk,'slot':2,'kind':'replace',
                'subject_id':self.subject.pk,'teacher_id':self.teacher.pk,'room_id':self.room.pk}],self.admin)
        self.assertEqual(Change.objects.filter(status='published').count(),0)

    def test_admin_saves_all_pair_settings_and_edits_them_as_one_scheme(self):
        self.client.force_login(self.admin)
        response=self.post_day(**{'short-0-break_minutes':20,'short-1-lesson_minutes':30})
        self.assertEqual(response.status_code,302)
        rule=ShortenedDay.objects.get()
        self.assertEqual(rule.bells.count(),2)
        self.assertEqual([(b['start'],b['end']) for b in bells_for(self.day)],
                         [('08:00','08:40'),('09:00','09:30')])
        response=self.post_day(rule,**{'short-0-break_minutes':5})
        self.assertEqual(response.status_code,302)
        self.assertEqual(rule.bells.count(),1)
        self.assertEqual(bells_for(self.day)[1]['end'],'09:25')

    def test_invalid_pair_rows_never_partially_save_parent_or_children(self):
        self.client.force_login(self.admin)
        rule=self.day_rule()
        ShortenedBell.objects.create(shortened_day=rule,time_slot=self.slot,break_minutes=15)
        for changes in [{'short-0-break_minutes':-1},{'short-1-slot':self.slot.pk},
                        {'short-TOTAL_FORMS':0},{'short-0-break_minutes':720,'start_time':'22:00'}]:
            self.assertEqual(self.post_day(rule,**changes).status_code,200)
            rule.refresh_from_db()
            self.assertIsNone(rule.start_time)
            self.assertEqual(rule.bells.get().break_minutes,15)

    def test_pending_rows_can_remove_a_previously_long_break_before_validation(self):
        self.client.force_login(self.admin)
        rule=self.day_rule(start_time=time(6))
        ShortenedBell.objects.create(shortened_day=rule,time_slot=self.slot,break_minutes=720)
        self.assertEqual(self.post_day(rule,start_time='21:00',**{'short-0-break_minutes':0}).status_code,302)
        self.assertEqual(bells_for(self.day)[1]['end'],'22:20')

    def test_multiple_days_and_copy_controls_are_visible_and_copy_requires_new_date(self):
        self.client.force_login(self.admin)
        first=self.day_rule()
        self.day_rule(date=self.day+timedelta(days=1),lesson_count=1)
        ShortenedBell.objects.create(shortened_day=first,time_slot=self.slot,break_minutes=20)
        home=self.client.get('/admin/bells/')
        self.assertContains(home,'19.10.2026');self.assertContains(home,'20.10.2026')
        self.assertContains(home,'Копировать на другую дату',count=2)
        copy=self.client.get(f'/admin/bells/?copy={first.pk}')
        self.assertFalse(copy.context['short_form'].instance.pk)
        self.assertIsNone(copy.context['short_form']['date'].value())
        self.assertEqual(copy.context['short_form']['lesson_count'].value(),2)
        self.assertEqual(copy.context['short_set'].forms[0]['break_minutes'].value(),20)
        response=self.post_day(date=str(self.day+timedelta(days=2)),**{'short-0-break_minutes':20})
        self.assertEqual(response.status_code,302)
        self.assertEqual(ShortenedDay.objects.count(),3)
        first.refresh_from_db();self.assertEqual(first.date,self.day)
        self.assertEqual(first.bells.get().break_minutes,20)

    def test_admin_limited_to_four_pairs_saves_only_active_overrides(self):
        self.add_slots();self.client.force_login(self.admin)
        response=self.post_day(lesson_count=4,**{'short-2-break_minutes':25,'short-6-break_minutes':30})
        self.assertEqual(response.status_code,302)
        rule=ShortenedDay.objects.get()
        self.assertEqual(rule.lesson_count,4)
        self.assertEqual([b['number'] for b in bells_for(self.day)],[1,2,3,4])
        self.assertEqual(list(rule.bells.values_list('time_slot__sort_order',flat=True)),[3])

    def test_last_slot_and_class_hour_validation_and_uniform_legacy_mode(self):
        zero=TimeSlot.objects.create(name='Классный час',sort_order=0)
        rule=self.day_rule(lesson_count=None)
        row=ShortenedBell(shortened_day=rule,time_slot=zero,lesson_minutes=30)
        with self.assertRaises(ValidationError):row.full_clean()
        self.assertEqual(len(bells_for(self.day)),2)
        self.assertEqual(short_day_info(self.day)['label'],'Сокращённый день · 2 пары по 40 мин')
