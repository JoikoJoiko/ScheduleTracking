"""Single source of truth for public schedules, previews and publication checks."""
from copy import deepcopy
import hashlib
import json
from datetime import datetime, timedelta, time
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import *
from .timing import week_info, bell_times, slot_times,lesson_limit,require_teaching_slot,curator_times
from .class_hours import is_class_hour, TITLE


def audit(user, action, obj, old=None, new=None):
    AuditLog.objects.create(user=user, action=action, entity_type=obj._meta.label,
                            entity_id=str(obj.pk), old_values=old or {}, new_values=new or {})


def scope_ids(user):
    if user.is_superuser:
        return None
    try:
        return set(user.staffscope.buildings.values_list('pk', flat=True))
    except StaffScope.DoesNotExist:
        return set()


def require_buildings(user, ids):
    allowed = scope_ids(user)
    if not user.is_active or not user.is_staff or (allowed is not None and not set(ids).issubset(allowed)):
        raise PermissionDenied('Нет доступа к выбранным корпусам')


def lesson_dict(lesson, date, times=None):
    start,end=slot_times(lesson.time_slot,date,times)
    if lesson.curator_shift:
        start,end=curator_times(lesson.curator_shift)
    start=lesson.start_time or start
    end=lesson.end_time or end
    return {'id': lesson.pk, 'date': date.isoformat(), 'slot': lesson.time_slot.sort_order,
        'start': start.isoformat(timespec='minutes') if start else None,
        'end': end.isoformat(timespec='minutes') if end else None,
        'time_label':lesson.time_slot.name,
        'event_kind':'lesson' if lesson.subject.is_curricular else 'class_hour',
        'curator_shift':lesson.curator_shift,
        'subject_id': lesson.subject_id, 'subject': TITLE if is_class_hour(lesson.subject.name) else lesson.subject.name, 'teacher_id': lesson.teacher_id,
        'teacher': lesson.teacher.name, 'room_id': lesson.room_id, 'room': lesson.room.number,
        'building_id': lesson.room.building_id, 'building': lesson.room.building.name,
        'groups': [{'id': p.group_id, 'name': p.group.name, 'subgroup_id': p.subgroup_id,
                    'subgroup': p.subgroup.name if p.subgroup else ''} for p in lesson.participants.all()],
        'lesson_type': lesson.lesson_type, 'status': 'regular', 'change_id': None}


def version_on(date):
    return ScheduleVersion.objects.filter(status='published', effective_from__lte=date,
        effective_to__gte=date).order_by('-published_at', '-pk').first()


def base_schedule(date, version=None):
    if version is None:
        result=[];claimed=set()
        versions=ScheduleVersion.objects.filter(status='published',effective_from__lte=date,effective_to__gte=date).order_by('-published_at','-pk').prefetch_related('groups')
        for item in versions:
            scope={g.pk for g in item.groups.all()}
            for lesson in base_schedule(date,item):
                lesson['groups']=[g for g in lesson['groups'] if g['id'] not in claimed and (not scope or g['id'] in scope)]
                if lesson['groups']:
                    result.append(lesson)
            if not scope:
                break
            claimed.update(scope)
        return result
    if not version or not version.effective_from <= date <= version.effective_to:
        return []
    calendar = CalendarDay.objects.filter(date=date, year=version.period.year).first()
    if calendar and calendar.is_holiday:
        return []
    weekday = calendar.weekday_override if calendar and calendar.weekday_override is not None else date.weekday()
    parity = week_info(date,version.period.year,calendar)['type']
    lessons = version.lessons.filter(day_of_week=weekday, week_type__in=['all', parity]).select_related(
        'subject', 'teacher', 'room__building', 'time_slot').prefetch_related('participants__group', 'participants__subgroup')
    times=bell_times(date)
    return [lesson_dict(x, date,times) for x in lessons]


def apply_change(change, base):
    result = deepcopy(base) if base else {'id': None, 'groups': [], 'lesson_type': '', 'date': change.target_date.isoformat()}
    result['original'] = deepcopy(base) if base else None
    result['change_id'] = change.pk
    result['reason'] = change.reason
    result['status'] = {'cancel':'cancelled', 'move':'moved', 'add':'added', 'replace':'changed'}[change.kind]
    result['date'] = (change.new_date if change.kind == 'move' else change.target_date).isoformat()
    for field in ['subject', 'teacher', 'room']:
        obj = getattr(change, field)
        if obj:
            result[field + '_id'] = obj.pk
            result[field] = obj.number if field == 'room' else obj.name
            if field=='subject':
                result['event_kind']='lesson' if obj.is_curricular else 'class_hour'
            if field == 'room':
                result.update(building_id=obj.building_id, building=obj.building.name)
    if change.time_slot or (change.kind=='move' and change.original_id):
        slot=change.time_slot or change.original.time_slot
        start,end=slot_times(slot,change.new_date if change.kind=='move' else change.target_date)
        if slot.sort_order==0 and change.original_id and change.original.curator_shift:
            start,end=curator_times(change.original.curator_shift)
        if not change.time_slot and change.original.start_time:
            start,end=change.original.start_time,change.original.end_time
        result.update(slot=slot.sort_order,time_label=slot.name,
            start=start.isoformat(timespec='minutes') if start else None,
            end=end.isoformat(timespec='minutes') if end else None)
    if change.start_time:
        result.update(start=change.start_time.isoformat(timespec='minutes'), end=change.end_time.isoformat(timespec='minutes'))
    if change.group:
        result['groups'] = [{'id': change.group_id, 'name': change.group.name, 'subgroup_id': change.subgroup_id,
                             'subgroup': change.subgroup.name if change.subgroup else ''}]
    elif change.subgroup or change.whole_group:
        for group in result['groups']:
            group.update(subgroup_id=change.subgroup_id, subgroup=change.subgroup.name if change.subgroup else '')
    return result


def split_change_target(lesson,change):
    """Separate the affected participants without losing a joint lesson or the other subgroup."""
    selected=deepcopy(lesson);selected['groups']=[]
    remaining=deepcopy(lesson);remaining['groups']=[]
    for participant in lesson['groups']:
        if participant['id']!=change.target_group_id:
            remaining['groups'].append(participant);continue
        if not change.target_subgroup_id:
            selected['groups'].append(participant);continue
        if participant['subgroup_id']==change.target_subgroup_id:
            selected['groups'].append(participant)
        elif participant['subgroup_id']:
            remaining['groups'].append(participant)
        else:
            for subgroup in Subgroup.objects.filter(group_id=participant['id']):
                target=selected if subgroup.pk==change.target_subgroup_id else remaining
                target['groups'].append({**participant,'subgroup_id':subgroup.pk,'subgroup':subgroup.name})
    return selected,remaining


def schedule_on(date, preview=None, version=None, omit_change=None):
    def prospective(day):
        rows=base_schedule(day,version)
        if version:
            scope=set(version.groups.values_list('pk',flat=True))
            if scope:
                for existing in base_schedule(day):
                    existing['groups']=[g for g in existing['groups'] if g['id'] not in scope]
                    if existing['groups']:
                        rows.append(existing)
        return rows
    lessons = prospective(date)
    changes = list(Change.objects.filter(status='published').select_related('original__version__period__year',
        'original__subject', 'original__teacher', 'original__room__building', 'original__time_slot',
        'subject', 'teacher', 'room__building', 'time_slot', 'group', 'subgroup').prefetch_related(
        'original__participants__group', 'original__participants__subgroup').order_by('published_at', 'pk'))
    if omit_change:
        changes = [x for x in changes if x.pk != omit_change]
    if preview:
        changes = [x for x in changes if x.pk != preview.pk] + [preview]
    # Latest published revision wins, including when a move changes destination twice.
    latest = {}
    for change in changes:
        latest[(change.original_id, change.target_date,change.target_group_id,change.target_subgroup_id) if change.original_id else ('add', change.pk)] = change
    for change in sorted(latest.values(),key=lambda x:(x==preview,x.published_at or timezone.now(),x.pk or 0)):
        if change.target_date != date and change.new_date != date:
            continue
        base = next((x for x in lessons if x['id'] == change.original_id), None) if change.original_id else None
        if change.original_id:
            source = prospective(change.target_date)
            base = next((x for x in source if x['id'] == change.original_id), None)
            if not base:
                continue
            if change.target_group_id:
                base,_=split_change_target(base,change)
                if not base['groups']:
                    continue
                kept=[]
                for item in lessons:
                    if item['id']==change.original_id and item['date']==change.target_date.isoformat():
                        _,item=split_change_target(item,change)
                        if item['groups']:kept.append(item)
                    else:kept.append(item)
                lessons=kept
            else:
                lessons = [x for x in lessons if x['id'] != change.original_id or x['date'] != change.target_date.isoformat()]
        changed = apply_change(change, base)
        if change.kind == 'move' and change.target_date == date and changed['date'] != date.isoformat():
            marker = deepcopy(base)
            marker.update(status='moved_out', change_id=change.pk, new_date=changed['date'], original=deepcopy(base), reason=change.reason)
            lessons.append(marker)
        if changed['date'] == date.isoformat():
            lessons.append(changed)
    # Off-site practice suppresses only its group, preserving joint lessons for others.
    away=set(PracticePeriod.objects.filter(is_active=True,start_date__lte=date,end_date__gte=date).values_list('group_id',flat=True))
    limit=lesson_limit(date)
    result=[]
    for lesson in lessons:
        if limit is not None and lesson['slot']>limit:
            continue
        lesson['groups']=[g for g in lesson['groups'] if g['id'] not in away]
        if lesson['groups']:
            result.append(lesson)
    return sorted(result, key=lambda x: (x['start'] or '', x['slot'], x.get('id') or 0))


def live(lesson):
    return lesson['status'] not in ['cancelled', 'moved_out']


def overlaps(start, end, other_start, other_end):
    return start < other_end and other_start < end


def room_overlap_key(lesson):
    """Bind an exception to exact participants, resources, time and replacement IDs."""
    snapshot={key:lesson.get(key) for key in ['id','change_id','date','slot','start','end','subject_id','teacher_id','room_id']}
    snapshot['groups']=sorted((g['id'],g.get('subgroup_id') or 0) for g in lesson['groups'])
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()


def overlap_approval_check(lessons):
    approvals={c['pk']:c for c in Change.objects.filter(pk__in={x['change_id'] for x in lessons if x.get('change_id')})
        .values('pk','room_overlap_approvals','teacher_overlap_approvals')}
    keys={id(x):room_overlap_key(x) for x in lessons}
    def approved(a,b,field):
        # Each resource needs its own decision for these exact two lessons.
        return any(p.get('lesson')==keys[id(own)] and p.get('other')==keys[id(other)]
            for own,other in [(a,b),(b,a)] for p in approvals.get(own.get('change_id'),{}).get(field,[]))
    return approved


def joint_class_hour(a,b):
    return (a['slot']==b['slot']==0 and a.get('curator_shift') and a.get('event_kind')==b.get('event_kind')=='class_hour'
        and all(a.get(key)==b.get(key) for key in ['slot','start','end','curator_shift','subject_id','teacher_id','room_id']))


def annotate_joint_groups(lessons):
    """Describe shared attendance without changing participants or occupancy checks."""
    active=[x for x in lessons if live(x)]
    approved=overlap_approval_check(active)
    participants={id(x):{(g['id'],g.get('subgroup_id')):g for g in x['groups']} for x in active}
    pools={}
    for lesson in active:
        # Subjects may have different curriculum names for the two groups.
        key=tuple(lesson.get(k) for k in ['date','slot','start','end','teacher_id','room_id','event_kind','curator_shift'])
        pools.setdefault(key,[]).append(lesson)
    for pool in pools.values():
        for i,a in enumerate(pool):
            for b in pool[i+1:]:
                if not a['start'] or not a['end']:
                    continue
                if joint_class_hour(a,b) or (approved(a,b,'room_overlap_approvals') and approved(a,b,'teacher_overlap_approvals')):
                    for own,other in [(a,b),(b,a)]:
                        participants[id(own)].update({(g['id'],g.get('subgroup_id')):g for g in other['groups']})
    for lesson in lessons:
        groups=list(participants.get(id(lesson),{}).values())
        lesson['joint_groups']=deepcopy(groups) if len({g['id'] for g in groups})>1 else []
    return lessons


def describe_lesson(lesson):
    if not lesson:
        return ''
    groups=', '.join(g['name']+(f' (подгруппа {g["subgroup"]})' if g.get('subgroup') else '') for g in lesson['groups'])
    pair=f'{lesson["slot"]} пара' if lesson['slot'] else 'Кураторский час'
    return f'{groups} · {pair}, {lesson["start"]}–{lesson["end"]} · {lesson["subject"]} · {lesson["teacher"]} · кабинет {lesson["room"]}'


def conflict_message(conflict):
    return f'{conflict["date"]}: {conflict["type"]}. {describe_lesson(conflict["lesson"])}'+(
        f' ↔ {describe_lesson(conflict["other"])}' if conflict['other'] else '')


def conflicts_for(date, lessons,include_allowed=False):
    active = [x for x in lessons if live(x) and x['start'] and x['end']]
    conflicts = []
    def add(kind, a, b=None,allowed=False):
        conflicts.append({'type': kind, 'date': date.isoformat(), 'lesson': a, 'other': b,'allowed':allowed})
    approved=overlap_approval_check(active)
    rooms = {x.pk: x for x in Room.objects.select_related('building')}
    day_start = timezone.make_aware(datetime.combine(date, time.min))
    blocks = list(RoomBlock.objects.filter(start_at__lt=day_start+timedelta(days=1), end_at__gt=day_start))
    for i, a in enumerate(active):
        room = rooms[a['room_id']]
        start = timezone.make_aware(datetime.combine(date, time.fromisoformat(a['start'])))
        end = timezone.make_aware(datetime.combine(date, time.fromisoformat(a['end'])))
        if not room.is_active or not room.building.is_active or any(b.room_id == room.pk and overlaps(start,end,b.start_at,b.end_at) for b in blocks):
            add('Аудитория недоступна', a)
        if not Period.objects.filter(start_date__lte=date, end_date__gte=date).exists():
            add('Занятие вне учебного периода', a)
        for b in active[i+1:]:
            if not overlaps(a['start'], a['end'], b['start'], b['end']):
                continue
            # A curator can lead several groups, recorded in separate course files.
            joint_hour=joint_class_hour(a,b)
            if a['room_id'] == b['room_id'] and not joint_hour:
                allowed=approved(a,b,'room_overlap_approvals')
                if include_allowed or not allowed:
                    add('Пересечение аудиторий', a, b,allowed=allowed)
            if a['teacher_id'] == b['teacher_id'] and not joint_hour:
                allowed=approved(a,b,'teacher_overlap_approvals')
                if include_allowed or not allowed:
                    add('Пересечение преподавателя', a, b,allowed=allowed)
            if any(ga['id'] == gb['id'] and (not ga['subgroup_id'] or not gb['subgroup_id'] or ga['subgroup_id'] == gb['subgroup_id']) for ga in a['groups'] for gb in b['groups']):
                add('Пересечение группы / подгруппы', a, b)
    return conflicts


def candidate_dates(version):
    date = version.effective_from
    while date <= version.effective_to:
        yield date
        date += timedelta(days=1)


def lock_schedule():
    # All publication writers lock the same rows; SQLite serializes writes itself.
    list(AcademicYear.objects.select_for_update().values_list('pk', flat=True))


@transaction.atomic
def publish_version(version, user):
    lock_schedule()
    version.refresh_from_db()
    if version.status == 'published':
        return
    version.full_clean()
    ids = set(version.lessons.values_list('room__building_id', flat=True))
    if not ids:
        raise ValidationError('Нельзя опубликовать пустое расписание')
    require_buildings(user, ids)
    for lesson in version.lessons.all():
        if not lesson.participants.exists():
            raise ValidationError(f'У занятия {lesson.pk} не указаны группы')
        for p in lesson.participants.all():
            p.full_clean()
            if not GroupYear.objects.filter(group=p.group, year=version.period.year, status='active').exists():
                raise ValidationError(f'Группа {p.group} не активна в выбранном учебном году')
    # Versions are complete snapshots, so a scoped dispatcher cannot replace another building's data.
    for old in ScheduleVersion.objects.filter(status='published', effective_from__lte=version.effective_to, effective_to__gte=version.effective_from):
        require_buildings(user, set(old.lessons.values_list('room__building_id', flat=True)))
    for date in candidate_dates(version):
        conflicts = conflicts_for(date, schedule_on(date, version=version))
        if conflicts:
            raise ValidationError(conflict_message(conflicts[0]))
    old = version.status
    version.status = 'published'
    version.published_at = timezone.now()
    version.save()
    audit(user, 'publish_version', version, {'status': old}, {'status': 'published'})


def change_buildings(change):
    ids = set()
    if change.original_id:
        ids.add(change.original.room.building_id)
    if change.room_id:
        ids.add(change.room.building_id)
    return ids


@transaction.atomic
def publish_change(change, user):
    lock_schedule()
    change.refresh_from_db()
    if change.status == 'published':
        return
    change.full_clean()
    require_buildings(user, change_buildings(change))
    if change.group_id:
        require_buildings(user, [change.group.building_id])
    if change.target_group_id:
        require_buildings(user,[change.target_group.building_id])
    if change.original_id and not any(x['id'] == change.original_id for x in base_schedule(change.target_date)):
        raise ValidationError('Исходного занятия нет в актуальном расписании на эту дату')
    dates = {change.target_date, change.new_date or change.target_date}
    group_ids=[change.target_group_id or change.group_id] if change.target_group_id or change.group_id else list(change.original.participants.values_list('group_id',flat=True))
    for day in dates:
        if PracticePeriod.objects.filter(group_id__in=group_ids,is_active=True,start_date__lte=day,end_date__gte=day).exists():
            raise ValidationError('На эту дату у группы назначена практика')
    slot=change.time_slot or (change.original.time_slot if change.original_id else None)
    if slot:
        require_teaching_slot(change.new_date or change.target_date,slot.sort_order)
    if change.original_id and change.new_date and not change.original.version.period.year.start_date <= change.new_date <= change.original.version.period.year.end_date:
        raise ValidationError('Перенос должен находиться в том же учебном году')
    for date in dates:
        proposed = schedule_on(date, preview=change)
        for lesson in proposed:
            if lesson['change_id'] == change.pk and live(lesson):
                year = AcademicYear.objects.filter(start_date__lte=date,end_date__gte=date).first()
                if not year or any(not GroupYear.objects.filter(year=year,group_id=g['id'],status='active').exists() for g in lesson['groups']):
                    raise ValidationError('Группа не активна в выбранном учебном году')
        errors = conflicts_for(date, proposed)
        if errors:
            raise ValidationError(conflict_message(errors[0]))
    change.status = 'published'
    change.published_at = timezone.now()
    change.save()
    audit(user, 'publish_change', change, {'status':'draft'}, {'status':'published'})


@transaction.atomic
def withdraw_change(change, user):
    lock_schedule()
    require_buildings(user, change_buildings(change))
    for date in {change.target_date, change.new_date or change.target_date}:
        if conflicts_for(date, schedule_on(date, omit_change=change.pk)):
            raise ValidationError('Откат создаёт конфликт. Сначала исправьте расписание.')
    change.status = 'withdrawn'
    change.save(update_fields=['status'])
    audit(user, 'withdraw_change', change)


@transaction.atomic
def rollback_version(version, user):
    if not user.is_superuser:
        raise PermissionDenied('Откат версии доступен администратору')
    lock_schedule()
    version.refresh_from_db()
    version.status = 'archived'
    version.save(update_fields=['status'])
    for date in candidate_dates(version):
        errors = conflicts_for(date, schedule_on(date))
        if errors:
            raise ValidationError(f'Откат создаёт конфликт: {date} {errors[0]["type"]}')
    audit(user, 'rollback_version', version)


def availability(date, start, end, rooms=None):
    lessons = [x for x in schedule_on(date) if live(x) and x['start'] and x['end']]
    day_start = timezone.make_aware(datetime.combine(date, time.min))
    blocks = list(RoomBlock.objects.filter(start_at__lt=day_start+timedelta(days=1), end_at__gt=day_start))
    begin = timezone.make_aware(datetime.combine(date, start))
    finish = timezone.make_aware(datetime.combine(date, end))
    has_data = version_on(date) is not None
    result = []
    for room in rooms if rooms is not None else Room.objects.select_related('building', 'room_type'):
        own = [x for x in lessons if x['room_id'] == room.pk]
        busy = [x for x in own if overlaps(start.isoformat(timespec='minutes'), end.isoformat(timespec='minutes'), x['start'], x['end'])]
        blocked = [b for b in blocks if b.room_id == room.pk and overlaps(begin, finish, b.start_at, b.end_at)]
        status = 'unavailable' if not room.is_active or not room.building.is_active or blocked else 'busy' if busy else 'free' if has_data else 'unknown'
        result.append({'id':room.pk, 'number':room.number, 'building_id':room.building_id, 'building':room.building.name,
            'floor':room.floor, 'capacity':room.capacity, 'room_type':room.room_type.name, 'room_type_id':room.room_type_id,
            'description':room.description, 'status':status, 'lessons':busy, 'blocks':[b.reason for b in blocked],
            'next':next((x for x in own if x['start'] >= end.isoformat(timespec='minutes')), None)})
    return result


def free_intervals(lessons, date, room_id=None):
    slots = [(a,b) for a,b in bell_times(date).values() if a and b]
    if not slots:
        return []
    start, finish = min(x[0] for x in slots), max(x[1] for x in slots)
    occupied = [(time.fromisoformat(x['start']), time.fromisoformat(x['end'])) for x in lessons if live(x) and x['start'] and x['end']]
    if room_id:
        a = timezone.make_aware(datetime.combine(date, start))
        b = timezone.make_aware(datetime.combine(date, finish))
        for block in RoomBlock.objects.filter(room_id=room_id, start_at__lt=b, end_at__gt=a):
            occupied.append((max(start, timezone.localtime(block.start_at).time()) if timezone.localdate(block.start_at) == date else start,
                             min(finish, timezone.localtime(block.end_at).time()) if timezone.localdate(block.end_at) == date else finish))
    gaps, cursor = [], start
    for a, b in sorted(occupied):
        a,b=max(a,start),min(b,finish)
        if a>=b:
            continue
        if cursor < a:
            gaps.append({'start':cursor.isoformat(timespec='minutes'), 'end':min(a,finish).isoformat(timespec='minutes')})
        cursor = max(cursor, b)
    if cursor < finish:
        gaps.append({'start':cursor.isoformat(timespec='minutes'), 'end':finish.isoformat(timespec='minutes')})
    return gaps


def lesson_windows(lessons,date):
    """A window is a vacant pair between lessons, never a scheduled break."""
    active=[x for x in lessons if live(x) and x.get('start') and x.get('end') and x.get('slot',0)>0]
    if len(active)<2:
        return []
    first,last=min(x['start'] for x in active),max(x['end'] for x in active)
    times=bell_times(date)
    result=[]
    for slot in TimeSlot.objects.filter(sort_order__gt=0):
        start,end=times.get(slot.pk,(None,None))
        if not start or not end:
            continue
        start,end=start.isoformat(timespec='minutes'),end.isoformat(timespec='minutes')
        if start<first or end>last or any(overlaps(start,end,x['start'],x['end']) for x in active):
            continue
        result.append({'slot':slot.sort_order,'start':start,'end':end})
    return result


def rollover_preview(source, target):
    rows = []
    for membership in GroupYear.objects.filter(year=source).select_related('group'):
        graduated = membership.status == 'active' and (membership.group.graduation_year <= target.start_date.year or membership.course>=4)
        rows.append({'group_id':membership.group_id, 'name':membership.group.name,
            'course':membership.course+1 if membership.status == 'active' and not graduated else membership.course,
            'status':'graduated' if graduated else membership.status})
    return rows


@transaction.atomic
def rollover(source, target, user):
    if not user.is_superuser:
        raise PermissionDenied
    lock_schedule()
    if target.start_date <= source.end_date:
        raise ValidationError('Новый год должен начинаться после предыдущего')
    if GroupYear.objects.filter(year=target).exists():
        raise ValidationError('В новом году уже есть группы. Повторный перевод запрещён.')
    for row in rollover_preview(source, target):
        GroupYear.objects.create(year=target, group_id=row['group_id'], course=row['course'], status=row['status'])
    audit(user, 'rollover', target, new={'source':source.pk})


@transaction.atomic
def activate_year(year, user):
    if not user.is_superuser:
        raise PermissionDenied
    lock_schedule()
    AcademicYear.objects.filter(status='active').exclude(pk=year.pk).update(status='archived')
    year.status = 'active'
    year.save(update_fields=['status'])
    for membership in GroupYear.objects.filter(year=year).select_related('group'):
        membership.group.status = membership.status
        membership.group.save(update_fields=['status'])
    audit(user, 'activate_year', year)
