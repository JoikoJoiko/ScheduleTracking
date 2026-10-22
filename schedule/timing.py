"""One calendar and bell calculation for lessons, replacements and room occupancy."""
from datetime import datetime, timedelta
from django.core.exceptions import ValidationError
from .models import AcademicYear, CalendarDay, TimeSlot, WeekdayBell, ShortenedDay, CuratorBell


def week_info(day, year=None, calendar=None):
    year = year or AcademicYear.objects.filter(start_date__lte=day, end_date__gte=day).first()
    monday = day - timedelta(days=day.weekday())
    result = {'start': monday.isoformat(), 'end': (monday+timedelta(days=6)).isoformat(),
              'number': None, 'type': None, 'label': 'Неделя не настроена'}
    if year is None:
        return result
    anchor = year.week_anchor_date or year.start_date-timedelta(days=year.start_date.weekday())
    number = (year.week_anchor_number-1+(monday-anchor).days//7) % 2+1
    calendar = calendar or CalendarDay.objects.filter(date=day, year=year).first()
    override = bool(calendar and calendar.week_type)
    if override:
        number = 1 if calendar.week_type == 'odd' else 2
    result.update(number=number, type='odd' if number == 1 else 'even', label=f'{number}-я неделя',
                  year_id=year.pk, anchor_date=anchor.isoformat(), anchor_number=year.week_anchor_number,
                  overridden=override)
    return result


def regular_bell_times(day):
    # Overrides follow the actual weekday, even when a calendar exception moves lessons.
    overrides = {x.time_slot_id: (x.start_time, x.end_time)
                 for x in WeekdayBell.objects.filter(day_of_week=day.weekday())}
    return {slot.pk: overrides.get(slot.pk, (slot.start_time, slot.end_time)) for slot in TimeSlot.objects.all()}


def shortened_times(day,times,rule,overrides=None):
    result=dict(times)
    slots=[]
    for slot in TimeSlot.objects.filter(sort_order__gt=0):
        if rule.lesson_count is not None and slot.sort_order>rule.lesson_count:
            result[slot.pk]=(None,None)
        elif times.get(slot.pk,(None,None))[0]:
            slots.append(slot)
    # Pending rows let the form validate the entire edited scheme before writing it.
    overrides=overrides if overrides is not None else (list(rule.bells.all()) if rule.pk else [])
    individual={row.time_slot_id:row for row in overrides}
    if not slots:
        return result
    cursor=datetime.combine(day,rule.start_time or times[slots[0].pk][0])
    for i,slot in enumerate(slots):
        row=individual.get(slot.pk)
        duration=row.lesson_minutes if row and row.lesson_minutes is not None else rule.lesson_minutes
        end=cursor+timedelta(minutes=duration)
        if end.date()!=day:
            raise ValidationError('Сокращённое расписание должно заканчиваться в тот же день')
        result[slot.pk]=(cursor.time(),end.time())
        if i<len(slots)-1:
            if row and row.break_minutes is not None:
                pause=row.break_minutes
            elif rule.keep_breaks:
                pause=max(0,(datetime.combine(day,times[slots[i+1].pk][0])-datetime.combine(day,times[slot.pk][1])).total_seconds()/60)
            else:
                pause=rule.break_minutes or 0
            cursor=end+timedelta(minutes=pause)
    return result


def bell_times(day):
    times=regular_bell_times(day)
    rule=ShortenedDay.objects.filter(date=day,is_active=True).first()
    return shortened_times(day,times,rule) if rule else times


def short_day_info(day):
    rule=ShortenedDay.objects.filter(date=day,is_active=True).first()
    if not rule:
        return None
    numbered=[b for b in bells_for(day) if b['number']>0]
    durations={(datetime.combine(day,datetime.strptime(b['end'],'%H:%M').time())-
                datetime.combine(day,datetime.strptime(b['start'],'%H:%M').time())).seconds//60 for b in numbered}
    varied=len(durations)>1
    count=len(numbered)
    label=f'Сокращённый день · {count} '+('пара' if count==1 else 'пары' if 2<=count<=4 else 'пар')
    if len(durations)==1:
        label+=f' по {next(iter(durations))} мин'
    return {'id':rule.pk,'minutes':rule.lesson_minutes,'note':rule.note,
            'lesson_count':count,'varied_duration':varied,'label':label}


def lesson_limit(day):
    """A shorter teaching day hides late pairs without altering the Excel snapshot."""
    return ShortenedDay.objects.filter(date=day,is_active=True).values_list('lesson_count',flat=True).first()


def require_teaching_slot(day,number):
    limit=lesson_limit(day)
    if limit is not None and number>limit:
        raise ValidationError(f'На {day:%d.%m.%Y} предусмотрено только {limit} пар')


def slot_times(slot, day, times=None):
    return (times if times is not None else bell_times(day)).get(slot.pk, (slot.start_time, slot.end_time))


def curator_bells_for(day):
    if day.weekday()!=0:
        return []
    return [{'shift':bell.shift,'name':'Кураторский час','subject':'Разговоры о важном',
             'start':bell.start_time.isoformat(timespec='minutes'),
             'end':bell.end_time.isoformat(timespec='minutes')} for bell in CuratorBell.objects.all()]


def curator_times(shift):
    bell=CuratorBell.objects.filter(shift=shift).first() if shift else None
    return (bell.start_time,bell.end_time) if bell else (None,None)


def bells_for(day):
    if day.weekday()==6:
        return []
    times = bell_times(day)
    return [{'id': slot.pk, 'number': slot.sort_order, 'name': slot.name,
             'start': start.isoformat(timespec='minutes'), 'end': end.isoformat(timespec='minutes')}
            for slot in TimeSlot.objects.filter(sort_order__gt=0) for start, end in [times[slot.pk]] if start and end]
