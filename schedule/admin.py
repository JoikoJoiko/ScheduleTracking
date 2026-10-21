from django.contrib import admin, messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.forms.models import model_to_dict
from django.shortcuts import render, redirect
from django.urls import path
from django import forms
from .models import *
from . import services
from .importing import preview_import, confirm_import

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
        old = {'summary': str(type(obj).objects.get(pk=obj.pk))} if change else {}
        super().save_model(request,obj,form,change)
        services.audit(request.user,'edit' if change else 'create',obj,old,{'summary':str(obj)})


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
    readonly_fields = ['status','created_by','created_at','published_at']
    actions = ['publish','withdraw']
    def has_change_permission(self,request,obj=None):
        return request.user.is_staff and (not obj or obj.status=='draft')
    def save_model(self,request,obj,form,change):
        services.require_buildings(request.user,services.change_buildings(obj))
        if obj.group_id:
            services.require_buildings(request.user,[obj.group.building_id])
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
    profile = forms.ModelChoiceField(ImportProfile.objects.all(),label='Профиль')
    period = forms.ModelChoiceField(Period.objects.all(),label='Учебный период')
    start = forms.DateField(label='Действует с',widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='Действует по',widget=forms.DateInput(attrs={'type':'date'}))


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
        return render(request,'admin/import_preview.html',dict(self.admin_site.each_context(request),batch=batch,
            group_count=len({r['group_id'] for r in batch.payload}),current=current,title='Предварительный просмотр импорта'))


@admin.register(AcademicYear)
class YearAdmin(SafeAdmin):
    list_display=['name','start_date','end_date','status']
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


for model in [Period, Building, RoomType, Room, Teacher, Specialty, StudentGroup, GroupYear, Subgroup, Subject, TimeSlot, CalendarDay, StaffScope, ImportProfile, SystemSetting]:
    admin.site.register(model,SafeAdmin)
