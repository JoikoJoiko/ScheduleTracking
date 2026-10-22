import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.db import connection,connections,transaction
from django.test import TestCase,TransactionTestCase,override_settings
from django.test.utils import CaptureQueriesContext
from . import tests as fixtures
from .importing import preview_import,import_report,_calculate_import_report
from .models import ImportBatch,ScheduleVersion,Lesson,Teacher
from .previewing import ImportPreviewUnavailable


@skipUnless(connection.vendor=='sqlite','SQLite-specific snapshot checks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ImportPreviewIsolationTests(TestCase):
    setUp=fixtures.ScheduleTests.setUp
    make_lesson=fixtures.ScheduleTests.make_lesson
    workbook=fixtures.ScheduleTests.workbook

    def batch(self,rows=None):
        return preview_import(self.workbook(rows),self.profile,self.period,self.day,self.day,self.admin)

    def test_sqlite_preview_issues_no_writes_to_source_and_reports_the_same_conflicts(self):
        batch=self.batch([
            ['Понедельник',1,g.name,'',self.subject.name,t.name,self.building.name,self.room.number,'all','','','']
            for g,t in [(self.group,self.teacher),(self.group2,self.teacher2)]])
        expected=_calculate_import_report(batch)
        with CaptureQueriesContext(connection) as queries:
            actual=import_report(batch)
        self.assertEqual(actual,expected)
        self.assertTrue(actual['conflicts'])
        for query in queries:
            self.assertNotIn(query['sql'].lstrip().split()[0].upper(),{'INSERT','UPDATE','DELETE','BEGIN'})
        self.assertEqual(ScheduleVersion.objects.count(),1)
        self.assertEqual(Lesson.objects.count(),1)

    def test_failed_worker_cleans_the_snapshot_and_renders_a_recoverable_admin_page(self):
        batch=self.batch();paths=[]
        def fail(command,**kwargs):
            paths.append(Path(command[3]));raise subprocess.TimeoutExpired(command,1)
        self.client.force_login(self.admin)
        with patch('schedule.previewing.subprocess.run',side_effect=fail),self.assertLogs('schedule.previewing',level='ERROR'):
            response=self.client.get(f'/admin/schedule/importbatch/{batch.pk}/preview/')
        self.assertEqual(response.status_code,503)
        self.assertContains(response,'Повторить проверку',status_code=503)
        self.assertNotContains(response,'Подтвердить и опубликовать',status_code=503)
        self.assertTrue(paths);self.assertTrue(all(not p.exists() for p in paths))
        batch.refresh_from_db();self.assertEqual(batch.status,'preview')
        self.assertEqual(ScheduleVersion.objects.count(),1)

    def test_worker_failure_is_a_clear_api_error_without_publishing(self):
        batch=self.batch();self.client.force_login(self.admin)
        with patch('schedule.views.import_report',side_effect=ImportPreviewUnavailable('Повторите проверку')):
            response=self.client.get(f'/api/admin/imports/{batch.pk}')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'preview_unavailable')
        batch.refresh_from_db();self.assertEqual(batch.status,'preview')

    def test_postgresql_keeps_the_transactional_report_path(self):
        batch=self.batch()
        with patch.object(connection,'vendor','postgresql'),patch('schedule.importing._calculate_import_report',return_value={'rows':[]}) as calculate:
            self.assertEqual(import_report(batch),{'rows':[]})
        calculate.assert_called_once_with(batch)


@skipUnless(connection.vendor=='sqlite','SQLite-specific concurrent snapshot checks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ConcurrentImportPreviewTests(TransactionTestCase):
    make_lesson=fixtures.ScheduleTests.make_lesson
    workbook=fixtures.ScheduleTests.workbook

    def setUp(self):
        fixtures.ScheduleTests.setUp(self)
        self.batch=preview_import(self.workbook(),self.profile,self.period,self.day,self.day,self.admin)

    def test_live_database_remains_writable_while_snapshot_is_being_checked(self):
        run=subprocess.run;paths=[]
        def check(command,**kwargs):
            paths.append(Path(command[3]))
            # A separate connection can write while the worker checks its earlier snapshot.
            with sqlite3.connect(str(connection.settings_dict['NAME']),uri=True,timeout=.5) as writer:
                writer.execute('BEGIN IMMEDIATE')
                writer.execute('UPDATE schedule_teacher SET name=? WHERE id=?',('Изменён во время проверки',self.teacher.pk))
            return run(command,**kwargs)
        original=self.teacher.name
        with patch('schedule.previewing.subprocess.run',side_effect=check):
            report=import_report(self.batch)
        self.assertEqual(report['rows'][0]['teacher'],original)
        self.teacher.refresh_from_db();self.assertEqual(self.teacher.name,'Изменён во время проверки')
        self.assertTrue(all(not p.exists() for p in paths))

    def test_two_simultaneous_previews_use_different_copies_and_keep_live_lessons(self):
        run=subprocess.run;barrier=Barrier(2);paths=[]
        def check(command,**kwargs):
            paths.append(Path(command[3]));barrier.wait(timeout=10)
            return run(command,**kwargs)
        def preview(_):
            try:
                return import_report(ImportBatch.objects.get(pk=self.batch.pk))
            finally:
                connections.close_all()
        with patch('schedule.previewing.subprocess.run',side_effect=check),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(preview,range(2)))
        self.assertEqual(len(set(paths)),2)
        self.assertEqual(results[0],results[1])
        self.assertTrue(all(not p.exists() for p in paths))
        self.assertEqual(ScheduleVersion.objects.count(),1);self.assertEqual(Lesson.objects.count(),1)
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'preview')

    def test_sqlite_atomic_writers_acquire_the_lock_before_reading(self):
        with CaptureQueriesContext(connection) as queries,transaction.atomic():
            Teacher.objects.filter(pk=self.teacher.pk).exists()
        self.assertIn('BEGIN IMMEDIATE',[q['sql'] for q in queries])
