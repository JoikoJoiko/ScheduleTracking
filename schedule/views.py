import json
import logging
from datetime import date, datetime, time, timedelta
from functools import wraps
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.http import Http404
from django.utils import timezone
from .models import *
from . import services as svc
from .importing import preview_import, confirm_import


def page(request, **kwargs):
    return render(request,'index.html')


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
    return JsonResponse({'today':timezone.localdate().isoformat(),'now':timezone.localtime().isoformat(),
        'updated_at':max(stamps).isoformat() if stamps else None,
        'buildings':list(Building.objects.values('id','name')),
        'room_types':list(RoomType.objects.values('id','name')),
        'specialties':list(Specialty.objects.values('id','name','code')),
        'slots':[{'id':x.pk,'number':x.sort_order,'name':x.name,'start':x.start_time.isoformat(timespec='minutes'),'end':x.end_time.isoformat(timespec='minutes')} for x in TimeSlot.objects.all()],
        'years':list(AcademicYear.objects.values('id','name','status')),
        'staff':request.user.is_active and request.user.is_staff})


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
                rows.append({'id':x.pk,'name':x.name,'specialty':x.specialty.name,'building':x.building.name,'status':status,'course':membership.course if membership else None})
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
    rows.sort(key=lambda x:(not x['name'].casefold().startswith(query.casefold()),x['name']))
    return data_response(rows,request)


@api
def schedule(request,kind,pk):
    model={'groups':StudentGroup,'teachers':Teacher,'rooms':Room}.get(kind)
    if not model:
        raise Http404
    obj=get_object_or_404(model,pk=pk)
    date_=selected_date(request)
    dates=[date_]
    if request.GET.get('view')=='week':
        monday=date_-timedelta(days=date_.weekday())
        dates=[monday+timedelta(days=i) for i in range(7)]
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
            lesson['current']=day==now.date() and svc.live(lesson) and lesson['status']!='reassigned' and lesson['start']<=now.strftime('%H:%M')<lesson['end']
            lesson['next']=day==now.date() and not next_found and svc.live(lesson) and lesson['status']!='reassigned' and lesson['start']>now.strftime('%H:%M')
            if lesson['next']:
                next_found=True
        days.append({'date':day.isoformat(),'lessons':lessons,'free_intervals':gaps,'has_data':bool(svc.version_on(day))})
    return JsonResponse({'id':pk,'name':obj.number if kind=='rooms' else obj.name,'kind':kind,'days':days},json_dumps_params={'ensure_ascii':False})


@api
def room_availability(request):
    day=selected_date(request)
    now=timezone.localtime().time()
    start=time.fromisoformat(request.GET.get('start',now.strftime('%H:%M')))
    end=time.fromisoformat(request.GET.get('end',(datetime.combine(day,start)+timedelta(minutes=1)).strftime('%H:%M')))
    if request.GET.get('slot'):
        slot=get_object_or_404(TimeSlot,sort_order=int(request.GET['slot']))
        start,end=slot.start_time,slot.end_time
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
    rows=svc.availability(day,start,end,rooms)
    if request.GET.get('status'):
        rows=[r for r in rows if r['status']==request.GET['status']]
    if request.GET.get('grid')=='1':
        for row in rows:
            row['grid']=[{'slot':slot.sort_order,'status':svc.availability(day,slot.start_time,slot.end_time,[Room.objects.select_related('building','room_type').get(pk=row['id'])])[0]['status']} for slot in TimeSlot.objects.all()]
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
        profile=get_object_or_404(ImportProfile,pk=request.POST['profile'])
        period=get_object_or_404(Period,pk=request.POST['period'])
        batch=preview_import(request.FILES['file'],profile,period,date.fromisoformat(request.POST['start']),date.fromisoformat(request.POST['end']),request.user)
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
    return JsonResponse({'id':batch.pk,'status':batch.status,'payload':batch.payload,'errors':batch.errors,'version_id':batch.version_id})


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
    allowed={'target_date','original_id','kind','new_date','time_slot_id','start_time','end_time','subject_id','teacher_id','room_id','group_id','subgroup_id','whole_group','reason'}
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
    conflicts=svc.conflicts_for(day,svc.schedule_on(day))
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
    rows=svc.conflicts_for(day,svc.schedule_on(day))
    ids=svc.scope_ids(request.user)
    if ids is not None:
        rows=[x for x in rows if x['lesson']['building_id'] in ids]
    from django.contrib import admin
    return render(request,'admin/conflicts.html',dict(admin.site.each_context(request),title='Контроль конфликтов',rows=rows,date=day.isoformat()))
