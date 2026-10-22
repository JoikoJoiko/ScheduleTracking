from datetime import time
from django.test import TestCase,override_settings
from . import tests as fixtures,services as svc
from .image_import import publish_review
from .models import ImageImport,Participant,Subject,Subgroup,PracticePeriod


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PublicLessonDetailsTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def lessons(self,group):
        response=self.client.get(f'/api/groups/{group.pk}/schedule',{'date':str(self.day)})
        self.assertEqual(response.status_code,200)
        return response.json()['days'][0]['lessons']

    def shared_replacement(self,**lesson_kwargs):
        other_subject=Subject.objects.create(name='Прикладная информатика')
        self.other=self.make_lesson(group=self.group2,teacher=self.teacher2,room=self.room2,subject=other_subject,**lesson_kwargs)
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)
        return publish_review(batch,[{'group_id':self.group.pk,'slot':1,'kind':'replace',
            'subject_id':self.subject.pk,'teacher_id':self.teacher2.pk,'room_id':self.room2.pk,
            'allow_teacher_overlap':True,'allow_room_overlap':True}],self.admin)[0]

    def test_replacement_keeps_the_actual_original_subject_teacher_room_and_times(self):
        subject=Subject.objects.create(name='Математика')
        change=self.change(subject=subject,teacher=self.teacher2,room=self.room2)
        svc.publish_change(change,self.admin)
        item=self.lessons(self.group)[0]
        self.assertEqual(item['status'],'changed');self.assertEqual(item['subject'],subject.name)
        original=item['original']
        self.assertEqual((original['subject'],original['teacher'],original['room']),
            (self.subject.name,self.teacher.name,self.room.number))
        self.assertEqual((original['start'],original['end']),('08:30','10:00'))
        self.assertEqual(item['joint_groups'],[])

    def test_joint_base_lesson_lists_other_group_and_subgroup_in_both_group_schedules(self):
        subgroup=Subgroup.objects.create(group=self.group2,name='2')
        Participant.objects.create(lesson=self.lesson,group=self.group2,subgroup=subgroup)
        for group in [self.group,self.group2]:
            with self.subTest(group=group.pk):
                item=self.lessons(group)[0]
                self.assertEqual({g['id'] for g in item['joint_groups']},{self.group.pk,self.group2.pk})
                self.assertEqual(next(g for g in item['joint_groups'] if g['id']==self.group2.pk)['subgroup'],'2')
                self.assertEqual(len(item['groups']),2)

    def test_confirmed_joint_replacement_is_visible_from_both_groups_with_separate_originals(self):
        change=self.shared_replacement()
        changed=self.lessons(self.group)[0];regular=self.lessons(self.group2)[0]
        for item in [changed,regular]:
            self.assertEqual({g['id'] for g in item['joint_groups']},{self.group.pk,self.group2.pk})
            self.assertEqual(len(item['groups']),1)
        self.assertEqual(changed['original']['id'],self.lesson.pk)
        self.assertNotIn('original',regular)
        self.assertEqual(regular['subject'],self.other.subject.name)
        history=self.client.get('/api/changes',{'date':str(self.day),'group':self.group.pk}).json()['results']
        self.assertEqual(history[0]['change_id'],change.pk)
        self.assertEqual(history[0]['joint_groups'],changed['joint_groups'])
        self.assertNotIn('teacher_overlap_approvals',changed)

    def test_room_or_teacher_permission_alone_does_not_imply_joint_attendance(self):
        change=self.shared_replacement()
        for field in ['room_overlap_approvals','teacher_overlap_approvals']:
            with self.subTest(field=field):
                saved=getattr(change,field)
                setattr(change,field,[]);change.save(update_fields=[field])
                self.assertTrue(all(not item['joint_groups'] for item in self.lessons(self.group)+self.lessons(self.group2)))
                setattr(change,field,saved);change.save(update_fields=[field])

    def test_partially_overlapping_lessons_are_not_labelled_as_one_joint_pair(self):
        self.shared_replacement(start_time=time(8,45),end_time=time(10,15))
        self.assertTrue(all(not item['joint_groups'] for item in self.lessons(self.group)+self.lessons(self.group2)))

    def test_practice_and_cancelled_participants_are_not_in_joint_labels(self):
        Participant.objects.create(lesson=self.lesson,group=self.group2)
        change=self.change(kind='cancel',target_group=self.group,group=self.group)
        svc.publish_change(change,self.admin)
        self.assertEqual(self.lessons(self.group)[0]['joint_groups'],[])
        self.assertEqual(self.lessons(self.group2)[0]['joint_groups'],[])
        svc.withdraw_change(change,self.admin)
        PracticePeriod.objects.create(group=self.group2,start_date=self.day,end_date=self.day)
        self.assertEqual(self.lessons(self.group)[0]['joint_groups'],[])
        self.assertEqual(self.lessons(self.group2),[])

    def test_subgroups_of_one_group_are_not_a_joint_group_label(self):
        self.lesson.participants.update(subgroup=self.sub1)
        Participant.objects.create(lesson=self.lesson,group=self.group,subgroup=self.sub2)
        self.assertEqual(self.lessons(self.group)[0]['joint_groups'],[])

    def test_replacing_one_subgroup_does_not_join_it_to_the_remaining_shared_pair(self):
        Participant.objects.create(lesson=self.lesson,group=self.group2)
        batch=ImageImport.objects.create(target_date=self.day,layout='table',uploaded_by=self.admin)
        publish_review(batch,[{'group_id':self.group.pk,'subgroup_id':self.sub1.pk,'slot':1,'kind':'replace',
            'subject_id':self.subject.pk,'teacher_id':self.teacher2.pk,'room_id':self.room2.pk}],self.admin)
        lessons=self.lessons(self.group)
        changed=next(l for l in lessons if l['status']=='changed')
        remaining=next(l for l in lessons if l['status']=='regular')
        self.assertEqual(changed['joint_groups'],[])
        self.assertEqual(changed['groups'][0]['subgroup_id'],self.sub1.pk)
        self.assertEqual(changed['original']['groups'],changed['groups'])
        self.assertEqual(remaining['groups'][0]['subgroup_id'],self.sub2.pk)
        self.assertEqual({g['id'] for g in remaining['joint_groups']},{self.group.pk,self.group2.pk})

    def test_new_revision_and_history_do_not_inherit_the_old_joint_label(self):
        old=self.shared_replacement()
        current=self.change(teacher=self.teacher,room=self.room)
        svc.publish_change(current,self.admin)
        self.assertEqual(self.lessons(self.group)[0]['joint_groups'],[])
        self.assertEqual(self.lessons(self.group2)[0]['joint_groups'],[])
        rows=self.client.get('/api/changes',{'date':str(self.day),'group':self.group.pk}).json()['results']
        self.assertEqual({r['change_id'] for r in rows},{old.pk,current.pk})
        self.assertTrue(all(not r['joint_groups'] for r in rows))
