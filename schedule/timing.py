"""One calendar and bell calculation for lessons, replacements and room occupancy."""
from datetime import timedelta
from .models import AcademicYear, CalendarDay, TimeSlot, WeekdayBell


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


def bell_times(day):
    # Overrides follow the actual weekday, even when a calendar exception moves lessons.
    overrides = {x.time_slot_id: (x.start_time, x.end_time)
                 for x in WeekdayBell.objects.filter(day_of_week=day.weekday())}
    return {slot.pk: overrides.get(slot.pk, (slot.start_time, slot.end_time)) for slot in TimeSlot.objects.all()}


def slot_times(slot, day, times=None):
    return (times if times is not None else bell_times(day)).get(slot.pk, (slot.start_time, slot.end_time))


def bells_for(day):
    times = bell_times(day)
    return [{'id': slot.pk, 'number': slot.sort_order, 'name': slot.name,
             'start': start.isoformat(timespec='minutes'), 'end': end.isoformat(timespec='minutes')}
            for slot in TimeSlot.objects.all() for start, end in [times[slot.pk]] if start and end]
