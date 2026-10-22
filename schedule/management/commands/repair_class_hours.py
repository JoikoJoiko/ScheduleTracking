"""Recover omitted Monday rows from a verified copy of an existing import."""
import hashlib
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from schedule.models import ImportBatch, Change, GroupYear
from schedule.college_import import parse_college, materialize
from schedule.importing import populate_version
from schedule.services import audit, lock_schedule, candidate_dates, schedule_on, conflicts_for


class Command(BaseCommand):
    help='Восстановить кураторские часы из исходного Excel (сверяется SHA-256 подтверждённого импорта)'

    def add_arguments(self,parser):
        parser.add_argument('source',type=Path)
        parser.add_argument('--batch',type=int,required=True)
        parser.add_argument('--restore-missing-groups',action='store_true',help='Восстановить пары групп, полностью пропущенных старым импортом')

    @transaction.atomic
    def handle(self,*args,**options):
        lock_schedule()
        batch=ImportBatch.objects.select_for_update().filter(pk=options['batch'],status='confirmed').first()
        if not batch or not batch.version_id or not batch.metadata.get('college'):
            raise CommandError('Нужен подтверждённый импорт расписания колледжа')
        try:
            data=options['source'].read_bytes()
        except OSError as exc:
            raise CommandError(f'Не удалось прочитать исходный файл: {exc}') from exc
        if hashlib.sha256(data).hexdigest()!=batch.file_hash:
            raise CommandError('Файл отличается от исходного импорта. Восстановление отменено.')
        rows,metadata,errors=parse_college(data,options['source'].name)
        if errors:
            raise CommandError('; '.join(errors[:10]))
        old=batch.version.lessons.filter(time_slot__sort_order=0)
        if Change.objects.filter(original__in=old).exists():
            raise CommandError('Для кураторских часов есть замены. Создайте новую версию через импорт.')
        if old.filter(start_time__isnull=False).exists():
            raise CommandError('У кураторских часов есть своё время. Используйте предпросмотр новой версии.')
        # Regular lesson IDs and their replacements remain intact during repair.
        old_count=old.count()
        memberships=list(GroupYear.objects.filter(year=batch.period.year,group__name__in=[g['name'] for g in metadata['groups']]))
        regular_groups=set(batch.version.lessons.filter(time_slot__sort_order__gt=0).values_list('participants__group__name',flat=True))
        missing=[r for r in rows if r['slot']>0 and r['group'] not in regular_groups] if options['restore_missing_groups'] else []
        old.delete()
        payload=materialize([r for r in rows if r['slot']==0],metadata,batch.period)
        restored=materialize(missing,metadata,batch.period) if missing else []
        GroupYear.objects.bulk_update(memberships,['course','status'])
        populate_version(batch.version,payload)
        populate_version(batch.version,restored,reuse_existing=True)
        batch.version.groups.add(*{r['group_id'] for r in payload+restored})
        if restored:
            for day in candidate_dates(batch.version):
                conflicts=conflicts_for(day,schedule_on(day))
                if conflicts:
                    raise CommandError(f'Конфликт на {day}: {conflicts[0]["type"]}. Изменения отменены.')
        batch.payload=rows
        batch.metadata.update(metadata)
        batch.save(update_fields=['payload','metadata'])
        audit(batch.uploaded_by,'repair_class_hours',batch,new={'previous':old_count,'rows':len(payload),'restored_regular_rows':len(restored)})
        self.stdout.write(self.style.SUCCESS(f'Импорт {batch.pk}: восстановлено {len(payload)} кураторских часов; строк пар пропущенных групп: {len(restored)}'))
