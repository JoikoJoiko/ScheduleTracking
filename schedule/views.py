import json
import logging
from django.conf import settings
from datetime import date, datetime, time, timedelta
from functools import wraps
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db import connection, DatabaseError
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.http import Http404
from django.utils import timezone
from .models import *
from . import services as svc
from .importing import preview_import, confirm_import, import_report
from .timing import week_info, bells_for, slot_times, short_day_info, curator_bells_for
from .curriculum import curriculum_for, schedule_coverage


def page(request, **kwargs):
    return render(request,'index.html')


@require_GET
@never_cache
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({'status':'unavailable'},status=503)
    return JsonResponse({'status':'ok'})


def api(fn):
    @wraps(fn)
    def wrapper(request,*args,**kwargs):
        try:
            return fn(request,*args,**kwargs)
        except PermissionDenied as exc:
            return error('forbidden',str(exc) or 'Недостаточно прав',403)
        except Http404:
            return error('not_found','Запись не найдена',404)
        except (ValidationError,ValueError,TypeError,KeyError, json.JSONDecodeError) as exc:
            message='; '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
            return error('validation',message,400)
        except Exception:
            logging.getLogger(__name__).exception('API failure')
            return error('internal','Внутренняя ошибка. Подробности в журнале сервера.',500)
    return wrapper


def error(code,message,status):
    return JsonResponse({'error':{'code':code,'message':message}},status=status,json_dumps_params={'ensure_ascii':False})


def csrf_failure(request,reason=''):
    if request.path.startswith('/api/'):
        return error('csrf','Не удалось проверить CSRF-токен. Обновите страницу и повторите запрос.',403)
    from django.views.csrf import csrf_failure as django_csrf_failure
    return django_csrf_failure(request,reason=reason)


def data_response(rows,request,**extra):
    page_number=max(1,int(request.GET.get('page',1)))
    size=max(1,min(100,int(request.GET.get('page_size',50))))
    return JsonResponse({'results':rows[(page_number-1)*size:page_number*size], 'count':len(rows),
        'page':page_number,'page_size':size,**extra},json_dumps_params={'ensure_ascii':False})


def selected_date(request):
    return date.fromisoformat(request.GET['date']) if request.GET.get('date') else timezone.localdate()


def matches(name, query):
    tokens=query.casefold().replace('ё','е').replace('.',' ').split()
    words=name.casefold().replace('ё','е').split()
    return all(any(w.startswith(t) or t in w for w in words) for t in tokens)


@api
def meta(request):
    version=ScheduleVersion.objects.filter(status='published').order_by('-published_at').first()
    change=Change.objects.filter(status='published').order_by('-published_at').first()
    stamps=[x.published_at for x in [version,change] if x]
    last_operation=AuditLog.objects.filter(action__in=['publish_version','publish_change','withdraw_change','rollback_version','activate_year',
        'edit_bells','reset_bells','edit_short_day','disable_short_day','edit_practice','disable_practice','repair_class_hours']).first()
    if last_operation:
        stamps.append(last_operation.created_at)
    organization=SystemSetting.objects.filter(key='organization').values_list('value',flat=True).first() or 'Расписание колледжа'
    return JsonResponse({'organization':organization,'version':settings.SITE_VERSION,'today':timezone.localdate().isoformat(),'now':timezone.localtime().isoformat(),
        'updated_at':max(stamps).isoformat() if stamps else None,
        'buildings':list(Building.objects.values('id','name')),
        'room_types':list(RoomType.objects.values('id','name')),
        'specialties':list(Specialty.objects.values('id','name','code')),
        'slots':bells_for(selected_date(request)),
        'curator_bells':curator_bells_for(selected_date(request)),
        'shortened_day':short_day_info(selected_date(request)),
        'week':week_info(selected_date(request)), 'current_week':week_info(timezone.localdate()),
        'periods':list(Period.objects.values('id','name','year_id','start_date','end_date').order_by('-start_date','end_date','-pk')),
        'years':list(AcademicYear.objects.values('id','name','status')),
        'staff':request.user.is_active and request.user.is_staff,
        'manage_bells':request.user.is_active and request.user.is_superuser})


@api
def bells(request):
    day=selected_date(request)
    if request.GET.get('weekday'):
        weekday=int(request.GET['weekday'])
        if weekday not in range(6):
            raise ValidationError('День недели от 0 до 5 (понедельник — суббота)')
        day=day-timedelta(days=day.weekday())+timedelta(days=weekday)
    return JsonResponse({'date':day.isoformat(),'weekday':day.weekday(),'slots':bells_for(day),
        'curator_bells':curator_bells_for(day),'week':week_info(day),'shortened_day':short_day_info(day)})


@api
def subjects(request):
    group=get_object_or_404(StudentGroup,pk=request.GET.get('group'))
    period=get_object_or_404(Period,pk=request.GET['period']) if request.GET.get('period') else None
    result=curriculum_for(group,selected_date(request),period)
    if request.GET.get('q'):
        result['subjects']=[x for x in result['subjects'] if matches(x['name'],request.GET['q'])]
    return JsonResponse(result,json_dumps_params={'ensure_ascii':False})


@api
def image_candidates(request):
    denied=staff(request)
    if denied:
        return denied
    from .image_import import infer_row
    group=get_object_or_404(StudentGroup,pk=request.GET.get('group'))
    svc.require_buildings(request.user,[group.building_id])
    day=selected_date(request)
    result=curriculum_for(group,day)
    row={key+'_text':request.GET.get(key,'')[:300] for key in ['subject','teacher','room']}
    row['slot']=int(request.GET['slot']) if request.GET.get('slot','').isdigit() else None
    result['suggestions']=infer_row(group.pk,day,row,result)
    return JsonResponse(result,json_dumps_params={'ensure_ascii':False})


@api
def catalog(request,kind):
    query=request.GET.get('q','').strip()[:150]
    archive=request.GET.get('archive')=='1'
    rows=[]
    if kind=='groups':
        groups=StudentGroup.objects.select_related('specialty','building')
        active=AcademicYear.objects.filter(status='active').first()
        year_id=request.GET.get('year') or (active.pk if active and not archive else None)
        memberships={x.group_id:x for x in GroupYear.objects.filter(year_id=year_id)} if year_id else {}
        for x in groups:
            membership=memberships.get(x.pk)
            status=membership.status if membership else x.status
            if not archive and (status!='active' or (year_id and not membership)):
                continue
            if request.GET.get('course') and (not membership or membership.course!=int(request.GET['course'])):
                continue
            if request.GET.get('specialty') and x.specialty_id!=int(request.GET['specialty']):
                continue
            if request.GET.get('building') and x.building_id!=int(request.GET['building']):
                continue
            if matches(x.name,query):
                rows.append({'id':x.pk,'name':x.name,'specialty':x.specialty.name if x.specialty else '', 'building':x.building.name,'status':status,
                    'course':membership.course if membership else None,'admission_year':x.admission_year})
    elif kind=='teachers':
        for x in Teacher.objects.all():
            if (archive or x.is_active) and matches(x.name,query):
                rows.append({'id':x.pk,'name':x.name})
    elif kind=='rooms':
        for x in Room.objects.select_related('building','room_type'):
            if matches(x.number+' '+x.building.name,query):
                rows.append({'id':x.pk,'name':x.number,'building':x.building.name,'type':x.room_type.name,'capacity':x.capacity,'active':x.is_active})
    else:
        raise Http404
    if kind=='groups':
        # Keep course order even during search; the intake suffix is chronological.
        rows.sort(key=lambda x:(x['course'] or 99,-x['admission_year'],x['name']))
    else:
        rows.sort(key=lambda x:(not x['name'].casefold().startswith(query.casefold()),x['name']))
    return data_response(rows,request)


@api
def schedule(request,kind,pk):
    model={'groups':StudentGroup,'teachers':Teacher,'rooms':Room}.get(kind)
    if not model:
        raise Http404
    obj=get_object_or_404(model,pk=pk)
    date_=selected_date(request)
    dates=[date_] if date_.weekday()<6 else []
    if request.GET.get('view')=='week':
        monday=date_-timedelta(days=date_.weekday())
        dates=[monday+timedelta(days=i) for i in range(6)]
    days=[]
    for day in dates:
        lessons=svc.schedule_on(day)
        if kind=='groups':
            lessons=[x for x in lessons if any(g['id']==pk for g in x['groups']) or any(g['id']==pk for g in (x.get('original') or {}).get('groups',[]))]
        elif kind=='teachers':
            lessons=[x for x in lessons if x['teacher_id']==pk or (x.get('original') or {}).get('teacher_id')==pk]
            for lesson in lessons:
                if lesson['teacher_id']!=pk:
                    lesson['status']='reassigned'
        else:
            lessons=[x for x in lessons if x['room_id']==pk or (x.get('original') or {}).get('room_id')==pk]
            for lesson in lessons:
                if lesson['room_id']!=pk:
                    lesson['status']='reassigned'
        gaps=svc.free_intervals([x for x in lessons if x['status']!='reassigned'],day,pk if kind=='rooms' else None)
        now=timezone.localtime()
        next_found=False
        for lesson in lessons:
            lesson['current']=bool(lesson['start'] and day==now.date() and svc.live(lesson) and lesson['status']!='reassigned' and lesson['start']<=now.strftime('%H:%M')<lesson['end'])
            lesson['next']=bool(lesson['start'] and day==now.date() and not next_found and svc.live(lesson) and lesson['status']!='reassigned' and lesson['start']>now.strftime('%H:%M'))
            if lesson['next']:
                next_found=True
        coverage=schedule_coverage(kind,obj,day)
        practice=PracticePeriod.objects.filter(group=obj,is_active=True,start_date__lte=day,end_date__gte=day).first() if kind=='groups' else None
        practice_info=None
        if practice:
            lessons=[]
            practice_info={'id':practice.pk,'title':practice.title,'end':practice.end_date.isoformat(),
                           'start':practice.start_date.isoformat(),'organization':practice.organization,
                           'label':f'{practice.title} до {practice.end_date:%d.%m.%Y}'}
            coverage.update(has_data=True,empty_reason='',next_date=None)
        days.append({'date':day.isoformat(),'lessons':lessons,'free_intervals':gaps if coverage['has_data'] else [],
                     'windows':svc.lesson_windows([x for x in lessons if x['status']!='reassigned'],day),
                     'practice':practice_info,'shortened_day':short_day_info(day),'week':week_info(day),**coverage})
    return JsonResponse({'id':pk,'name':obj.number if kind=='rooms' else obj.name,'kind':kind,'days':days,
                         'week':week_info(date_)},json_dumps_params={'ensure_ascii':False})


@api
def room_availability(request):
    day=selected_date(request)
    now=timezone.localtime().time()
    start=time.fromisoformat(request.GET.get('start',now.strftime('%H:%M')))
    end=time.fromisoformat(request.GET.get('end',(datetime.combine(day,start)+timedelta(minutes=1)).strftime('%H:%M')))
    if request.GET.get('slot'):
        slot=get_object_or_404(TimeSlot,sort_order=int(request.GET['slot']))
        start,end=slot_times(slot,day)
        if not start or not end:
            raise ValidationError('Для этой пары не задано время звонков')
    if start>=end:
        raise ValidationError('Время окончания должно быть позже начала в пределах одного дня')
    rooms=Room.objects.select_related('building','room_type')
    for query,field in [('building','building_id'),('floor','floor'),('type','room_type_id')]:
        if request.GET.get(query):
            rooms=rooms.filter(**{field:int(request.GET[query])})
    if request.GET.get('capacity'):
        rooms=rooms.filter(capacity__gte=int(request.GET['capacity']))
    if request.GET.get('q'):
        rooms=[r for r in rooms if matches(r.number+' '+r.building.name,request.GET['q'])]
    rooms=list(rooms)
    rows=svc.availability(day,start,end,rooms)
    if request.GET.get('status'):
        rows=[r for r in rows if r['status']==request.GET['status']]
    if request.GET.get('grid')=='1':
        grid={room.pk:[] for room in rooms}
        for slot in bells_for(day):
            for entry in svc.availability(day,time.fromisoformat(slot['start']),time.fromisoformat(slot['end']),rooms):
                grid[entry['id']].append({'slot':slot['number'],'status':entry['status']})
        for row in rows:
            row['grid']=grid[row['id']]
    return data_response(rows,request,date=day.isoformat(),start=start.isoformat(timespec='minutes'),end=end.isoformat(timespec='minutes'))


@api
def changes(request):
    day=selected_date(request)
    rows=[]
    for change in Change.objects.filter(status='published',target_date=day).select_related('original','room','teacher','subject'):
        base=svc.lesson_dict(change.original,change.target_date) if change.original else None
        item=svc.apply_change(change,base)
        if request.GET.get('group') and not any(g['id']==int(request.GET['group']) for g in item['groups']):
            continue
        if request.GET.get('building') and item['building_id']!=int(request.GET['building']):
            continue
        if request.GET.get('teacher') and item['teacher_id']!=int(request.GET['teacher']):
            continue
        item['published_at']=change.published_at.isoformat()
        rows.append(item)
    return data_response(rows,request)


def staff(request):
    if not request.user.is_authenticated:
        return error('unauthorized','Требуется авторизация',401)
    if not request.user.is_staff or not request.user.is_active:
        raise PermissionDenied
    return None


def body(request):
    value=json.loads(request.body or '{}')
    if not isinstance(value,dict):
        raise ValidationError('Ожидается JSON-объект')
    return value


@api
def admin_imports(request,pk=None,action=None):
    denied=staff(request)
    if denied:
        return denied
    if pk is None:
        if request.method!='POST':
            return error('method','Используйте POST',405)
        profile=get_object_or_404(ImportProfile,pk=request.POST['profile']) if request.POST.get('profile') else get_object_or_404(ImportProfile,kind='college')
        period=get_object_or_404(Period,pk=request.POST['period']) if request.POST.get('period') else None
        start=date.fromisoformat(request.POST['start']) if request.POST.get('start') else None
        end=date.fromisoformat(request.POST['end']) if request.POST.get('end') else None
        batch=preview_import(request.FILES['file'],profile,period,start,end,request.user)
        return JsonResponse({'id':batch.pk,'status':batch.status,'payload':batch.payload,'errors':batch.errors},status=201)
    batch=get_object_or_404(ImportBatch,pk=pk)
    if not request.user.is_superuser and batch.uploaded_by_id!=request.user.pk:
        raise PermissionDenied
    if action=='confirm':
        if request.method!='POST':
            return error('method','Используйте POST',405)
        version=confirm_import(batch,request.user)
        return JsonResponse({'version_id':version.pk,'status':'published'})
    if request.method!='GET':
        return error('method','Используйте GET',405)
    return JsonResponse({'id':batch.pk,'status':batch.status,'payload':batch.payload,'errors':batch.errors,'version_id':batch.version_id,'report':import_report(batch)})


@api
def admin_changes(request,pk=None,action=None):
    denied=staff(request)
    if denied:
        return denied
    change=get_object_or_404(Change,pk=pk) if pk else None
    if change:
        svc.require_buildings(request.user,svc.change_buildings(change))
    if action:
        if request.method!='POST':
            return error('method','Используйте POST',405)
        if action=='publish':
            svc.publish_change(change,request.user)
        elif action=='withdraw':
            svc.withdraw_change(change,request.user)
        elif action=='preview':
            lessons=svc.schedule_on(change.new_date or change.target_date,preview=change)
            return JsonResponse({'lessons':lessons,'conflicts':svc.conflicts_for(change.new_date or change.target_date,lessons)})
        else:
            raise Http404
        return JsonResponse({'id':change.pk,'status':change.status})
    if request.method not in (['PATCH'] if change else ['POST']):
        return error('method','Используйте PATCH для черновика или POST для создания',405)
    if change and change.status!='draft':
        raise ValidationError('Опубликованная замена неизменяема. Создайте новую редакцию.')
    values=body(request)
    allowed={'target_date','original_id','kind','new_date','time_slot_id','start_time','end_time','subject_id','teacher_id','room_id','group_id','target_group_id','subgroup_id','whole_group','reason'}
    if set(values)-allowed:
        raise ValidationError('Неизвестные поля: '+', '.join(set(values)-allowed))
    change=change or Change(created_by=request.user)
    for key,value in values.items():
        setattr(change,key,value)
    change.full_clean()
    svc.require_buildings(request.user,svc.change_buildings(change))
    if change.group_id:
        svc.require_buildings(request.user,[change.group.building_id])
    change.save()
    svc.audit(request.user,'save_draft',change)
    return JsonResponse({'id':change.pk,'status':change.status},status=200 if pk else 201)


@api
def admin_conflicts(request):
    denied=staff(request)
    if denied:
        return denied
    day=selected_date(request)
    conflicts=svc.conflicts_for(day,svc.schedule_on(day),include_allowed=request.GET.get('include_allowed')=='1')
    ids=svc.scope_ids(request.user)
    if ids is not None:
        conflicts=[x for x in conflicts if x['lesson']['building_id'] in ids]
    return data_response(conflicts,request)


@api
def years(request):
    if request.method=='GET':
        return data_response(list(AcademicYear.objects.values('id','name','start_date','end_date','status')),request)
    return error('method','Используйте GET',405)


@api
def admin_years(request):
    denied=staff(request)
    if denied:
        return denied
    if not request.user.is_superuser:
        raise PermissionDenied
    if request.method!='POST':
        return error('method','Используйте POST',405)
    values=body(request)
    if set(values)-{'name','start_date','end_date'}:
        raise ValidationError('Допустимы name, start_date, end_date')
    year=AcademicYear(**values)
    year.full_clean(); year.save()
    svc.audit(request.user,'create_year',year)
    return JsonResponse({'id':year.pk},status=201)


@api
def admin_rollover(request):
    denied=staff(request)
    if denied:
        return denied
    if not request.user.is_superuser:
        raise PermissionDenied
    values=body(request) if request.method=='POST' else request.GET
    source=get_object_or_404(AcademicYear,pk=values['source'])
    target=get_object_or_404(AcademicYear,pk=values['target'])
    rows=svc.rollover_preview(source,target)
    if request.method=='POST':
        svc.rollover(source,target,request.user)
    elif request.method!='GET':
        return error('method','Используйте GET / POST',405)
    return JsonResponse({'results':rows,'confirmed':request.method=='POST'})


def conflicts_page(request):
    if not request.user.is_staff:
        from django.shortcuts import redirect
        return redirect('/admin/login/?next=/admin/conflicts/')
    try:
        day=selected_date(request)
    except ValueError:
        day=timezone.localdate()
    rows=svc.conflicts_for(day,svc.schedule_on(day),include_allowed=True)
    ids=svc.scope_ids(request.user)
    if ids is not None:
        rows=[x for x in rows if x['lesson']['building_id'] in ids]
    for row in rows:
        row['lesson_text']=svc.describe_lesson(row['lesson'])
        row['other_text']=svc.describe_lesson(row['other'])
    from django.contrib import admin
    return render(request,'admin/conflicts.html',dict(admin.site.each_context(request),title='Контроль конфликтов',rows=rows,date=day.isoformat()))
