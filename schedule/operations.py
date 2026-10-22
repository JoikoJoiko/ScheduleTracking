"""Daily work screens; low-level model editing stays in the advanced area."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.forms import formset_factory,BaseFormSet
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import TimeSlot, WeekdayBell, ShortenedDay, PracticePeriod, StudentGroup, WEEKDAYS
from . import services
from .timing import regular_bell_times, shortened_times


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


class ShortDayForm(forms.ModelForm):
    class Meta:
        model=ShortenedDay
        fields=['date','lesson_minutes','start_time','keep_breaks','break_minutes','note','is_active']
        widgets={'date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),
                 'start_time':forms.TimeInput(format='%H:%M',attrs={'type':'time'}),'note':forms.TextInput()}


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
    slots=list(TimeSlot.objects.all())
    overrides={row.time_slot_id:row for row in WeekdayBell.objects.filter(day_of_week=weekday)}
    initial=[{'slot':slot.pk,'override':slot.pk in overrides,
              'start':overrides[slot.pk].start_time if slot.pk in overrides else slot.start_time,
              'end':overrides[slot.pk].end_time if slot.pk in overrides else slot.end_time} for slot in slots]
    RowSet=formset_factory(BellRowForm,formset=BellRows,extra=0,max_num=100,validate_max=True)
    action=request.POST.get('action')
    formset=RowSet(request.POST if action=='weekday' else None,initial=initial,slots=slots,weekday=weekday)
    rule_id=request.POST.get('rule_id') or request.GET.get('short')
    rule=get_object_or_404(ShortenedDay,pk=rule_id) if rule_id else None
    short_form=ShortDayForm(request.POST if action=='short_day' else None,instance=rule,
                            initial=None if rule else {'date':timezone.localdate()})
    if request.method=='POST':
        if action=='weekday' and formset.is_valid():
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
        elif action=='short_day' and short_form.is_valid():
            with transaction.atomic():
                saved=short_form.save()
                services.audit(request.user,'edit_short_day',saved,new={'date':str(saved.date),'minutes':saved.lesson_minutes,'active':saved.is_active})
            messages.success(request,'Сокращённый день сохранён')
            return redirect(f'{request.path}?weekday={weekday}&short={saved.pk}')
        elif action=='disable_short' and rule:
            rule.is_active=False;rule.save(update_fields=['is_active'])
            services.audit(request.user,'disable_short_day',rule)
            messages.success(request,'Сокращённый день отключён')
            return redirect(request.path)
    preview=[]
    if rule and rule.is_active and not (short_form.is_bound and short_form.errors):
        times=shortened_times(rule.date,regular_bell_times(rule.date),rule)
        preview=[{'name':s.name,'start':times[s.pk][0],'end':times[s.pk][1]} for s in slots if s.sort_order and times[s.pk][0]]
    return render(request,'admin/bells_editor.html',dict(site.each_context(request),title='Звонки',
        weekday=weekday,weekdays=WEEKDAYS[:6],bell_rows=list(zip(formset,slots)),formset=formset,
        short_form=short_form,rule=rule,preview=preview,short_days=ShortenedDay.objects.order_by('-date')[:40]))


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
