from datetime import date,time
from django.test import TestCase,override_settings
from . import tests as fixtures
from .models import Room,Subject,CalendarDay,ImageImport,Change,TimeSlot
from .image_import import infer_row,publish_review


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class WeeklyRoomMatchingTests(TestCase):
    make_lesson=fixtures.ScheduleTests.make_lesson

    def setUp(self):
        fixtures.ScheduleTests.setUp(self)
        self.lesson.day_of_week=3;self.lesson.week_type='odd';self.lesson.save()
        self.even_subject=Subject.objects.create(name='Информационные системы')
        self.even=self.make_lesson(day_of_week=3,week_type='even',subject=self.even_subject,teacher=self.teacher2,room=self.room2)
        self.thursday=date(2026,10,22)

    def create_room(self,number):
        return Room.objects.create(number=number,building=self.building,room_type=self.type)

    def test_date_of_image_selects_second_week_and_next_thursday_selects_first(self):
        for day,week,room,subject,teacher in [(self.thursday,2,self.room2,self.even_subject,self.teacher2),
                (date(2026,10,29),1,self.room,self.subject,self.teacher)]:
            result=infer_row(self.group.pk,day,{'slot':1})
            self.assertEqual(result['week']['number'],week)
            self.assertEqual(result['room_id'],room.pk)
            self.assertEqual(result['subject_id'],subject.pk)
            self.assertEqual(result['teacher_id'],teacher.pk)

    def test_image_publication_replaces_original_of_target_week_not_current_or_other_week(self):
        for day,original in [(self.thursday,self.even),(date(2026,10,29),self.lesson)]:
            batch=ImageImport.objects.create(target_date=day,layout='table',uploaded_by=self.admin)
            row={'group_id':self.group.pk,'slot':1,'kind':'replace','subject_id':original.subject_id,
                 'teacher_id':original.teacher_id,'room_id':original.room_id}
            change=publish_review(batch,[row],self.admin)[0]
            self.assertEqual(change.original_id,original.pk)
        self.assertEqual(Change.objects.count(),2)

    def test_calendar_week_override_also_controls_room_context(self):
        CalendarDay.objects.create(year=self.year,date=self.thursday,week_type='odd')
        result=infer_row(self.group.pk,self.thursday,{'slot':1})
        self.assertEqual(result['week']['number'],1)
        self.assertEqual(result['room_id'],self.room.pk)

    def test_clear_room_number_is_not_fuzzily_replaced_by_another_number(self):
        far=self.create_room('411')
        result=infer_row(self.group.pk,self.thursday,{'slot':1,'room_text':'11'})
        self.assertEqual(result['room_id'],self.room2.pk)
        self.assertNotEqual(result['room_id'],far.pk)
        self.assertTrue(result['inferred'])
        exact=infer_row(self.group.pk,self.thursday,{'slot':1,'room_text':'411'})
        self.assertEqual(exact['room_id'],far.pk)

    def test_damaged_glyphs_are_read_against_complete_catalog_codes(self):
        for code,raw in [('103п1','10371'),('101п6','iOirr6'),('303','ЗОЗ')]:
            room=self.create_room(code)
            result=infer_row(self.group.pk,self.thursday,{'slot':1,'raw':f'Информационные системы Смирнов П.И.\n{raw}'})
            self.assertEqual(result['room_id'],room.pk,raw)

    def test_ambiguous_suffix_prefers_the_original_room_of_this_week_and_is_flagged(self):
        first=self.create_room('103п1');second=self.create_room('103п3')
        self.even.room=second;self.even.save()
        result=infer_row(self.group.pk,self.thursday,{'slot':1,'room_text':'103п'})
        self.assertEqual(result['room_id'],second.pk)
        self.assertTrue(result['inferred'])
        self.assertEqual({r['id'] for r in result['room_candidates']},{first.pk,second.pk})

    def test_changed_subject_uses_its_room_in_target_week_instead_of_original_classroom(self):
        subject=Subject.objects.create(name='Физическая культура')
        gym1=self.create_room('С.зал1');gym2=self.create_room('С.зал2')
        self.make_lesson(day_of_week=3,week_type='even',time_slot=self.slot2,subject=subject,teacher=self.teacher2,room=gym1)
        self.make_lesson(day_of_week=3,week_type='odd',time_slot=self.slot2,subject=subject,teacher=self.teacher2,room=gym2)
        for day,expected in [(self.thursday,gym1),(date(2026,10,29),gym2)]:
            result=infer_row(self.group.pk,day,{'slot':1,'subject_text':'Физ-ра','teacher_text':'Смирнов П.И.'})
            self.assertEqual(result['room_id'],expected.pk)
            sports=infer_row(self.group.pk,day,{'slot':1,'raw':'Физ-ра Смирнов П.И. C.sza'})
            self.assertEqual(sports['room_id'],expected.pk)

    def test_subject_module_number_is_not_treated_as_a_room_number(self):
        module_like=self.create_room('01')
        result=infer_row(self.group.pk,self.thursday,{'slot':1,'raw':'МДК 03.01 Смирнов П.И.'})
        self.assertNotEqual(result['room_id'],module_like.pk)

    def test_teacher_initials_are_not_recognized_as_a_sports_room(self):
        gym=self.create_room('С.з.')
        result=infer_row(self.group.pk,self.thursday,{'slot':1,'raw':'Информационные системы Петров С.З.'})
        self.assertNotEqual(result['room_id'],gym.pk)

    def test_review_displays_target_week_and_refreshes_old_pending_room_guess(self):
        room=self.create_room('103п1')
        batch=ImageImport.objects.create(target_date=self.thursday,layout='table',uploaded_by=self.admin,payload=[{
            'group_id':self.group.pk,'slot':1,'room_id':self.room2.pk,'room_text':'11',
            'raw_variants':['Информационные системы Смирнов П.И. 10371']}])
        self.client.force_login(self.admin)
        response=self.client.get(f'/admin/schedule/imageimport/{batch.pk}/review/')
        self.assertContains(response,'2-я неделя')
        self.assertEqual(response.context['formset'].forms[0]['room'].value(),room.pk)
        batch.refresh_from_db();self.assertEqual(batch.payload[0]['room_id'],self.room2.pk)
        batch.status='published';batch.save()
        response=self.client.get(f'/admin/schedule/imageimport/{batch.pk}/review/')
        self.assertEqual(response.context['formset'].forms[0]['room'].value(),self.room2.pk)

    def test_bell_editor_saves_cascade_as_one_weekday_and_preserves_unsaved_break_on_errors(self):
        self.client.force_login(self.admin)
        third=TimeSlot.objects.create(name='3 пара',sort_order=3,start_time=time(12),end_time=time(13,30))
        slots=[self.slot,self.slot2,third]
        data={'action':'weekday','weekday':1,'form-TOTAL_FORMS':3,'form-INITIAL_FORMS':3}
        for i,(slot,start,end) in enumerate(zip(slots,['08:00','09:40','12:00'],['09:30','11:10','13:30'])):
            data.update({f'form-{i}-slot':slot.pk,f'form-{i}-override':'on',f'form-{i}-start':start,f'form-{i}-end':end})
        self.assertEqual(self.client.post('/admin/bells/',data).status_code,302)
        response=self.client.get('/admin/bells/',{'weekday':1})
        self.assertEqual(response.context['bell_rows'][1][0].bell_pause,50)
        self.assertContains(response,'Автосдвиг следующих пар')
        data['form-0-start']='';data['form-2-start']='12:20'
        response=self.client.post('/admin/bells/',data)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['bell_rows'][1][0].bell_pause,70)
