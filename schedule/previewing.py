"""Run SQLite import simulations on disposable snapshots, never on the live database."""
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from django.conf import settings
from django.db import connection


class ImportPreviewUnavailable(Exception):
    pass


def sqlite_import_report(batch):
    connection.ensure_connection()
    try:
        with tempfile.TemporaryDirectory(prefix='schedule-preview-') as folder:
            snapshot=Path(folder)/'preview.sqlite3'
            with closing(sqlite3.connect(snapshot)) as target:
                if connection.in_atomic_block:
                    # Include this connection's uncommitted data without backing up a write transaction.
                    target.executescript('\n'.join(connection.connection.iterdump()))
                else:
                    started=time.monotonic()
                    def progress(status,remaining,total):
                        if time.monotonic()-started>20:
                            raise TimeoutError('SQLite snapshot timed out')
                    connection.connection.backup(target,pages=256,progress=progress)
            env=os.environ.copy()
            env['DJANGO_SETTINGS_MODULE']=env.get('DJANGO_SETTINGS_MODULE') or 'college.settings'
            env['PYTHONIOENCODING']='utf-8'
            result=subprocess.run([sys.executable,'-m','schedule.preview_worker',str(snapshot),str(batch.pk)],
                cwd=settings.BASE_DIR,env=env,capture_output=True,encoding='utf-8',timeout=180,check=True)
            return json.loads(result.stdout)
    except (OSError,sqlite3.Error,subprocess.SubprocessError,ValueError,TimeoutError) as exc:
        logging.getLogger(__name__).exception('Isolated import preview failed')
        if isinstance(exc,subprocess.CalledProcessError):
            logging.getLogger(__name__).error('Preview worker: %s',exc.stderr[-6000:] if exc.stderr else '(no diagnostic output)')
        raise ImportPreviewUnavailable('Не удалось завершить проверку. Повторите её через несколько секунд; расписание не изменено.') from exc
