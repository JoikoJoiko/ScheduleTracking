from django.contrib import admin, messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.forms.models import model_to_dict
from django.core.serializers.json import DjangoJSONEncoder
import json
import subprocess
from django.shortcuts import render, redirect
from django.urls import path
from django import forms
from .models import *
from . import services
from .importing import preview_import, confirm_import, import_report

admin.site.site_header = 'Расписание · управление'
admin.site.site_title = 'Расписание колледжа'
admin.site.index_title = 'Управление учебным процессом'


class SafeAdmin(admin.ModelAdmin):
    save_on_top = True
    def has_module_permission(self, request):
        return request.user.is_superuser
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
    def has_add_permission(self, request):
        return request.user.is_superuser
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser
    def has_delete_permission(self, request, obj=None):
        # Archive records instead; references and history must survive.
        return False
    def save_model(self, request, obj, form, change):
        def snapshot(record):
            return json.loads(json.dumps({f.attname:getattr(record,f.attname) for f in record._meta.fields},cls=DjangoJSONEncoder))
        old = snapshot(type(obj).objects.get(pk=obj.pk)) if change else {}
        super().save_model(request,obj,form,change)
        services.audit(request.user,'edit' if change else 'create',obj,old,snapshot(obj))


class ScopedAdmin(SafeAdmin):
    def has_module_permission(self, request):
        return request.user.is_staff
    def has_view_permission(self, request, obj=None):
        return request.user.is_staff
    def has_add_permission(self, request):
        return request.user.is_staff
    def has_change_permission(self, request, obj=None):
        return request.user.is_staff
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        ids = services.scope_ids(request.user)
        if ids is None:
            return qs
        if self.model == Change:
            return qs.filter(Q(original__room__building_id__in=ids)|Q(original__isnull=True,room__building_id__in=ids)).filter(Q(room__isnull=True)|Q(room__building_id__in=ids))
        if self.model == Lesson:
            return qs.filter(room__building_id__in=ids)
        if self.model == RoomBlock:
            return qs.filter(room__building_id__in=ids)
        if self.model == ScheduleVersion:
            return qs.filter(lessons__room__building_id__in=ids).exclude(lessons__room__building_id__in=Building.objects.exclude(pk__in=ids)).distinct()
        return qs.none()
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        ids = services.scope_ids(request.user)
        if ids is not None:
            model = db_field.remote_field.model
            if model == Room:
                kwargs['queryset'] = Room.objects.filter(building_id__in=ids)
            if model == Lesson:
                kwargs['queryset'] = Lesson.objects.filter(room__building_id__in=ids,version__status='published')
            if model == StudentGroup:
                kwargs['queryset'] = StudentGroup.objects.filter(building_id__in=ids)
        return super().formfield_for_foreignkey(db_field,request,**kwargs)


from django.db.models import Q

def operation(admin_obj, request, queryset, title, callback, preview=None):
    if 'confirm' not in request.POST:
        return render(request,'admin/operation.html',dict(admin_obj.admin_site.each_context(request),title=title,
            objects=queryset, action=request.POST.get('action'), preview=preview))
    for obj in queryset:
        try:
            callback(obj, request.user)
            admin_obj.message_user(request,f'Готово: {obj}',messages.SUCCESS)
        except (ValidationError,PermissionDenied) as exc:
            admin_obj.message_user(request,str(exc),messages.ERROR)


@admin.register(ScheduleVersion)
class VersionAdmin(ScopedAdmin):
    list_display = ['name','period','effective_from','effective_to','status','published_at']
    list_filter = ['status','period']
    readonly_fields = ['status','created_at','published_at']
    actions = ['publish','rollback']
    def has_add_permission(self, request):
        return request.user.is_superuser
    def has_change_permission(self,request,obj=None):
        return request.user.is_superuser and (not obj or obj.status=='draft')
    @admin.action(description='Проверить и опубликовать выбранные версии')
    def publish(self,request,queryset):
        return operation(self,request,queryset,'Публикация версий',services.publish_version)
    @admin.action(description='Откатить выбранные версии')
    def rollback(self,request,queryset):
        return operation(self,request,queryset,'Откат публикации',services.rollback_version)


class ParticipantInline(admin.TabularInline):
    model = Participant
    extra = 1
    min_num = 1
    validate_min = True


@admin.register(Lesson)
class LessonAdmin(ScopedAdmin):
    list_display = ['id','version','day_of_week','time_slot','subject','teacher','room']
    list_filter = ['version','day_of_week','room__building']
    inlines = [ParticipantInline]
    search_fields = ['subject__name','teacher__name','participants__group__name']
    def has_add_permission(self,request):
        return request.user.is_superuser
    def has_change_permission(self,request,obj=None):
        return request.user.is_superuser and (not obj or obj.version.status=='draft')


@admin.register(Change)
class ChangeAdmin(ScopedAdmin):
    list_display = ['id','target_date','kind','original','room','status','created_by']
    list_filter = ['status','kind','target_date']
    readonly_fields = ['status','created_by','created_at','published_at','room_overlap_approvals','teacher_overlap_approvals']
    actions = ['publish','withdraw']
    def has_change_permission(self,request,obj=None):
        return request.user.is_staff and (not obj or obj.status=='draft')
    def save_model(self,request,obj,form,change):
        services.require_buildings(request.user,services.change_buildings(obj))
        if obj.group_id:
            services.require_buildings(request.user,[obj.group.building_id])
        if obj.target_group_id:
            services.require_buildings(request.user,[obj.target_group.building_id])
        if not change:
            obj.created_by=request.user
        super().save_model(request,obj,form,change)
    @admin.action(description='Предпросмотр и публикация замен')
    def publish(self,request,queryset):
        preview=[]
        for obj in queryset:
            rows=services.schedule_on(obj.new_date or obj.target_date,preview=obj)
            preview.append({'change':str(obj),'lessons':rows,'conflicts':services.conflicts_for(obj.new_date or obj.target_date,rows)})
        return operation(self,request,queryset,'Публикация замен',services.publish_change,preview)
    @admin.action(description='Отозвать публикацию (проверить откат)')
    def withdraw(self,request,queryset):
        return operation(self,request,queryset,'Отзыв замен',services.withdraw_change)


@admin.register(RoomBlock)
class BlockAdmin(ScopedAdmin):
    list_display = ['room','start_at','end_at','reason']
    readonly_fields = ['created_by']
    def save_model(self,request,obj,form,change):
        services.require_buildings(request.user,[obj.room.building_id])
        obj.created_by=request.user
        super().save_model(request,obj,form,change)


class UploadForm(forms.Form):
    file = forms.FileField(label='Файл XLSX')
    profile = forms.ModelChoiceField(ImportProfile.objects.all(),label='Формат')
    period = forms.ModelChoiceField(Period.objects.all(),label='Период',required=False)
    start = forms.DateField(label='С',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='По',required=False,widget=forms.DateInput(attrs={'type':'date'}))


@admin.register(ImportBatch)
class ImportAdmin(SafeAdmin):
    list_display = ['filename','created_at','uploaded_by','status','version']
    readonly_fields = [f.name for f in ImportBatch._meta.fields]
    change_list_template = 'admin/import_list.html'
    def has_module_permission(self,request):
        return request.user.is_staff
    def has_view_permission(self,request,obj=None):
        return request.user.is_staff
    def has_add_permission(self,request):
        return False
    def get_queryset(self,request):
        qs=super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(uploaded_by=request.user)
    def get_urls(self):
        return [path('upload/',self.admin_site.admin_view(self.upload),name='schedule_import_upload'),
                path('<int:pk>/preview/',self.admin_site.admin_view(self.preview),name='schedule_import_preview')] + super().get_urls()
    def upload(self,request):
        form=UploadForm(request.POST or None,request.FILES or None)
        if request.method=='POST' and form.is_valid():
            try:
                batch=preview_import(form.cleaned_data['file'],form.cleaned_data['profile'],form.cleaned_data['period'],form.cleaned_data['start'],form.cleaned_data['end'],request.user)
                return redirect('admin:schedule_import_preview',pk=batch.pk)
            except (ValidationError,PermissionDenied) as exc:
                form.add_error(None,str(exc))
        return render(request,'admin/upload.html',dict(self.admin_site.each_context(request),form=form,title='Импорт расписания'))
    def preview(self,request,pk):
        from django.shortcuts import get_object_or_404
        batch=get_object_or_404(self.get_queryset(request),pk=pk)
        if request.method=='POST':
            try:
                if request.POST.get('cancel'):
                    if batch.status=='preview':
                        batch.status='cancelled'
                        batch.save(update_fields=['status'])
                        services.audit(request.user,'cancel_import',batch)
                else:
                    confirm_import(batch,request.user)
                return redirect('admin:schedule_importbatch_changelist')
            except (ValidationError,PermissionDenied) as exc:
                self.message_user(request,str(exc),messages.ERROR)
        current=services.version_on(batch.effective_from)
        report=import_report(batch)
        return render(request,'admin/import_preview.html',dict(self.admin_site.each_context(request),batch=batch,
            report=report,group_count=len({r.get('group_id',r.get('group')) for r in batch.payload}),current=current,title='Предварительный просмотр импорта'))


@admin.register(AcademicYear)
class YearAdmin(SafeAdmin):
    list_display=['name','start_date','end_date','week_anchor_date','week_anchor_number','status']
    readonly_fields=['status']
    actions=['activate']
    @admin.action(description='Активировать учебный год')
    def activate(self,request,queryset):
        return operation(self,request,queryset,'Активация учебного года',services.activate_year)
    def get_urls(self):
        return [path('rollover/',self.admin_site.admin_view(self.rollover_view),name='schedule_year_rollover')]+super().get_urls()
    def rollover_view(self,request):
        if not request.user.is_superuser:
            raise PermissionDenied
        years=AcademicYear.objects.all()
        source=target=None
        rows=[]
        try:
            if request.GET.get('source') and request.GET.get('target'):
                source=years.get(pk=request.GET['source']); target=years.get(pk=request.GET['target'])
                rows=services.rollover_preview(source,target)
            if request.method=='POST':
                source=years.get(pk=request.POST['source']); target=years.get(pk=request.POST['target'])
                services.rollover(source,target,request.user)
                self.message_user(request,'Состав нового года сформирован. Проверьте его и активируйте год.',messages.SUCCESS)
                return redirect('admin:schedule_groupyear_changelist')
        except (ValidationError,ValueError,AcademicYear.DoesNotExist) as exc:
            self.message_user(request,str(exc),messages.ERROR)
        return render(request,'admin/rollover.html',dict(self.admin_site.each_context(request),years=years,rows=rows,source=source,target=target,title='Переход на новый учебный год'))


@admin.register(AuditLog)
class AuditAdmin(SafeAdmin):
    list_display=['created_at','user','action','entity_type','entity_id']
    list_filter=['action','entity_type']
    readonly_fields=[f.name for f in AuditLog._meta.fields]
    def has_add_permission(self,request):
        return False
    def has_change_permission(self,request,obj=None):
        return False


for model in [Period, Building, RoomType, Teacher, Specialty, StudentGroup, GroupYear, Subgroup, Subject, CalendarDay, StaffScope, ImportProfile, SystemSetting]:
    admin.site.register(model,SafeAdmin)


@admin.register(TimeSlot)
class BellsAdmin(SafeAdmin):
    list_display=['name','start_time','end_time','sort_order']
    list_editable=['start_time','end_time']


@admin.register(WeekdayBell)
class WeekdayBellsAdmin(SafeAdmin):
    change_list_template='admin/weekday_bells.html'
    list_display=['day_of_week','time_slot','start_time','end_time']
    list_filter=['day_of_week']
    list_editable=['start_time','end_time']
    actions=['delete_selected']
    def has_delete_permission(self,request,obj=None):
        # Deleting an override restores the common bell interval.
        return request.user.is_superuser


@admin.register(ShortenedDay)
class ShortenedDayAdmin(SafeAdmin):
    list_display=['date','lesson_count','lesson_minutes','start_time','is_active']
    list_filter=['is_active']


@admin.register(PracticePeriod)
class PracticeAdmin(SafeAdmin):
    list_display=['group','start_date','end_date','title','organization','is_active']
    list_filter=['is_active','group']
    search_fields=['group__name','title','organization']


@admin.register(Room)
class RoomsAdmin(SafeAdmin):
    list_display=['number','is_active']
    search_fields=['number']
    fields=['number','description','is_active']
    def save_model(self,request,obj,form,change):
        if not obj.building_id:
            obj.building=Building.objects.get_or_create(name='Колледж')[0]
        if not obj.room_type_id:
            obj.room_type=RoomType.objects.get_or_create(name='Кабинет')[0]
        super().save_model(request,obj,form,change)


class ImageUploadForm(forms.Form):
    image=forms.ImageField(label='Изображение')
    target_date=forms.DateField(label='Дата замен',widget=forms.DateInput(attrs={'type':'date'}))
    layout=forms.ChoiceField(label='Формат',choices=[('table','Таблица замен'),('yellow','Жёлтые ячейки')])


class ImageRowForm(forms.Form):
    use=forms.BooleanField(label='Проверено',required=False)
    allow_room_overlap=forms.BooleanField(label='Оставить пересечение кабинета',required=False)
    allow_teacher_overlap=forms.BooleanField(label='Оставить пересечение преподавателя',required=False)
    group=forms.ModelChoiceField(StudentGroup.objects.all(),required=False,label='Группа')
    subgroup=forms.ModelChoiceField(Subgroup.objects.select_related('group'),required=False,label='Подгруппа')
    slot=forms.IntegerField(min_value=1,max_value=7,required=False,label='Пара')
    kind=forms.ChoiceField(choices=[('replace','Замена'),('cancel','Отмена')],label='Действие')
    subject=forms.ModelChoiceField(Subject.objects.all(),required=False,label='Дисциплина')
    teacher=forms.ModelChoiceField(Teacher.objects.all(),required=False,label='Преподаватель')
    room=forms.ModelChoiceField(Room.objects.select_related('building'),required=False,label='Кабинет')
    def __init__(self,*args,target_date=None,**kwargs):
        super().__init__(*args,**kwargs)
        from django.utils import timezone
        from .curriculum import curriculum_for
        self.target_date=target_date or timezone.localdate()
        group_id=self.data.get(self.add_prefix('group')) if self.is_bound else self.initial.get('group')
        group=StudentGroup.objects.filter(pk=group_id).first() if str(group_id or '').isdigit() else None
        data=curriculum_for(group,self.target_date) if group else {'subjects':[],'teachers':[],'subgroups':[]}
        for field,key,model in [('subject','subjects',Subject),('teacher','teachers',Teacher),('subgroup','subgroups',Subgroup)]:
            self.fields[field].queryset=model.objects.filter(pk__in=[x['id'] for x in data[key]])
        self.fields['subgroup'].queryset=self.fields['subgroup'].queryset.select_related('group')
    def clean(self):
        data=super().clean()
        if data.get('use'):
            for name in ['group','slot']+([] if data.get('kind')=='cancel' else ['subject','teacher','room']):
                if not data.get(name):
                    self.add_error(name,'Выберите значение')
            if data.get('subgroup') and data.get('group') and data['subgroup'].group_id!=data['group'].pk:
                self.add_error('subgroup','Не относится к группе')
        return data


@admin.register(ImageImport)
class ImageAdmin(SafeAdmin):
    list_display=['target_date','layout','status','uploaded_by','created_at']
    change_list_template='admin/image_list.html'
    readonly_fields=[f.name for f in ImageImport._meta.fields]
    def has_module_permission(self,request):
        return request.user.is_staff
    def has_view_permission(self,request,obj=None):
        return request.user.is_staff
    def has_add_permission(self,request):
        return False
    def get_queryset(self,request):
        qs=super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(uploaded_by=request.user)
    def get_urls(self):
        return [path('upload/',self.admin_site.admin_view(self.upload),name='schedule_image_upload'),
            path('<int:pk>/review/',self.admin_site.admin_view(self.review),name='schedule_image_review'),
            path('<int:pk>/fragment/<int:index>/',self.admin_site.admin_view(self.fragment),name='schedule_image_fragment'),
            path('<int:pk>/source/',self.admin_site.admin_view(self.source),name='schedule_image_source')]+super().get_urls()
    def upload(self,request):
        from .image_import import upload_image
        form=ImageUploadForm(request.POST or None,request.FILES or None)
        if request.method=='POST' and form.is_valid():
            try:
                batch=upload_image(form.cleaned_data['image'],form.cleaned_data['target_date'],form.cleaned_data['layout'],request.user)
                return redirect('admin:schedule_image_review',pk=batch.pk)
            except ValidationError as exc:
                form.add_error(None,exc)
        return render(request,'admin/upload.html',dict(self.admin_site.each_context(request),form=form,title='Загрузить замены'))
    def source(self,request,pk):
        from django.shortcuts import get_object_or_404
        from django.http import FileResponse
        batch=get_object_or_404(self.get_queryset(request),pk=pk)
        return FileResponse(batch.image.open('rb'),content_type='image/png')
    def fragment(self,request,pk,index):
        from django.shortcuts import get_object_or_404
        from django.http import HttpResponse, Http404
        from .image_import import fragment_image
        batch=get_object_or_404(self.get_queryset(request),pk=pk)
        try:
            return HttpResponse(fragment_image(batch,index,request.GET.get('context')=='1'),content_type='image/png')
        except ValidationError as exc:
            raise Http404(str(exc)) from exc
    def review(self,request,pk):
        from django.shortcuts import get_object_or_404
        from django.forms import formset_factory
        from .image_import import publish_review,infer_row,ImageReviewConflicts
        batch=get_object_or_404(self.get_queryset(request),pk=pk)
        if request.method=='POST' and request.POST.get('reanalyze') and batch.status=='review':
            from .image_import import analyze
            from django.db import transaction
            from PIL import Image
            try:
                with batch.image.open('rb') as source,Image.open(source) as original:
                    if original.width*original.height>16000000:
                        raise ValidationError('Изображение больше 16 мегапикселей')
                    rows,warnings=analyze(original.convert('RGB'),batch.layout,batch.target_date)
                with transaction.atomic():
                    services.lock_schedule()
                    batch=ImageImport.objects.select_for_update().get(pk=batch.pk)
                    if batch.status!='review':
                        raise ValidationError('Импорт уже завершён. Повторный анализ отменён.')
                    batch.payload=rows;batch.warnings=warnings;batch.save(update_fields=['payload','warnings'])
                    services.audit(request.user,'reanalyze_image',batch,new={'rows':len(rows)})
                self.message_user(request,'Изображение распознано повторно',messages.SUCCESS)
            except (ValidationError,OSError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
                self.message_user(request,str(exc),messages.ERROR)
            return redirect('admin:schedule_image_review',pk=batch.pk)
        initial=[];dictionaries={};previews_rows=[];base=services.base_schedule(batch.target_date);review_conflicts=[]
        for row in batch.payload:
            group_id=row.get('group_id')
            if group_id not in dictionaries:
                from .curriculum import curriculum_for
                group=StudentGroup.objects.filter(pk=group_id).first()
                dictionaries[group_id]=curriculum_for(group,batch.target_date) if group else None
            suggestions=infer_row(group_id,batch.target_date,row,dictionaries[group_id],base)
            fresh=batch.status=='review' and bool(row.get('raw') or row.get('raw_variants') or row.get('source_box'))
            # Older pending uploads also show when newly completed fields are inferred.
            preview={**row,**{key:suggestions[key] for key in ['inferred','confidence','inferences']}} if fresh or 'inferred' not in row else row
            if fresh:
                group_notes=[note for note in row.get('inferences',[]) if note.startswith('Группа:')]
                if group_notes:
                    preview['inferences']=group_notes+list(preview['inferences'])
                    preview['inferred']=True;preview['confidence']=min(preview['confidence'],80)
            previews_rows.append(preview)
            picked=lambda field:suggestions[field+'_id'] if fresh else row.get(field+'_id') or suggestions[field+'_id']
            initial.append({'group':row.get('group_id'),'subgroup':row.get('subgroup_id'),'slot':row.get('slot'),
                'allow_room_overlap':row.get('allow_room_overlap',False),
                'allow_teacher_overlap':row.get('allow_teacher_overlap',False),
                'kind':row.get('kind','replace'),'subject':picked('subject'),
                'teacher':picked('teacher'),'room':picked('room')})
        FormSet=formset_factory(ImageRowForm,extra=3,max_num=250,validate_max=True,can_delete=True)
        formset=FormSet(request.POST or None,initial=initial,form_kwargs={'target_date':batch.target_date})
        if request.method=='POST' and batch.status=='review':
            if request.POST.get('cancel'):
                batch.status='cancelled';batch.save();services.audit(request.user,'cancel_image',batch)
                return redirect('admin:schedule_imageimport_changelist')
            if formset.is_valid():
                rows=[]
                for index,form in enumerate(formset):
                    d=form.cleaned_data
                    if not d or d.get('DELETE') or not d.get('use'):
                        continue
                    rows.append({'group_id':d['group'].pk,'slot':d['slot'],'kind':d['kind'],'review_row':index+1,
                        'allow_room_overlap':d.get('allow_room_overlap',False),
                        'allow_teacher_overlap':d.get('allow_teacher_overlap',False),
                        'subgroup_id':d['subgroup'].pk if d.get('subgroup') else None,
                        **{key+'_id':d[key].pk if d.get(key) else None for key in ['subject','teacher','room']}})
                try:
                    publish_review(batch,rows,request.user)
                    self.message_user(request,'Замены опубликованы',messages.SUCCESS)
                    return redirect('admin:schedule_imageimport_changelist')
                except ImageReviewConflicts as exc:
                    review_conflicts=exc.conflicts
                except (ValidationError,PermissionDenied) as exc:
                    self.message_user(request,str(exc),messages.ERROR)
        previews=[r.get('group_text','')+' · '+r.get('raw','') for r in batch.payload]
        items=[]
        for i,form in enumerate(formset):
            row=dict(previews_rows[i]) if i<len(previews_rows) else {}
            row['conflicts']=[]
            for conflict in review_conflicts:
                if i+1 not in conflict['rows']:
                    continue
                own_first=conflict['first_row']==i+1
                row['conflicts'].append({**conflict,
                    'counterpart':conflict['other_text'] if own_first else conflict['lesson_text'],
                    'counterpart_row':conflict['second_row'] if own_first else conflict['first_row']})
            row['room_conflicts']=any(c['room_conflict'] for c in row['conflicts'])
            row['teacher_conflicts']=any(c['teacher_conflict'] for c in row['conflicts'])
            items.append((form,previews[i] if i<len(previews) else '',row))
        return render(request,'admin/image_review.html',dict(self.admin_site.each_context(request),title='Проверка замен',
            batch=batch,formset=formset,items=items,review_conflicts=review_conflicts,review_week=services.week_info(batch.target_date)))
