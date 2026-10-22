from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError
from . import tests as fixtures,services as svc
from .models import ImageImport,Change,Room,Teacher,StudentGroup,GroupYear,AuditLog
from .image_import import publish_review,ImageReviewConflicts


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RoomOverlapReviewTests(TestCase):
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def setUp(self):
        fixtures.ScheduleTests.setUp(self)
        self.other=self.make_lesson(group=self.group2,teacher=self.teacher2,room=self.room2)
        self.batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)

    def row(self,**extra):
        return {'group_id':self.group.pk,'slot':1,'kind':'replace','subject_id':self.subject.pk,
                'teacher_id':self.teacher.pk,'room_id':self.room2.pk,**extra}

    def test_room_error_keeps_original_row_number_and_both_lesson_details(self):
        with self.assertRaises(ImageReviewConflicts) as caught:
            publish_review(self.batch,[self.row(review_row=4)],self.admin)
        conflict=caught.exception.conflicts[0]
        self.assertEqual(conflict['rows'],[4])
        self.assertEqual((conflict['start'],conflict['end']),('08:30','10:00'))
        text=' '.join(caught.exception.messages)
        for expected in ['4',self.room2.number,self.group.name,self.group2.name,self.teacher2.name,self.subject.name]:
            self.assertIn(expected,text)
        self.assertFalse(Change.objects.exists())
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'review')

    def approve(self):
        return publish_review(self.batch,[self.row(allow_room_overlap=True)],self.admin)[0]

    def test_explicit_room_exception_is_persisted_audited_and_room_remains_busy(self):
        change=self.approve();change.refresh_from_db()
        self.assertEqual(len(change.room_overlap_approvals),1)
        rows=svc.schedule_on(self.day)
        self.assertEqual(svc.conflicts_for(self.day,rows),[])
        accepted=svc.conflicts_for(self.day,rows,include_allowed=True)
        self.assertEqual(len(accepted),1);self.assertTrue(accepted[0]['allowed'])
        room=svc.availability(self.day,self.slot.start_time,self.slot.end_time,[self.room2])[0]
        self.assertEqual(room['status'],'busy');self.assertEqual(len(room['lessons']),2)
        audit=AuditLog.objects.get(action='publish_change',entity_id=str(change.pk))
        self.assertEqual(audit.user_id,self.admin.pk)
        self.assertIn(self.group2.name,audit.new_values['room_overlap_approvals'][0]['with'])
        self.client.force_login(self.admin)
        response=self.client.get('/admin/conflicts/',{'date':str(self.day)})
        self.assertContains(response,'разрешено');self.assertContains(response,self.group.name)
        self.assertContains(response,self.group2.name)

    def test_exception_does_not_allow_an_unrelated_new_lesson_to_share_the_room(self):
        self.approve()
        group=StudentGroup.objects.create(name='ТЕСТ-3-26',building=self.building,admission_year=2026,graduation_year=2030)
        GroupYear.objects.create(group=group,year=self.year,course=1)
        teacher=Teacher.objects.create(name='Третий В.В.')
        change=self.change(kind='add',original=None,time_slot=self.slot,subject=self.subject,
                           group=group,teacher=teacher,room=self.room2)
        with self.assertRaisesMessage(ValidationError,'Пересечение аудиторий'):
            svc.publish_change(change,self.admin)
        change.refresh_from_db();self.assertEqual(change.status,'draft')

    def test_changed_counterpart_does_not_inherit_the_old_exception(self):
        self.approve()
        change=self.change(original=self.other,teacher=self.teacher2,room=self.room2)
        with self.assertRaisesMessage(ValidationError,'Пересечение аудиторий'):
            svc.publish_change(change,self.admin)
        change.refresh_from_db();self.assertEqual(change.status,'draft')

    def test_room_exception_never_waives_teacher_group_or_unavailable_room_errors(self):
        with self.assertRaises(ImageReviewConflicts) as caught:
            publish_review(self.batch,[self.row(teacher_id=self.teacher2.pk,allow_room_overlap=True)],self.admin)
        self.assertTrue(any(c['type']=='Пересечение преподавателя' and not c['allowed'] for c in caught.exception.conflicts))
        self.assertTrue(any(c['type']=='Пересечение аудиторий' and c['allowed'] for c in caught.exception.conflicts))
        self.assertFalse(Change.objects.exists())
        self.other.participants.update(group=self.group)
        self.other.time_slot=self.slot2;self.other.start_time=self.slot.start_time;self.other.end_time=self.slot.end_time;self.other.save()
        with self.assertRaisesMessage(ImageReviewConflicts,'Пересечение группы'):
            publish_review(self.batch,[self.row(allow_room_overlap=True)],self.admin)
        self.other.participants.update(group=self.group2)
        self.room2.is_active=False;self.room2.save()
        with self.assertRaisesMessage(ImageReviewConflicts,'Аудитория недоступна'):
            publish_review(self.batch,[self.row(allow_room_overlap=True)],self.admin)
        self.assertFalse(Change.objects.exists())

    def test_review_highlights_both_rows_then_publishes_after_one_explicit_checkbox(self):
        room=Room.objects.create(number='103',building=self.building,room_type=self.type)
        self.batch.payload=[{} for _ in range(7)];self.batch.save()
        data={'form-TOTAL_FORMS':7,'form-INITIAL_FORMS':7}
        for i in range(7):data[f'form-{i}-kind']='replace'
        for index,group,teacher in [(1,self.group,self.teacher),(6,self.group2,self.teacher2)]:
            data.update({f'form-{index}-use':'on',f'form-{index}-group':group.pk,f'form-{index}-slot':1,
                f'form-{index}-subject':self.subject.pk,f'form-{index}-teacher':teacher.pk,f'form-{index}-room':room.pk})
        self.client.force_login(self.admin)
        url=f'/admin/schedule/imageimport/{self.batch.pk}/review/'
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        for text in ['Строка 2','Строка 7','Со строкой 7',self.teacher2.name,'Оставить пересечение кабинета']:
            self.assertContains(response,text)
        self.assertEqual(response.context['review_conflicts'][0]['rows'],[2,7])
        self.assertFalse(response.context['formset'].forms[1]['allow_room_overlap'].value())
        self.assertFalse(Change.objects.exists())
        data['form-1-allow_room_overlap']='on'
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(Change.objects.filter(status='published').count(),2)
        self.assertEqual(sum(bool(c.room_overlap_approvals) for c in Change.objects.all()),1)
        self.assertEqual(svc.conflicts_for(self.day,svc.schedule_on(self.day)),[])

    def test_approval_data_cannot_be_injected_through_draft_api(self):
        self.client.force_login(self.admin)
        response=self.client.post('/api/admin/changes',{'room_overlap_approvals':[{'lesson':'fake','other':'fake'}]},content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertFalse(Change.objects.exists())
