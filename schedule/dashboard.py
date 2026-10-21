"""The admin home owns day-to-day uploads; existing review pages stay reusable."""
from datetime import date
import logging
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .models import AcademicYear, GroupYear, ImportBatch, ImportProfile, ImageImport
from .importing import preview_import, confirm_import
from .image_import import upload_image
from .timing import week_info


class CourseUploadForm(forms.Form):
    file = forms.FileField()
    course = forms.TypedChoiceField(choices=[(n,n) for n in range(1,5)], coerce=int)
    publish = forms.BooleanField(required=False)


class ChangesUploadForm(forms.Form):
    image = forms.ImageField()
    target_date = forms.DateField()
    layout = forms.ChoiceField(choices=[('table','Таблица замен'),('yellow','Жёлтые ячейки')])


def records_for(user):
    imports = ImportBatch.objects.select_related('version').order_by('-created_at')
    images = ImageImport.objects.order_by('-created_at')
    if not user.is_superuser:
        imports = imports.filter(uploaded_by=user)
        images = images.filter(uploaded_by=user)
    return imports, images


def dashboard_context(user):
    year = AcademicYear.objects.filter(status='active').first()
    counts = dict(GroupYear.objects.filter(year=year,status='active').values_list('course').annotate(total=Count('pk')))
    imports, images = records_for(user)
    recent_imports = list(imports[:40])
    courses = []
    for number in range(1,5):
        published = imports.filter(metadata__course=number,status='confirmed',version__status='published',period__year=year).first()
        courses.append({'number':number,'groups':counts.get(number,0),'published_at':published.completed_at if published else None,
            'filename':published.filename if published else '',
            'review_url':reverse('admin:schedule_import_preview',args=[published.pk]) if published else ''})
    history = []
    for batch in recent_imports[:8]:
        history.append({'kind':'schedule','name':batch.filename,'date':batch.created_at,
            'state':batch.status,'label':{'preview':'Проверка','confirmed':'Опубликовано','cancelled':'Отменено'}.get(batch.status,batch.status),
            'url':reverse('admin:schedule_import_preview',args=[batch.pk])})
    for batch in images[:8]:
        history.append({'kind':'changes','name':f'Замены · {batch.target_date:%d.%m.%Y}', 'date':batch.created_at,
            'state':batch.status,'label':batch.get_status_display(),
            'url':reverse('admin:schedule_image_review',args=[batch.pk])})
    history.sort(key=lambda row:row['date'], reverse=True)
    return {'courses':courses,'upload_history':history[:8], 'upload_today':timezone.localdate().isoformat(),
            'active_year':year,'current_week':week_info(timezone.localdate()),'pending_images':images.filter(status='review').count()}


def form_error(form):
    labels={'file':'Файл','course':'Курс','publish':'Публикация','image':'Изображение','target_date':'Дата','layout':'Формат'}
    errors=[f'{labels.get(field,field)}: {message}' for field,messages in form.errors.items() for message in messages]
    return JsonResponse({'error':' '.join(errors)},status=400)


def operation_error(exc, status=400):
    message='; '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
    return JsonResponse({'error':message},status=status)


@require_POST
def upload_schedule(request):
    if not request.user.is_superuser:
        return JsonResponse({'error':'Расписание загружает администратор.'},status=403)
    form=CourseUploadForm(request.POST,request.FILES)
    if not form.is_valid():
        return form_error(form)
    data=form.cleaned_data
    try:
        profile=ImportProfile.objects.filter(kind='college').first()
        if profile is None:
            profile=ImportProfile.objects.create(name='Расписание колледжа',kind='college')
        batch=preview_import(data['file'],profile,None,None,None,request.user,expected_course=data['course'])
        url=reverse('admin:schedule_import_preview',args=[batch.pk])
        result={'id':batch.pk,'state':'review','url':url,'course':data['course'],
            'groups':len(batch.metadata['groups']),'rows':len(batch.payload),
            'message':'Файл разобран. Проверьте результат.'}
        if batch.errors:
            result.update(message='Есть строки, требующие проверки.',errors=batch.errors)
        elif data['publish']:
            try:
                confirm_import(batch,request.user)
                result.update(state='published',message='Расписание обновлено.')
            except ValidationError as exc:
                # Keep a reviewable draft when a conflict blocks automatic publication.
                result.update(message='Публикация требует проверки.',errors=exc.messages)
        return JsonResponse(result,status=201)
    except (ValidationError,PermissionDenied) as exc:
        return operation_error(exc,403 if isinstance(exc,PermissionDenied) else 400)
    except Exception:
        logging.getLogger(__name__).exception('Dashboard schedule upload failed')
        return JsonResponse({'error':'Не удалось обработать файл. Опубликованное расписание сохранено.'},status=500)


@require_POST
def upload_changes(request):
    form=ChangesUploadForm(request.POST,request.FILES)
    if not form.is_valid():
        return form_error(form)
    data=form.cleaned_data
    try:
        batch=upload_image(data['image'],data['target_date'],data['layout'],request.user)
        return JsonResponse({'id':batch.pk,'state':'review','url':reverse('admin:schedule_image_review',args=[batch.pk]),
            'rows':len(batch.payload),'message':f'Распознано строк: {len(batch.payload)}. Проверьте перед публикацией.',
            'warnings':batch.warnings},status=201)
    except ValidationError as exc:
        return operation_error(exc)
    except Exception:
        logging.getLogger(__name__).exception('Dashboard image upload failed')
        return JsonResponse({'error':'Не удалось обработать изображение. Попробуйте ещё раз.'},status=500)


@require_GET
def upload_status(request):
    context=dashboard_context(request.user)
    return JsonResponse({'courses':context['courses'],'history':context['upload_history'],
                         'pending_images':context['pending_images']})
