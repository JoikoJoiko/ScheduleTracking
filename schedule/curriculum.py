"""Candidate dictionaries come from the group's published Excel snapshot, both weeks."""
from datetime import timedelta
from django.db.models import Q
from .models import Period, ScheduleVersion


def group_versions(group):
    return ScheduleVersion.objects.filter(status='published').filter(
        Q(groups=group) | Q(groups__isnull=True, lessons__participants__group=group)).distinct()


def curriculum_for(group, day, period=None):
    period = period or Period.objects.filter(start_date__lte=day, end_date__gte=day).order_by('-start_date','end_date','-pk').first()
    result = {'group': {'id': group.pk, 'name': group.name}, 'period': None,
              'subjects': [], 'teachers': [], 'subgroups': list(group.subgroup_set.values('id','name')), 'version': None}
    if period is None:
        return result
    result['period'] = {'id': period.pk, 'name': period.name, 'start': period.start_date.isoformat(), 'end': period.end_date.isoformat()}
    # Semester boundaries may be added after a file was imported into a whole-year period.
    versions = ScheduleVersion.objects.filter(status='published',period__year=period.year,
        effective_from__lte=period.end_date,effective_to__gte=period.start_date).filter(
        Q(groups=group) | Q(groups__isnull=True)).distinct()
    # A selected semester may be in the future; its nearest snapshot still defines the dictionary.
    reference=max(period.start_date,min(day,period.end_date))
    version = versions.filter(effective_from__lte=reference, effective_to__gte=reference).order_by('-published_at','-pk').first()
    version = version or versions.filter(effective_from__gt=reference).order_by('effective_from','-published_at','-pk').first()
    version = version or versions.order_by('-effective_to','-published_at','-pk').first()
    if version is None:
        return result
    result['version'] = {'id': version.pk, 'start': version.effective_from.isoformat(), 'end': version.effective_to.isoformat()}
    subjects, teachers = {}, {}
    for lesson in version.lessons.filter(participants__group=group).select_related('subject','teacher').distinct():
        item = subjects.setdefault(lesson.subject_id, {'id': lesson.subject_id, 'name': lesson.subject.name,
            'short_name': lesson.subject.short_name, 'teachers': {}, 'weeks': set()})
        teacher = {'id': lesson.teacher_id, 'name': lesson.teacher.name}
        item['teachers'][lesson.teacher_id] = teacher
        item['weeks'].update([1,2] if lesson.week_type=='all' else [1 if lesson.week_type=='odd' else 2])
        teachers[lesson.teacher_id] = teacher
    for item in subjects.values():
        item['weeks'] = sorted(item['weeks'])
        item['teachers'] = sorted(item['teachers'].values(), key=lambda x:x['name'])
    result['subjects'] = sorted(subjects.values(), key=lambda x:x['name'])
    result['teachers'] = sorted(teachers.values(), key=lambda x:x['name'])
    return result


def schedule_coverage(kind, obj, day):
    versions = ScheduleVersion.objects.filter(status='published')
    if kind=='groups':
        versions = group_versions(obj)
    elif kind=='teachers':
        versions = versions.filter(lessons__teacher=obj).distinct()
    elif kind=='rooms':
        versions = versions.filter(lessons__room=obj).distinct()
    if versions.filter(effective_from__lte=day,effective_to__gte=day).exists():
        return {'has_data': True, 'empty_reason': '', 'next_date': None}
    upcoming = versions.filter(effective_from__gt=day).order_by('effective_from').first()
    if upcoming:
        start = upcoming.effective_from
        next_day = start + timedelta(days=(day.weekday()-start.weekday())%7)
        return {'has_data': False, 'empty_reason': f'Расписание действует с {start:%d.%m.%Y}.',
                'next_date': (next_day if next_day<=upcoming.effective_to else start).isoformat()}
    previous = versions.order_by('-effective_to').first()
    return {'has_data': False, 'empty_reason': f'Расписание действовало по {previous.effective_to:%d.%m.%Y}.' if previous else 'Расписание ещё не загружено.',
            'next_date': None}
