"""Daily work screens; low-level model editing stays in the advanced area."""
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.forms import formset_factory,BaseFormSet
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import TimeSlot, WeekdayBell, CuratorBell, ShortenedDay,ShortenedBell, PracticePeriod, WEEKDAYS,Room,RoomCampus,ImageImport
from . import services
from .timing import regular_bell_times, shortened_times


class ManualChangesForm(forms.Form):
    date=forms.DateField(label='Дата замен',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    row_count=forms.IntegerField(label='Строк',min_value=1,max_value=100,initial=20)


def manual_changes(request,site):
    form=ManualChangesForm(request.POST or None,initial={'date':timezone.localdate()})
    if request.method=='POST' and form.is_valid():
        if form.cleaned_data['date'].weekday()==6:
            form.add_error('date','Воскресенье не входит в учебную неделю')
        else:
            batch=ImageImport.objects.create(target_date=form.cleaned_data['date'],layout='manual',
                payload=[{} for _ in range(form.cleaned_data['row_count'])],uploaded_by=request.user)
            services.audit(request.user,'create_manual_changes',batch)
            return redirect('admin:schedule_image_review',pk=batch.pk)
    return render(request,'admin/manual_changes.html',dict(site.each_context(request),title='Новые замены',form=form))


class RoomPlaceForm(forms.Form):
    room=forms.ModelChoiceField(Room.objects.all(),widget=forms.HiddenInput)
    campus=forms.ModelChoiceField(RoomCampus.objects.all(),required=False,label='Корпус')
    floor=forms.IntegerField(required=False,min_value=-10,max_value=100,label='Этаж')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from .room_locations import campus_sort_key
        self.fields['campus'].choices=[('','---------')]+[(c.pk,c.name) for c in sorted(RoomCampus.objects.all(),key=lambda c:campus_sort_key(c.name))]


class RoomCampusForm(forms.ModelForm):
    class Meta:
        model=RoomCampus
        fields=['name']


def room_places(request,site):
    root_only(request)
    rooms=list(Room.objects.select_related('building','campus'))
    Set=formset_factory(RoomPlaceForm,extra=0,max_num=1000,validate_max=True)
    rows=Set(request.POST if request.method=='POST' and request.POST.get('action')=='rooms' else None,
        initial=[{'room':r.pk,'campus':r.campus_id,'floor':r.floor} for r in rooms])
    campus=RoomCampusForm(request.POST if request.method=='POST' and request.POST.get('action')=='campus' else None)
    if request.method=='POST':
        if request.POST.get('action')=='infer':
            from .room_locations import fill_room_location
            count=0
            with transaction.atomic():
                for room in Room.objects.select_for_update():
                    old={'campus_id':room.campus_id,'floor':room.floor}
                    if fill_room_location(room):
                        services.audit(request.user,'edit_room_place',room,old,{'campus_id':room.campus_id,'floor':room.floor})
                        count+=1
            messages.success(request,f'Заполнено кабинетов: {count}')
            return redirect('admin:room_places')
        if request.POST.get('action')=='campus' and campus.is_valid():
            item=campus.save();services.audit(request.user,'create_room_campus',item)
            return redirect('admin:room_places')
        if request.POST.get('action')=='rooms' and rows.is_valid():
            submitted=[f.cleaned_data['room'].pk for f in rows]
            if len(submitted)!=len(set(submitted)) or set(submitted)!={r.pk for r in rooms}:
                rows._non_form_errors=rows.error_class(['Список кабинетов изменился. Обновите страницу.'])
            else:
                with transaction.atomic():
                    for form in rows:
                        data=form.cleaned_data;room=Room.objects.select_for_update().get(pk=data['room'].pk)
                        old={'campus_id':room.campus_id,'floor':room.floor}
                        room.campus=data['campus'];room.floor=data['floor']
                        if old!={'campus_id':room.campus_id,'floor':room.floor}:
                            room.save(update_fields=['campus','floor'])
                            services.audit(request.user,'edit_room_place',room,old,{'campus_id':room.campus_id,'floor':room.floor})
                messages.success(request,'Группировка кабинетов сохранена')
                return redirect('admin:room_places')
    return render(request,'admin/room_places.html',dict(site.each_context(request),title='Корпуса и этажи',
        rows=rows,items=list(zip(rows,rooms)),campus_form=campus))


class BellRowForm(forms.Form):
    slot=forms.ModelChoiceField(TimeSlot.objects.all(),widget=forms.HiddenInput)
    override=forms.BooleanField(required=False,label='Своё время')
    start=forms.TimeField(required=False,widget=forms.TimeInput(format='%H:%M',attrs={'type':'time'}))
    end=forms.TimeField(required=False,widget=forms.TimeInput(format='%H:%M',attrs={'type':'time'}))
    def clean(self):
        data=super().clean()
        if data.get('override') and (not data.get('start') or not data.get('end') or data['start']>=data['end']):
            raise ValidationError('Укажите начало и окончание пары')
        return data
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['start'].widget.attrs['data-bell-start']=''
        self.fields['end'].widget.attrs['data-bell-end']=''


class CuratorForm(forms.Form):
    morning_start=forms.TimeField(label='Первая смена · начало',widget=forms.TimeInput(format='%H:%M',attrs={'type':'time'}))
    morning_end=forms.TimeField(label='Первая смена · окончание',widget=forms.TimeInput(format='%H:%M',attrs={'type':'time'}))
    afternoon_start=forms.TimeField(label='Вторая смена · начало',widget=forms.TimeInput(format='%H:%M',attrs={'type':'time'}))
    afternoon_end=forms.TimeField(label='Вторая смена · окончание',widget=forms.TimeInput(format='%H:%M',attrs={'type':'time'}))
    def clean(self):
        data=super().clean()
        for prefix in ['morning','afternoon']:
            start,end=data.get(prefix+'_start'),data.get(prefix+'_end')
            if start and end and start>=end:
                self.add_error(prefix+'_end','Окончание должно быть позже начала')
        return data


class ShortDayForm(forms.ModelForm):
    class Meta:
        model=ShortenedDay
        fields=['date','lesson_count','lesson_minutes','start_time','keep_breaks','break_minutes','note','is_active']
        widgets={'date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),
                 'start_time':forms.TimeInput(format='%H:%M',attrs={'type':'time'}),'note':forms.TextInput()}
        labels={'lesson_minutes':'Длительность пары по умолчанию, мин',
                'keep_breaks':'Обычные перемены по умолчанию','break_minutes':'Перемена по умолчанию, мин'}


class ShortBellForm(forms.Form):
    slot=forms.ModelChoiceField(TimeSlot.objects.filter(sort_order__range=(1,7)),widget=forms.HiddenInput)
    lesson_minutes=forms.IntegerField(label='Длительность пары',min_value=1,max_value=180,required=False,
                                     widget=forms.NumberInput(attrs={'placeholder':'Общая','inputmode':'numeric'}))
    break_minutes=forms.IntegerField(label='Перемена после',min_value=0,max_value=720,required=False,
                                    widget=forms.NumberInput(attrs={'inputmode':'numeric'}))


class ShortBellRows(BaseFormSet):
    def __init__(self,*args,slots,**kwargs):
        self.slots=slots
        super().__init__(*args,**kwargs)
    def clean(self):
        if any(self.errors):
            return
        ids=[f.cleaned_data['slot'].pk for f in self.forms if f.cleaned_data.get('slot')]
        if len(ids)!=len(self.slots) or set(ids)!={s.pk for s in self.slots}:
            raise ValidationError('Состав пар изменился. Обновите страницу.')
    def pending(self,day):
        return [ShortenedBell(shortened_day=day,time_slot=f.cleaned_data['slot'],
                    lesson_minutes=f.cleaned_data.get('lesson_minutes'),break_minutes=f.cleaned_data.get('break_minutes'))
                for f in self.forms if f.cleaned_data and (f.cleaned_data.get('lesson_minutes') is not None or f.cleaned_data.get('break_minutes') is not None)]


class PracticeForm(forms.ModelForm):
    class Meta:
        model=PracticePeriod
        fields=['group','start_date','end_date','title','organization','is_active']
        widgets={'start_date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),
                 'end_date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})}


class BellRows(BaseFormSet):
    def __init__(self,*args,slots,weekday,**kwargs):
        self.slots=slots;self.weekday=weekday
        super().__init__(*args,**kwargs)
    def clean(self):
        if any(self.errors):
            return
        ids=[f.cleaned_data.get('slot').pk for f in self.forms if f.cleaned_data.get('slot')]
        if len(ids)!=len(self.slots) or set(ids)!={s.pk for s in self.slots}:
            raise ValidationError('Состав пар изменился. Обновите страницу.')
        times={row['slot'].pk:(row['start'],row['end']) if row['override'] else (row['slot'].start_time,row['slot'].end_time)
            for row in [f.cleaned_data for f in self.forms]}
        previous=None
        for slot in self.slots:
            start,end=times[slot.pk]
            if not slot.sort_order or not start:
                continue
            if previous and start<previous:
                raise ValidationError('Пары пересекаются или идут не по порядку. Проверьте время.')
            previous=end
        for rule in ShortenedDay.objects.filter(is_active=True):
            if rule.date.weekday()==self.weekday:
                shortened_times(rule.date,times,rule)


def root_only(request):
    if not request.user.is_superuser:
        raise PermissionDenied('Изменения доступны администратору')


def bells_editor(request,site):
    root_only(request)
    try:
        weekday=int(request.POST.get('weekday',request.GET.get('weekday',min(timezone.localdate().weekday(),5))))
    except ValueError:
        weekday=0
    weekday=weekday if weekday in range(6) else 0
    slots=list(TimeSlot.objects.filter(sort_order__gt=0))
    overrides={row.time_slot_id:row for row in WeekdayBell.objects.filter(day_of_week=weekday)}
    initial=[{'slot':slot.pk,'override':slot.pk in overrides,
              'start':overrides[slot.pk].start_time if slot.pk in overrides else slot.start_time,
              'end':overrides[slot.pk].end_time if slot.pk in overrides else slot.end_time} for slot in slots]
    RowSet=formset_factory(BellRowForm,formset=BellRows,extra=0,max_num=100,validate_max=True)
    action=request.POST.get('action')
    curator_initial={}
    for bell in CuratorBell.objects.all():
        prefix='morning' if bell.shift==1 else 'afternoon'
        curator_initial.update({prefix+'_start':bell.start_time,prefix+'_end':bell.end_time})
    curator_form=CuratorForm(request.POST if action=='curator' else None,initial=curator_initial)
    formset=RowSet(request.POST if action=='weekday' else None,initial=initial,slots=slots,weekday=weekday)
    bell_rows=[]
    for i,(form,slot) in enumerate(zip(formset,slots)):
        # Preserve already configured long breaks; blank means the 10-minute default.
        pause=None
        if i+1<len(formset.forms):
            try:
                start=forms.TimeField(required=False).clean(formset.forms[i+1]['start'].value())
                end=forms.TimeField(required=False).clean(form['end'].value())
            except ValidationError:
                start=end=None
            if start and end:
                minutes=max(0,(start.hour*60+start.minute)-(end.hour*60+end.minute))
                pause=minutes if minutes!=10 else None
        form.bell_pause=pause
        bell_rows.append((form,slot))
    rule_id=request.POST.get('rule_id') or request.GET.get('short')
    rule=get_object_or_404(ShortenedDay,pk=rule_id) if rule_id else None
    copy_from=get_object_or_404(ShortenedDay,pk=request.GET['copy']) if request.GET.get('copy') and not rule else None
    short_initial=None if rule else {'date':timezone.localdate(),'lesson_count':7}
    if copy_from:
        short_initial={**model_to_dict(copy_from,fields=ShortDayForm.Meta.fields),'date':None,'is_active':True}
    short_form=ShortDayForm(request.POST if action=='short_day' else None,instance=rule,
                            initial=short_initial)
    short_slots=[s for s in slots if 1<=s.sort_order<=7]
    source=rule or copy_from
    individual={b.time_slot_id:b for b in source.bells.all()} if source else {}
    short_values=[{'slot':s.pk,'lesson_minutes':individual[s.pk].lesson_minutes if s.pk in individual else None,
                   'break_minutes':individual[s.pk].break_minutes if s.pk in individual else None} for s in short_slots]
    ShortSet=formset_factory(ShortBellForm,formset=ShortBellRows,extra=0,max_num=7,validate_max=True)
    # Accept older open forms without losing already saved per-pair settings.
    short_bound=action=='short_day' and any(key.startswith('short-') for key in request.POST)
    short_set=ShortSet(request.POST if short_bound else None,initial=short_values,prefix='short',slots=short_slots)
    if request.method=='POST':
        if action=='curator' and curator_form.is_valid():
            with transaction.atomic():
                for shift,prefix in [(1,'morning'),(2,'afternoon')]:
                    bell,_=CuratorBell.objects.update_or_create(shift=shift,defaults={
                        'start_time':curator_form.cleaned_data[prefix+'_start'],
                        'end_time':curator_form.cleaned_data[prefix+'_end']})
                    services.audit(request.user,'edit_bells',bell,new={'shift':shift,'start':str(bell.start_time),'end':str(bell.end_time)})
            messages.success(request,'Кураторские часы сохранены')
            return redirect(f'{request.path}?weekday=0')
        elif action=='weekday' and formset.is_valid():
            ids=[f.cleaned_data['slot'].pk for f in formset]
            if len(set(ids))!=len(slots) or set(ids)!={s.pk for s in slots}:
                messages.error(request,'Состав пар изменился. Обновите страницу.')
            else:
                with transaction.atomic():
                    for form in formset:
                        row=form.cleaned_data
                        if row['override']:
                            bell,_=WeekdayBell.objects.update_or_create(day_of_week=weekday,time_slot=row['slot'],
                                defaults={'start_time':row['start'],'end_time':row['end']})
                            services.audit(request.user,'edit_bells',bell,new={'weekday':weekday,'start':str(row['start']),'end':str(row['end'])})
                        else:
                            for bell in WeekdayBell.objects.filter(day_of_week=weekday,time_slot=row['slot']):
                                services.audit(request.user,'reset_bells',bell)
                                bell.delete()
                messages.success(request,'Звонки сохранены')
                return redirect(f'{request.path}?weekday={weekday}')
        elif action=='short_day':
            rows_valid=not short_bound or short_set.is_valid()
            if rows_valid and short_bound:
                short_form.instance._pending_bells=short_set.pending(short_form.instance)
            if short_form.is_valid() and rows_valid:
                with transaction.atomic():
                    services.lock_schedule()
                    saved=short_form.save()
                    if short_bound:
                        saved.bells.all().delete()
                        entries=short_set.pending(saved)
                        ShortenedBell.objects.bulk_create([entry for entry in entries if saved.lesson_count is None or entry.time_slot.sort_order<=saved.lesson_count])
                    services.audit(request.user,'edit_short_day',saved,new={'date':str(saved.date),'minutes':saved.lesson_minutes,
                        'lesson_count':saved.lesson_count,'active':saved.is_active})
                messages.success(request,'Сокращённый день сохранён')
                return redirect(f'{request.path}?weekday={weekday}&short={saved.pk}')
        elif action=='disable_short' and rule:
            rule.is_active=False;rule.save(update_fields=['is_active'])
            services.audit(request.user,'disable_short_day',rule)
            messages.success(request,'Сокращённый день отключён')
            return redirect(request.path)
    preview=[]
    if rule and rule.is_active and not (short_form.is_bound and short_form.errors) and not (short_bound and short_set.errors):
        times=shortened_times(rule.date,regular_bell_times(rule.date),rule)
        preview=[{'name':s.name,'start':times[s.pk][0],'end':times[s.pk][1]} for s in slots if s.sort_order and times[s.pk][0]]
    monday=timezone.localdate()-timedelta(days=timezone.localdate().weekday())
    base_times=[]
    for number in range(6):
        regular=regular_bell_times(monday+timedelta(days=number))
        base_times.append({'weekday':number,'slots':[{'number':s.sort_order,
            'start':regular[s.pk][0].isoformat(timespec='minutes') if regular[s.pk][0] else None,
            'end':regular[s.pk][1].isoformat(timespec='minutes') if regular[s.pk][1] else None} for s in short_slots]})
    return render(request,'admin/bells_editor.html',dict(site.each_context(request),title='Звонки',
        weekday=weekday,weekdays=WEEKDAYS[:6],bell_rows=bell_rows,formset=formset,curator_form=curator_form,
        short_form=short_form,short_set=short_set,short_rows=list(zip(short_set,short_slots)),short_base_times=base_times,
        rule=rule,copy_from=copy_from,preview=preview,short_days=ShortenedDay.objects.order_by('-date')))


def practices_editor(request,site):
    root_only(request)
    record_id=request.POST.get('record_id') or request.GET.get('edit')
    record=get_object_or_404(PracticePeriod,pk=record_id) if record_id else None
    if request.method=='POST' and request.POST.get('action')=='disable' and record:
        record.is_active=False;record.save(update_fields=['is_active'])
        services.audit(request.user,'disable_practice',record)
        messages.success(request,'Практика отменена. Занятия снова показываются по расписанию.')
        return redirect(request.path)
    form=PracticeForm(request.POST or None,instance=record,initial=None if record else {'start_date':timezone.localdate()})
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            saved=form.save()
            services.audit(request.user,'edit_practice',saved,new={'group':saved.group_id,'start':str(saved.start_date),'end':str(saved.end_date),'active':saved.is_active})
        messages.success(request,'Практика сохранена')
        return redirect(request.path)
    return render(request,'admin/practices_editor.html',dict(site.each_context(request),title='Практики',form=form,
        record=record,practices=PracticePeriod.objects.select_related('group').all(),today=timezone.localdate()))
