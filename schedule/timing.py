"""One calendar and bell calculation for lessons, replacements and room occupancy."""
from datetime import datetime, timedelta
from django.core.exceptions import ValidationError
from .models import AcademicYear, CalendarDay, TimeSlot, WeekdayBell, ShortenedDay


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


def shortened_times(day,times,rule):
    result=dict(times)
    slots=[s for s in TimeSlot.objects.filter(sort_order__gt=0) if times.get(s.pk,(None,None))[0]]
    if not slots:
        return result
    cursor=datetime.combine(day,rule.start_time or times[slots[0].pk][0])
    for i,slot in enumerate(slots):
        end=cursor+timedelta(minutes=rule.lesson_minutes)
        if end.date()!=day:
            raise ValidationError('Сокращённое расписание должно заканчиваться в тот же день')
        result[slot.pk]=(cursor.time(),end.time())
        if i<len(slots)-1:
            if rule.keep_breaks:
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
    return {'id':rule.pk,'minutes':rule.lesson_minutes,'note':rule.note} if rule else None


def slot_times(slot, day, times=None):
    return (times if times is not None else bell_times(day)).get(slot.pk, (slot.start_time, slot.end_time))


def bells_for(day):
    if day.weekday()==6:
        return []
    times = bell_times(day)
    return [{'id': slot.pk, 'number': slot.sort_order, 'name': slot.name,
             'start': start.isoformat(timespec='minutes'), 'end': end.isoformat(timespec='minutes')}
            for slot in TimeSlot.objects.all() for start, end in [times[slot.pk]] if start and end]
