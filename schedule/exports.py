"""Printable matrices and editable Word documents share the same schedule snapshot."""
from datetime import date,timedelta
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,render
from django.utils import timezone
from django.views.decorators.http import require_GET
from . import services
from .models import StudentGroup,ScheduleVersion,PracticePeriod,WEEKDAYS
from .curriculum import curriculum_for,schedule_coverage
from .timing import week_info,regular_bell_times
from .word_export import word_document,W


def day_text(day):
    return f'{day:%d.%m.%Y}'


def lesson_text(lesson,group_id=None):
    names=[]
    if lesson.get('start'):
        names.append(f'{lesson["start"]}–{lesson["end"]}')
    subgroups=[g['subgroup'] for g in lesson['groups'] if g.get('subgroup') and (group_id is None or g['id']==group_id)]
    if subgroups:
        names.append('Подгруппа '+', '.join(dict.fromkeys(subgroups)))
    names.extend([lesson['subject'],lesson.get('teacher',''),'Каб. '+lesson.get('room','')])
    labels={'changed':'Замена','added':'Доп. занятие','cancelled':'Отменено','moved':'Перенос','moved_out':'Перенесено'}
    if lesson.get('status') in labels:
        names.append(labels[lesson['status']])
    original=lesson.get('original')
    if original and lesson.get('status') in ['changed','moved']:
        old_time=' · '+original['start']+'–'+original['end'] if original.get('start') else ''
        names.append('Вместо: '+original['subject']+' · '+original['teacher']+' · каб. '+original['room']+old_time)
    if lesson.get('new_date'):
        names.append('На '+day_text(date.fromisoformat(lesson['new_date'])))
    joint=lesson.get('joint_groups',lesson['groups'])
    others=[g['name']+(' · подгр. '+g['subgroup'] if g.get('subgroup') else '') for g in joint if g['id']!=group_id]
    if len({g['id'] for g in joint})>1:
        names.append('Объединено с: '+', '.join(dict.fromkeys(others)))
    if lesson.get('reason'):
        names.append(lesson['reason'])
    return '\n'.join(n for n in names if n)


def week_matrix(days,group,title):
    used={l['slot'] for d in days for l in d['lessons']}
    numbered=sorted(n for n in used if n>0)
    # Keep internal vacant pairs; omit rows unused throughout the entire week.
    slots=([0] if 0 in used else [])+(list(range(numbered[0],numbered[-1]+1)) if numbered else [])
    slots=slots or [1]
    rows=[]
    for number in slots:
        cells=['Занятия' if not used else 'Кураторский час' if number==0 else f'{number} пара']
        for day in days:
            records=[l for l in day['lessons'] if l['slot']==number]
            text='\n\n'.join(lesson_text(l,group.pk) for l in records)
            if not text and day.get('practice') and number==slots[0]:
                text=day['practice']['label']
            if not text and day.get('missing') and number==slots[0]:
                text='Нет расписания\n'+day['missing']
            cells.append(text or '—')
        rows.append(cells)
    return {'title':title,'headers':['Пара']+[d['label'] for d in days],'rows':rows}


def group_report(group,day,view):
    if view=='base':
        info=curriculum_for(group,day)
        if not info.get('version'):
            raise ValidationError('У группы нет расписания на выбранный учебный период')
        version=ScheduleVersion.objects.select_related('period__year').get(pk=info['version']['id'])
        # A semester pattern uses regular weekday bells, without temporary date exceptions.
        monday=version.effective_from-timedelta(days=version.effective_from.weekday())
        sections=[]
        for number,parity in [(1,'odd'),(2,'even')]:
            representative=monday
            while week_info(representative,version.period.year)['number']!=number:
                representative+=timedelta(days=7)
                if representative>version.period.year.end_date:
                    raise ValidationError('Настройте отсчёт недель учебного года')
            days=[]
            for weekday,name in WEEKDAYS[:6]:
                target=representative+timedelta(days=weekday)
                lessons=version.lessons.filter(day_of_week=weekday,week_type__in=['all',parity],participants__group=group).distinct().select_related(
                    'subject','teacher','room__building','time_slot').prefetch_related('participants__group','participants__subgroup')
                days.append({'label':name,'lessons':[services.lesson_dict(l,target,regular_bell_times(target)) for l in lessons]})
            sections.append(week_matrix(days,group,f'{number}-я неделя'))
        return {'title':'Расписание группы '+group.name,'subtitle':f'{version.period.name} · {day_text(version.effective_from)}–{day_text(version.effective_to)}',
            'sections':sections,'filename':f'group-{group.pk}-base-{day}.docx'}
    if view=='day' and day.weekday()==6:
        raise ValidationError('Воскресенье не входит в учебную неделю')
    monday=day-timedelta(days=day.weekday())
    targets=[day] if view=='day' else [monday+timedelta(days=i) for i in range(6)]
    days=[]
    for target in targets:
        # Use the final service result, including cancellations and scoped subgroup changes.
        lessons=[l for l in services.annotate_joint_groups(services.schedule_on(target)) if any(g['id']==group.pk for g in l['groups'])
            or any(g['id']==group.pk for g in (l.get('original') or {}).get('groups',[]))]
        practice=PracticePeriod.objects.filter(group=group,is_active=True,start_date__lte=target,end_date__gte=target).first()
        coverage=schedule_coverage('groups',group,target)
        days.append({'date':target.isoformat(),'label':f'{WEEKDAYS[target.weekday()][1]}\n{target:%d.%m}',
            'lessons':[] if practice else lessons,'practice':{'label':f'{practice.title} до {practice.end_date:%d.%m.%Y}'} if practice else None,
            'missing':coverage['empty_reason'] if not coverage['has_data'] and not practice else ''})
    start=date.fromisoformat(days[0]['date']);end=date.fromisoformat(days[-1]['date'])
    return {'title':'Расписание группы '+group.name,'subtitle':f'{day_text(start)}–{day_text(end)} · {week_info(day)["label"]}',
        'sections':[week_matrix(days,group,'')],'filename':f'group-{group.pk}-{start}-{end}.docx'}


def changes_report(day,group_id=None):
    lessons=[l for l in services.annotate_joint_groups(services.schedule_on(day)) if l.get('change_id')]
    base=services.base_schedule(day)
    if group_id is not None:
        lessons=[l for l in lessons if any(g['id']==group_id for g in l['groups'])]
    groups={}
    for lesson in lessons:
        for group in lesson['groups']:
            if group_id is not None and group['id']!=group_id:
                continue
            key=(group['id'],group.get('subgroup_id'))
            entry=groups.setdefault(key,{'group':group,'lessons':{}})
            entry['lessons'].setdefault(lesson['slot'],[]).append(lesson)
    admissions=dict(StudentGroup.objects.filter(pk__in={k[0] for k in groups}).values_list('pk','admission_year'))
    numbers=([0] if any(l['slot']==0 for l in lessons) else [])+list(range(1,8))
    rows=[]
    for key,entry in sorted(groups.items(),key=lambda pair:(-admissions[pair[0][0]],pair[1]['group']['name'],pair[1]['group'].get('subgroup',''))):
        group=entry['group'];name=group['name']+('\nПодгруппа '+group['subgroup'] if group.get('subgroup') else '')
        cells=[name]
        for n in numbers:
            text='\n\n'.join(lesson_text(l,group['id']) for l in entry['lessons'].get(n,[]))
            regular=any(l['slot']==n and any(g['id']==group['id'] and (not group.get('subgroup_id') or not g.get('subgroup_id')
                or g['subgroup_id']==group['subgroup_id']) for g in l['groups']) for l in base)
            cells.append(text or ('По расписанию' if regular else '—'))
        rows.append(cells)
    return {'title':'Замены занятий','subtitle':f'{day_text(day)} · {WEEKDAYS[day.weekday()][1]} · {week_info(day)["label"]}',
        'sections':[{'title':'','headers':['Группа']+['Кураторский час' if n==0 else f'{n} пара' for n in numbers],
                     'rows':rows or [['Замен нет']+['—']*len(numbers)]}], 'filename':f'changes-{day}.docx'}


@require_GET
def export(request,kind,pk=None):
    from .views import error
    try:
        day=date.fromisoformat(request.GET['date']) if request.GET.get('date') else timezone.localdate()
        fmt=request.GET.get('format','docx');view=request.GET.get('view','week')
        if fmt not in ['docx','print'] or view not in ['day','week','base']:
            raise ValidationError('Неизвестный формат выгрузки')
        if kind=='groups':
            report=group_report(get_object_or_404(StudentGroup,pk=pk),day,view)
        elif kind=='changes':
            group=int(request.GET['group']) if request.GET.get('group') else None
            report=changes_report(day,group)
        else:
            raise ValidationError('Неизвестная выгрузка')
    except (ValidationError,ValueError) as exc:
        return error('export','; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная дата или группа',400)
    if fmt=='print':
        return render(request,'print_schedule.html',{'report':report,'word_url':request.path+'?'+request.GET.copy().urlencode().replace('format=print','format=docx')})
    response=HttpResponse(word_document(report),content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    response['Content-Disposition']='attachment; filename="'+report['filename']+'"'
    response['Cache-Control']='no-store'
    return response
