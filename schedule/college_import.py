"""Parser for the college's two-week, two-row-per-pair Excel layout."""
import re
from datetime import date, time
from .workbooks import load_schedule_workbook
from django.core.exceptions import ValidationError
from .models import (AcademicYear, Period, Building, RoomType, Room, Teacher, Subject,
                     StudentGroup, GroupYear, Subgroup, TimeSlot, WeekdayBell, CuratorBell)
from .class_hours import is_class_hour, TITLE

BELLS = [('09:00','10:30'),('10:40','12:10'),('12:40','14:10'),('14:20','15:50'),
         ('16:00','17:30'),('18:00','19:30'),('19:40','21:10')]
MONDAY_BELLS = BELLS[:3]+[('14:50','16:20'),('16:30','18:00'),('18:30','20:00'),('20:10','21:40')]
DAYS = ['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье']
TEACHER = re.compile(r'([А-ЯЁA-Z][а-яёa-zА-ЯЁ-]+)\s+([А-ЯЁA-Z])\s*[.,]?\s*([А-ЯЁA-Z])\s*[.,]?')


def text(value):
    return ' '.join(str(value if value is not None else '').split())


def teacher_name(value):
    match=TEACHER.search(value)
    return f'{match[1]} {match[2]}.{match[3]}.' if match else value.strip(' ,')


class Grid:
    def __init__(self,sheet):
        self.sheet=sheet
        self.merged={}
        for area in sheet.merged_cells.ranges:
            for r in range(area.min_row,area.max_row+1):
                for c in range(area.min_col,area.max_col+1):
                    self.merged[r,c]=area
    def value(self,r,c):
        area=self.merged.get((r,c))
        return text(self.sheet.cell(area.min_row,area.min_col).value if area else self.sheet.cell(r,c).value)


def parse_college(data,filename=''):
    book=load_schedule_workbook(data,data_only=True)
    rows,errors,warnings,groups=[],[],[],[]
    start=end=None
    for sheet in book:
        if sheet.max_row<8:
            continue
        if sheet.max_row>10000 or sheet.max_column>200:
            raise ValidationError('Превышен размер листа')
        grid=Grid(sheet)
        group_row=None
        for r in range(1,min(20,sheet.max_row)+1):
            found=[c for c in range(1,sheet.max_column+1) if re.fullmatch(r'[А-Яа-яЁёA-Za-z()]+-\d+-\d{2,4}',text(sheet.cell(r,c).value))]
            if found:
                group_row=r
                columns=found
                break
        if group_row is None:
            errors.append(f'{sheet.title}: не найдена строка групп')
            continue
        for r in range(1,group_row):
            for c in range(1,sheet.max_column+1):
                dates=re.findall(r'(\d{2})\.(\d{2})\.(\d{4})',grid.value(r,c))
                if len(dates)>=2:
                    start=date(*map(int,reversed(dates[0])));end=date(*map(int,reversed(dates[1])))
                    break
        if not start:
            raise ValidationError('Не найден период действия расписания')
        school_year=start.year if start.month>=9 else start.year-1
        for c in columns:
            name=grid.value(group_row,c)
            intake=int(name.rsplit('-',1)[1]);intake=intake+2000 if intake<100 else intake
            course=school_year-intake+1
            if not 1<=course<=4:
                raise ValidationError(f'{name}: курс {course} выходит за диапазон 1–4')
            groups.append({'name':name,'admission_year':intake,'course':course})
        # Class hours sit outside the merged weekday header. Look ahead to the
        # next numbered pair so Monday morning belongs to the correct week.
        contexts={}
        weekday=None;cycle=0;seen_days=set()
        for r in range(group_row+2,sheet.max_row+1):
            label=text(sheet.cell(r,1).value).casefold()
            if label in DAYS:
                weekday=DAYS.index(label)
                if weekday in seen_days:
                    cycle+=1;seen_days=set()
                seen_days.add(weekday)
            raw_slot=text(sheet.cell(r,2).value)
            if weekday is not None and raw_slot.isdigit():
                contexts[r]=(weekday,cycle,int(raw_slot))
        for r in range(group_row+2,sheet.max_row+1):
            context=contexts.get(r)
            conversation=any(is_class_hour(grid.value(r,c+part)) for c in columns for part in [0,2])
            if not context and not conversation:
                continue
            next_context=next((contexts[n] for n in range(r+1,min(r+10,sheet.max_row+1)) if n in contexts),None)
            if not context:
                context=next_context
            if not context:
                warnings.append(f'{sheet.title}:{r}: не определён день кураторского часа')
                continue
            weekday,cycle,slot=context
            if cycle>1:
                errors.append(f'{sheet.title}:{r}: больше двух недель')
                continue
            for c in columns:
                whole=grid.merged.get((r,c))
                whole=whole is not None and whole.max_col>=c+2
                parts=[(c,c+3,'')] if whole else [(c,c+1,'1'),(c+2,c+3,'2')]
                for subject_col,room_col,subgroup in parts:
                    subject=grid.value(r,subject_col)
                    if not subject or subject in ['-','—']:
                        continue
                    class_hour=is_class_hour(subject)
                    if r not in contexts and not class_hour:
                        continue
                    area=grid.merged.get((r,subject_col))
                    if class_hour and area and area.min_row!=r:
                        continue
                    room=grid.value(r,room_col)
                    teacher=grid.value(r+1,subject_col)
                    if class_hour:
                        match=TEACHER.search(subject)
                        teacher=teacher_name(match[0]) if match else ''
                        subject=subject[:match.start()].strip() if match else subject
                        subject=TITLE
                    teacher=teacher_name(teacher)
                    source=f'{sheet.title}!{sheet.cell(r,subject_col).coordinate}'
                    if not room or room==subject or not teacher or not TEACHER.fullmatch(teacher):
                        errors.append(f'{source}: проверьте преподавателя и кабинет ({teacher!r}, {room!r})')
                        continue
                    rows.append({'group':grid.value(group_row,c),'subgroup':subgroup,'subject':subject,
                        'teacher':teacher,'room':room,'day_of_week':weekday,'slot':0 if class_hour else slot,
                        **({'curator_shift':1 if slot==1 else 2} if class_hour else {}),
                        'week_type':'odd' if cycle==0 else 'even','source':source})
                    if len(rows)>5000:
                        raise ValidationError('Не более 5000 записей за один импорт')
    if not rows:
        raise ValidationError('В файле нет занятий')
    # Match the two week blocks by timetable content, not by cell formatting.
    return rows,{'groups':groups,'start':start.isoformat(),'end':end.isoformat(),
        'year_start':school_year,'warnings':warnings,'parser_revision':2,'course':groups[0]['course'] if groups else None},errors


def install_bells():
    for number,(start,end) in enumerate(BELLS,1):
        TimeSlot.objects.update_or_create(sort_order=number,defaults={'name':f'{number} пара',
            'start_time':time.fromisoformat(start),'end_time':time.fromisoformat(end)})
    TimeSlot.objects.update_or_create(sort_order=0,defaults={'name':'Разговоры о важном','start_time':None,'end_time':None})
    for number,(start,end) in enumerate(MONDAY_BELLS[3:],4):
        WeekdayBell.objects.get_or_create(day_of_week=0,time_slot=TimeSlot.objects.get(sort_order=number),
            defaults={'start_time':time.fromisoformat(start),'end_time':time.fromisoformat(end)})
    for shift,start,end in [(1,'08:20','08:55'),(2,'14:15','14:45')]:
        CuratorBell.objects.get_or_create(shift=shift,defaults={'start_time':time.fromisoformat(start),'end_time':time.fromisoformat(end)})


def ensure_period(metadata):
    y=metadata['year_start']
    year,_=AcademicYear.objects.get_or_create(name=f'{y}/{y+1}',defaults={
        'start_date':date(y,9,1),'end_date':date(y+1,8,31),'status':'active'})
    period,_=Period.objects.get_or_create(year=year,name='Учебный год',defaults={'start_date':year.start_date,'end_date':year.end_date})
    return period


def materialize(rows,metadata,period):
    building,_=Building.objects.get_or_create(name='Колледж')
    room_type,_=RoomType.objects.get_or_create(name='Кабинет')
    groups={}
    for raw in metadata['groups']:
        group,_=StudentGroup.objects.get_or_create(name=raw['name'],defaults={'building':building,
            'admission_year':raw['admission_year'],'graduation_year':raw['admission_year']+4})
        GroupYear.objects.update_or_create(group=group,year=period.year,defaults={'course':raw['course'],'status':'active'})
        groups[group.name]=group
    payload=[]
    for row in rows:
        slot=TimeSlot.objects.get(sort_order=row['slot'])
        group=groups[row['group']]
        subgroup=Subgroup.objects.get_or_create(group=group,name=row['subgroup'])[0] if row['subgroup'] else None
        room=Room.objects.get_or_create(building=building,number=row['room'],defaults={'room_type':room_type})[0]
        teacher=Teacher.objects.get_or_create(name=row['teacher'])[0]
        curricular=row['slot']!=0
        subject=Subject.objects.get_or_create(name=row['subject'],defaults={'is_curricular':curricular})[0]
        if not curricular and subject.is_curricular:
            subject.is_curricular=False;subject.save(update_fields=['is_curricular'])
        payload.append({'day_of_week':row['day_of_week'],'time_slot_id':slot.pk,'group_id':group.pk,
            'subgroup_id':subgroup.pk if subgroup else None,'room_id':room.pk,'teacher_id':teacher.pk,
            'subject_id':subject.pk,'week_type':row['week_type'],'lesson_type':'' if curricular else 'class_hour',
            'curator_shift':row.get('curator_shift'),'start_time':None,'end_time':None})
    return payload
