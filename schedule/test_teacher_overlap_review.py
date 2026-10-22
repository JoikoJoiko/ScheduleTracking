from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError
from . import tests as fixtures,services as svc
from .models import ImageImport,Change,Room,StudentGroup,GroupYear,AuditLog
from .image_import import publish_review,ImageReviewConflicts


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TeacherOverlapReviewTests(TestCase):
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def setUp(self):
        fixtures.ScheduleTests.setUp(self)
        self.other=self.make_lesson(group=self.group2,teacher=self.teacher2,room=self.room2)
        self.batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)

    def row(self,**extra):
        return {'group_id':self.group.pk,'slot':1,'kind':'replace','subject_id':self.subject.pk,
                'teacher_id':self.teacher2.pk,'room_id':self.room.pk,**extra}

    def approve(self):
        return publish_review(self.batch,[self.row(allow_teacher_overlap=True)],self.admin)[0]

    def test_unapproved_conflict_names_the_row_teacher_and_both_lessons(self):
        with self.assertRaises(ImageReviewConflicts) as caught:
            publish_review(self.batch,[self.row(review_row=4)],self.admin)
        conflict=caught.exception.conflicts[0]
        self.assertTrue(conflict['teacher_conflict']);self.assertFalse(conflict['room_conflict'])
        self.assertEqual(conflict['rows'],[4])
        for text in [self.teacher2.name,self.group.name,self.group2.name,'08:30','10:00']:
            self.assertIn(text,' '.join(caught.exception.messages))
        self.assertFalse(Change.objects.exists())
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'review')

    def test_teacher_exception_is_persisted_audited_and_visible_in_conflict_control(self):
        change=self.approve();change.refresh_from_db()
        self.assertEqual(len(change.teacher_overlap_approvals),1)
        self.assertEqual(change.room_overlap_approvals,[])
        lessons=svc.schedule_on(self.day)
        self.assertEqual(len(lessons),2)
        self.assertEqual(svc.conflicts_for(self.day,lessons),[])
        allowed=svc.conflicts_for(self.day,lessons,include_allowed=True)
        self.assertEqual([(c['type'],c['allowed']) for c in allowed],[('Пересечение преподавателя',True)])
        audit=AuditLog.objects.get(action='publish_change',entity_id=str(change.pk))
        self.assertEqual(audit.user_id,self.admin.pk)
        entry=audit.new_values['teacher_overlap_approvals'][0]
        self.assertEqual(entry['teacher'],self.teacher2.name)
        self.assertIn(self.group2.name,entry['with'])
        self.client.force_login(self.admin)
        response=self.client.get('/admin/conflicts/',{'date':str(self.day)})
        for text in ['Пересечение преподавателя','разрешено',self.teacher2.name,self.group.name,self.group2.name]:
            self.assertContains(response,text)

    def test_room_and_teacher_decisions_are_independent(self):
        for flags,blocked,allowed in [
            ({'allow_teacher_overlap':True},'Пересечение аудиторий','Пересечение преподавателя'),
            ({'allow_room_overlap':True},'Пересечение преподавателя','Пересечение аудиторий'),
        ]:
            with self.subTest(flags=flags),self.assertRaises(ImageReviewConflicts) as caught:
                publish_review(self.batch,[self.row(room_id=self.room2.pk,**flags)],self.admin)
            self.assertTrue(any(c['type']==blocked and not c['allowed'] for c in caught.exception.conflicts))
            self.assertTrue(any(c['type']==allowed and c['allowed'] for c in caught.exception.conflicts))
            self.assertFalse(Change.objects.exists())
        change=publish_review(self.batch,[self.row(room_id=self.room2.pk,allow_room_overlap=True,allow_teacher_overlap=True)],self.admin)[0]
        self.assertEqual(len(change.room_overlap_approvals),1)
        self.assertEqual(len(change.teacher_overlap_approvals),1)
        self.assertEqual(svc.conflicts_for(self.day,svc.schedule_on(self.day)),[])

    def test_either_of_two_uploaded_rows_can_approve_their_teacher_overlap(self):
        rows=[self.row(review_row=2),self.row(group_id=self.group2.pk,room_id=self.room2.pk,review_row=7)]
        with self.assertRaises(ImageReviewConflicts) as caught:
            publish_review(self.batch,rows,self.admin)
        self.assertEqual(caught.exception.conflicts[0]['rows'],[2,7])
        rows[1]['allow_teacher_overlap']=True
        changes=publish_review(self.batch,rows,self.admin)
        self.assertEqual([len(c.teacher_overlap_approvals) for c in changes],[0,1])
        self.assertEqual(svc.conflicts_for(self.day,svc.schedule_on(self.day)),[])

    def test_unrelated_new_lesson_does_not_inherit_teacher_permission(self):
        self.approve()
        group=StudentGroup.objects.create(name='ТЕСТ-3-26',building=self.building,admission_year=2026,graduation_year=2030)
        GroupYear.objects.create(group=group,year=self.year,course=1)
        room=Room.objects.create(number='103',building=self.building,room_type=self.type)
        change=self.change(kind='add',original=None,time_slot=self.slot,subject=self.subject,
                           group=group,teacher=self.teacher2,room=room)
        with self.assertRaisesMessage(ValidationError,'Пересечение преподавателя'):
            svc.publish_change(change,self.admin)
        change.refresh_from_db();self.assertEqual(change.status,'draft')

    def test_new_counterpart_revision_requires_a_new_teacher_decision(self):
        self.approve()
        change=self.change(original=self.other,teacher=self.teacher2,room=self.room2)
        with self.assertRaisesMessage(ValidationError,'Пересечение преподавателя'):
            svc.publish_change(change,self.admin)
        change.refresh_from_db();self.assertEqual(change.status,'draft')

    def test_both_permissions_do_not_waive_group_or_unavailable_room_errors(self):
        row=self.row(room_id=self.room2.pk,allow_teacher_overlap=True,allow_room_overlap=True)
        self.other.participants.update(group=self.group)
        self.other.time_slot=self.slot2;self.other.start_time=self.slot.start_time;self.other.end_time=self.slot.end_time;self.other.save()
        with self.assertRaisesMessage(ImageReviewConflicts,'Пересечение группы'):
            publish_review(self.batch,[row],self.admin)
        self.other.participants.update(group=self.group2)
        self.room2.is_active=False;self.room2.save()
        with self.assertRaisesMessage(ImageReviewConflicts,'Аудитория недоступна'):
            publish_review(self.batch,[row],self.admin)
        self.assertFalse(Change.objects.exists())

    def test_review_shows_both_checkboxes_and_retains_one_while_the_other_blocks(self):
        # This teacher belongs to the group's semester dictionary as well.
        self.make_lesson(time_slot=self.slot2,teacher=self.teacher2,room=self.room)
        self.batch.payload=[{} for _ in range(4)];self.batch.save()
        self.client.force_login(self.admin)
        url=f'/admin/schedule/imageimport/{self.batch.pk}/review/'
        data={'form-TOTAL_FORMS':4,'form-INITIAL_FORMS':4}
        data.update({f'form-{i}-kind':'replace' for i in range(4)})
        data.update({'form-1-use':'on','form-1-group':self.group.pk,'form-1-slot':1,
            'form-1-subject':self.subject.pk,'form-1-teacher':self.teacher2.pk,'form-1-room':self.room2.pk})
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        for text in ['Строка 2',self.teacher2.name,self.group2.name,'Оставить пересечение преподавателя','Оставить пересечение кабинета']:
            self.assertContains(response,text)
        self.assertFalse(response.context['formset'].forms[1]['allow_teacher_overlap'].value())
        self.assertFalse(response.context['formset'].forms[1]['allow_room_overlap'].value())
        data['form-1-allow_teacher_overlap']='on'
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['formset'].forms[1]['allow_teacher_overlap'].value())
        self.assertContains(response,'Пересечение преподавателя разрешено')
        self.assertFalse(Change.objects.exists())
        data['form-1-allow_room_overlap']='on'
        self.assertEqual(self.client.post(url,data).status_code,302)
        change=Change.objects.get()
        self.assertEqual(len(change.teacher_overlap_approvals),1)
        self.assertEqual(len(change.room_overlap_approvals),1)

    def test_teacher_approval_payload_cannot_be_injected_through_draft_api(self):
        self.client.force_login(self.admin)
        response=self.client.post('/api/admin/changes',{'teacher_overlap_approvals':[{'lesson':'fake','other':'fake'}]},content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertFalse(Change.objects.exists())
