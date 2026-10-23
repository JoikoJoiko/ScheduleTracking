import io
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from datetime import timedelta
from django.test import TestCase,Client,override_settings
from . import tests as fixtures,services
from .models import ImageImport,Change,Subject,PracticePeriod,Participant,RoomCampus,Room,Building,TimeSlot
from .exports import group_report,changes_report,word_document,W
from .college_import import materialize


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ExportTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson
    change=fixtures.ScheduleTests.change

    def xml(self,body):
        with ZipFile(io.BytesIO(body)) as package:
            self.assertIsNone(package.testzip())
            return ET.fromstring(package.read('word/document.xml'))

    def test_public_word_week_is_six_days_and_preserves_subgroups_and_original_pairs(self):
        self.lesson.participants.update(subgroup=self.sub1)
        subject=Subject.objects.create(name='Предмет <новый> & другой')
        change=self.change(subject=subject,teacher=self.teacher2);services.publish_change(change,self.admin)
        response=self.client.get(f'/exports/groups/{self.group.pk}',{'date':str(self.day),'view':'week'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        root=self.xml(response.content)
        text=' '.join(root.itertext())
        for value in [self.group.name,subject.name,'Вместо:',self.subject.name,self.teacher.name,'Подгруппа 1']:
            self.assertIn(value,text)
        self.assertNotIn('Воскресенье',text)
        headers=root.find('.//{'+W+'}tbl/{'+W+'}tr')
        self.assertEqual(len(headers.findall('{'+W+'}tc')),7)
        self.assertEqual(root.find('.//{'+W+'}pgSz').get('{'+W+'}orient'),'landscape')
        self.assertIsNotNone(root.find('.//{'+W+'}tblHeader'))

    def test_base_export_has_both_weeks_and_ignores_temporary_practice_and_changes(self):
        PracticePeriod.objects.create(group=self.group,start_date=self.day,end_date=self.day+timedelta(days=6))
        actual=group_report(self.group,self.day,'week')
        self.assertIn('Практика',' '.join(str(r) for r in actual['sections'][0]['rows']))
        base=group_report(self.group,self.day,'base')
        self.assertEqual([s['title'] for s in base['sections']],['1-я неделя','2-я неделя'])
        self.assertTrue(all(self.subject.name in str(s['rows']) for s in base['sections']))
        self.assertNotIn('Практика до',str(base))
        root=self.xml(word_document(base))
        self.assertIsNone(root.find('.//{'+W+'}pageBreakBefore'))
        self.assertEqual(len(root.findall('.//{'+W+'}tbl')),2)
        self.assertIn('1-я неделя',' '.join(root.itertext()))
        self.assertIn('2-я неделя',' '.join(root.itertext()))

    def test_changes_export_uses_current_revision_and_dashes_for_absent_pairs(self):
        subject=Subject.objects.create(name='Новый предмет')
        old=self.change(subject=subject);services.publish_change(old,self.admin)
        current=self.change();services.publish_change(current,self.admin)
        report=changes_report(self.day)
        self.assertEqual(len(report['sections'][0]['rows']),1)
        row=report['sections'][0]['rows'][0]
        self.assertIn('Вместо:',row[1]);self.assertNotIn(subject.name,str(row))
        self.assertEqual(row[2:],["—"]*6)
        self.xml(word_document(report))

    def test_print_page_is_public_escaped_and_has_word_download(self):
        self.subject.name='<script>alert(1)</script>';self.subject.save()
        response=self.client.get(f'/exports/groups/{self.group.pk}',{'date':str(self.day),'format':'print','view':'day'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'window.print()');self.assertContains(response,'format=docx')
        self.assertContains(response,'&lt;script&gt;');self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertContains(response,'table-header-group')

    def test_filtered_changes_keep_joint_label_without_extra_group_rows(self):
        Participant.objects.create(lesson=self.lesson,group=self.group2)
        change=self.change();services.publish_change(change,self.admin)
        report=changes_report(self.day,self.group.pk)
        rows=report['sections'][0]['rows']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],self.group.name)
        self.assertIn('Объединено с: '+self.group2.name,rows[0][1])

    def test_no_export_can_publish_drafts_and_invalid_requests_are_rejected(self):
        self.change()
        response=self.client.get('/exports/changes',{'date':str(self.day)})
        self.assertEqual(response.status_code,200)
        self.assertIn('Замен нет',' '.join(self.xml(response.content).itertext()))
        for values in [{'date':'bad'},{'view':'evil'},{'format':'html'},{'group':'<script>'}]:
            with self.subTest(values=values):
                self.assertEqual(self.client.get('/exports/changes',values).status_code,400)
        self.assertEqual(Change.objects.get().status,'draft')
        self.assertEqual(self.client.post('/exports/changes').status_code,405)

    def test_week_export_keeps_internal_gaps_and_labels_missing_data(self):
        third=TimeSlot.objects.create(name='3 пара',sort_order=3)
        self.make_lesson(time_slot=third)
        report=group_report(self.group,self.day,'week')
        self.assertEqual([r[0] for r in report['sections'][0]['rows']],['1 пара','2 пара','3 пара'])
        self.version.effective_from=self.day+timedelta(days=1);self.version.save()
        report=group_report(self.group,self.day,'week')
        self.assertIn('Нет расписания',report['sections'][0]['rows'][0][1])


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ManualAndRoomTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson

    def new_batch(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/admin/changes/new/').status_code,200)
        self.assertFalse(ImageImport.objects.exists())
        response=self.client.post('/admin/changes/new/',{'date':str(self.day),'row_count':1})
        self.assertEqual(response.status_code,302)
        return ImageImport.objects.get()

    def data(self):
        return {'form-TOTAL_FORMS':4,'form-INITIAL_FORMS':1,**{f'form-{i}-kind':'replace' for i in range(4)},
            'form-0-use':'on','form-0-group':self.group.pk,'form-0-slot':1,'form-0-subject':self.subject.pk,
            'form-0-teacher':self.teacher.pk,'form-0-room':self.room2.pk}

    def test_manual_replacements_share_review_conflicts_publication_and_word_export(self):
        batch=self.new_batch();url=f'/admin/schedule/imageimport/{batch.pk}/review/'
        response=self.client.get(url)
        self.assertContains(response,'Ручной ввод');self.assertContains(response,'manual-review')
        self.assertNotContains(response,'Распознать повторно')
        response=self.client.post(url,self.data());self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,url)
        batch.refresh_from_db();self.assertEqual(batch.status,'published')
        self.assertContains(self.client.get(url),'Скачать замены в Word')
        self.assertContains(self.client.get(url),'Распечатать')
        self.assertEqual(Change.objects.get().original_id,self.lesson.pk)
        self.assertEqual(self.client.get('/exports/changes',{'date':str(self.day)}).status_code,200)
        self.assertEqual(self.client.get(f'/admin/schedule/imageimport/{batch.pk}/source/').status_code,404)

    def test_adding_manual_rows_preserves_data_without_publishing(self):
        batch=self.new_batch();data=self.data();data['add_rows']='1'
        response=self.client.post(f'/admin/schedule/imageimport/{batch.pk}/review/',data)
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.context['formset'].forms),9)
        self.assertEqual(str(response.context['formset'].forms[0]['room'].value()),str(self.room2.pk))
        self.assertFalse(Change.objects.exists())

    def test_manual_creation_requires_staff_csrf_and_a_teaching_day(self):
        self.assertEqual(self.client.post('/admin/changes/new/',{'date':str(self.day),'row_count':1}).status_code,302)
        client=Client(enforce_csrf_checks=True);client.force_login(self.admin)
        self.assertEqual(client.post('/admin/changes/new/',{'date':str(self.day),'row_count':1}).status_code,403)
        self.client.force_login(self.admin)
        response=self.client.post('/admin/changes/new/',{'date':str(self.day+timedelta(days=6)),'row_count':1})
        self.assertContains(response,'Воскресенье');self.assertFalse(ImageImport.objects.exists())

    def test_room_grouping_is_manual_filterable_and_does_not_change_import_identity(self):
        campus=RoomCampus.objects.create(name='Корпус 1')
        self.client.force_login(self.admin)
        rooms=list(Room.objects.all())
        data={'action':'rooms','form-TOTAL_FORMS':len(rooms),'form-INITIAL_FORMS':len(rooms)}
        for i,room in enumerate(rooms):
            data.update({f'form-{i}-room':room.pk,f'form-{i}-campus':campus.pk if room.pk==self.room.pk else '',
                         f'form-{i}-floor':'0' if room.pk==self.room.pk else ''})
        self.assertEqual(self.client.post('/admin/rooms/grouping/',data).status_code,302)
        self.room.refresh_from_db()
        self.assertEqual((self.room.building_id,self.room.floor,self.room.campus_id),(self.building.pk,0,campus.pk))
        meta=self.client.get('/api/meta').json();self.assertIn(0,meta['room_floors'])
        result=self.client.get('/api/rooms/availability',{'date':str(self.day),'campus':campus.pk,'floor':0,'group_by':'campus_floor'}).json()
        self.assertEqual(result['count'],1);self.assertEqual(result['results'][0]['campus'],'Корпус 1')
        self.client.force_login(self.dispatcher)
        self.assertEqual(self.client.get('/admin/rooms/grouping/').status_code,403)

    def test_reimport_preserves_campus_and_floor(self):
        campus=RoomCampus.objects.create(name='Главный корпус');building=Building.objects.create(name='Колледж')
        room=Room.objects.create(number='210п',building=building,room_type=self.type,campus=campus,floor=2)
        metadata={'groups':[{'name':self.group.name,'admission_year':2023,'course':4}]}
        materialize([{'group':self.group.name,'subgroup':'','room':room.number,'teacher':self.teacher.name,'subject':self.subject.name,
            'slot':1,'week_type':'all','day_of_week':0}],metadata,self.period)
        room.refresh_from_db();self.assertEqual((room.campus_id,room.floor),(campus.pk,2))

    def test_number_rules_include_suffixes_gym_and_preserve_unknown_codes(self):
        from .room_locations import location_from_number
        for number,expected in [('101',(1,'1 корпус · Основной')),('204',(2,'1 корпус · Основной')),('104о',(1,'1 корпус · Общежитие')),
                ('103п1',(1,'1 корпус · Производственный')),('101п6',(1,'1 корпус · Производственный')),
                ('210П',(2,'1 корпус · Производственный')),('С.зал2',(1,'1 корпус · Основной')),('Спортивный зал',(1,'1 корпус · Основной')),
                ('спорт. зал',(1,'1 корпус · Основной')),('01',(None,'1 корпус · Основной')),('Акт.',(None,None)),('А.з.о',(None,'1 корпус · Общежитие')),('101б',(1,None))]:
            with self.subTest(number=number):self.assertEqual(location_from_number(number),expected)

    def test_named_rooms_defaults_and_existing_data_upgrade(self):
        from .room_locations import location_from_number
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        rooms=[]
        for name,floor in [('А.з.',1),('А.з',1),('Библ',2),('Библ.',2)]:
            self.assertEqual(location_from_number(name),(floor,'1 корпус · Основной'))
            rooms.append((Room.objects.create(number=name,building=self.building,room_type=self.type),floor))
        dorm=Room.objects.create(number='А.з.о',building=self.building,room_type=self.type)
        self.room.floor=4;self.room.save()
        import_module('schedule.migrations.0015_named_room_locations').fill_named_rooms(apps,SimpleNamespace(connection=connection))
        for room,floor in rooms:
            room.refresh_from_db();self.assertEqual(room.floor,floor)
            self.assertEqual(room.campus.name,'1 корпус · Основной')
        dorm.refresh_from_db();self.assertIsNone(dorm.floor);self.assertIsNone(dorm.campus_id)
        self.room.refresh_from_db();self.assertEqual(self.room.floor,4)

    def test_infer_location_is_explicit_root_only_and_preserves_manual_values(self):
        room=Room.objects.create(number='104о',building=self.building,room_type=self.type)
        self.client.force_login(self.admin)
        self.client.get('/admin/rooms/grouping/')
        room.refresh_from_db();self.assertIsNone(room.floor)
        self.assertEqual(self.client.post('/admin/rooms/grouping/',{'action':'infer'}).status_code,302)
        room.refresh_from_db();self.assertEqual((room.floor,room.campus.name),(1,'1 корпус · Общежитие'))
        room.floor=0;room.save()
        self.client.post('/admin/rooms/grouping/',{'action':'infer'})
        room.refresh_from_db();self.assertEqual(room.floor,0)
        self.assertEqual(room.building_id,self.building.pk)
        self.client.force_login(self.dispatcher)
        self.assertEqual(self.client.post('/admin/rooms/grouping/',{'action':'infer'}).status_code,403)

    def test_excel_import_fills_new_room_location_without_changing_number(self):
        metadata={'groups':[{'name':self.group.name,'admission_year':2023,'course':4}]}
        materialize([{'group':self.group.name,'subgroup':'','room':'103п4','teacher':self.teacher.name,
            'subject':self.subject.name,'slot':1,'week_type':'all','day_of_week':0}],metadata,self.period)
        room=Room.objects.get(number='103п4')
        self.assertEqual((room.floor,room.campus.name),(1,'1 корпус · Производственный'))

    def test_complex_order_and_upgrade_keep_manual_room_assignments(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        old=RoomCampus.objects.create(name='Производственный')
        self.room2.campus=old;self.room2.save()
        custom=RoomCampus.objects.create(name='2 корпус · Основной')
        self.remote.campus=custom;self.remote.save()
        import_module('schedule.migrations.0014_main_room_campus').group_first_complex(apps,SimpleNamespace(connection=connection))
        self.room.refresh_from_db();self.room2.refresh_from_db();self.remote.refresh_from_db()
        self.assertEqual(self.room.campus.name,'1 корпус · Основной')
        self.assertEqual(self.room2.campus.name,'1 корпус · Производственный')
        self.assertEqual(self.remote.campus_id,custom.pk)
        RoomCampus.objects.create(name='1 корпус · Общежитие')
        self.assertEqual([c['name'] for c in self.client.get('/api/meta').json()['room_campuses']],
            ['1 корпус · Основной','1 корпус · Производственный','1 корпус · Общежитие','2 корпус · Основной'])
