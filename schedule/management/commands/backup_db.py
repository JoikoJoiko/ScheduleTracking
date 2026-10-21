import sqlite3
import subprocess
import os
from pathlib import Path
from datetime import datetime
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError

class Command(BaseCommand):
    help='Согласованная резервная копия SQLite или PostgreSQL (pg_dump)'
    def add_arguments(self,parser):
        parser.add_argument('--directory',default='backups')
        parser.add_argument('--keep',type=int,default=14)
    def handle(self,*args,**options):
        if options['keep']<1:
            raise CommandError('--keep должен быть не меньше 1')
        directory=Path(options['directory']).resolve(); directory.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        db=settings.DATABASES['default']
        suffix='.sqlite3' if 'sqlite' in db['ENGINE'] else '.dump'
        target=directory/f'schedule-{stamp}{suffix}'
        if suffix=='.sqlite3':
            with sqlite3.connect(str(db['NAME'])) as source,sqlite3.connect(target) as backup:
                source.backup(backup)
        else:
            env=os.environ.copy();env['PGPASSWORD']=db['PASSWORD']
            try:
                subprocess.run(['pg_dump','--format=custom','--host',db['HOST'],'--port',str(db['PORT']),
                    '--username',db['USER'],'--file',str(target),db['NAME']],env=env,check=True)
            except (OSError,subprocess.CalledProcessError) as exc:
                raise CommandError('Не удалось выполнить pg_dump. Проверьте клиент PostgreSQL и доступ к БД.') from exc
        # Retention touches only our own completed backup names inside this directory.
        for old in sorted(directory.glob('schedule-*'+suffix),reverse=True)[options['keep']:]:
            old.unlink()
        self.stdout.write(self.style.SUCCESS(str(target)))
