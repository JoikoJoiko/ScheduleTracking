from django.test import TestCase,override_settings
from . import tests as fixtures,services
from .models import Subject
from .timing import week_info


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ManualDefaultTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def defaults(self,**values):
        self.client.force_login(self.admin)
        return self.client.get('/api/admin/image-candidates',{'group':self.group.pk,'date':str(self.day),
            'subject_id':self.subject.pk,'slot':1,**values})

    def test_suggestions_use_original_schedule_even_after_a_published_change(self):
        change=self.change(teacher=self.teacher2);services.publish_change(change,self.admin)
        response=self.defaults();self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['defaults'],{'teacher_id':self.teacher.pk,'room_id':self.room.pk})

    def test_subgroup_and_week_select_an_existing_teacher_room_pair(self):
        parity='odd' if week_info(self.day,self.year)['number']==1 else 'even'
        self.lesson.week_type='even' if parity=='odd' else 'odd';self.lesson.save()
        self.make_lesson(subgroup=self.sub1,teacher=self.teacher2,room=self.room2,week_type=parity)
        self.make_lesson(subgroup=self.sub2,teacher=self.teacher,room=self.remote,week_type=parity)
        response=self.defaults(subgroup=self.sub1.pk)
        self.assertEqual(response.json()['defaults'],{'teacher_id':self.teacher2.pk,'room_id':self.room2.pk})

    def test_other_groups_subjects_are_not_guessed(self):
        other=Subject.objects.create(name='Чужая дисциплина')
        self.make_lesson(group=self.group2,subject=other,teacher=self.teacher2)
        self.assertEqual(self.defaults(subject_id=other.pk).status_code,400)

    def test_request_requires_staff_and_group_scope(self):
        self.assertEqual(self.client.get('/api/admin/image-candidates',{'group':self.group.pk,'subject_id':self.subject.pk}).status_code,401)
        self.group.building=self.other_building;self.group.save()
        self.client.force_login(self.dispatcher)
        self.assertEqual(self.client.get('/api/admin/image-candidates',{'group':self.group.pk,'subject_id':self.subject.pk}).status_code,403)
