"""Parser for the college's two-week, two-row-per-pair Excel layout."""
import re
from datetime import date, time
from .workbooks import load_schedule_workbook
from django.core.exceptions import ValidationError
from .models import (AcademicYear, Period, Building, RoomType, Room, Teacher, Subject,
                     StudentGroup, GroupYear, Subgroup, TimeSlot)

BELLS = [('08:00','09:30'),('09:40','11:10'),('11:40','13:10'),('13:20','14:50'),
         ('15:00','16:30'),('17:00','18:30'),('18:40','20:10')]
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
        weekday=None;cycle=0;seen_days=set()
        for r in range(group_row+2,sheet.max_row+1):
            label=text(sheet.cell(r,1).value).casefold()
            if label in DAYS:
                weekday=DAYS.index(label)
                if weekday in seen_days:
                    cycle+=1;seen_days=set()
                seen_days.add(weekday)
            raw_slot=text(sheet.cell(r,2).value)
            conversation=any(grid.value(r,c).startswith('Разговоры') for c in columns)
            if weekday is None or (not raw_slot.isdigit() and not conversation):
                continue
            if cycle>1:
                errors.append(f'{sheet.title}:{r}: больше двух недель')
                continue
            slot=int(raw_slot) if raw_slot.isdigit() else 0
            for c in columns:
                whole=grid.merged.get((r,c))
                whole=whole is not None and whole.max_col>=c+2
                parts=[(c,c+3,'')] if whole else [(c,c+1,'1'),(c+2,c+3,'2')]
                for subject_col,room_col,subgroup in parts:
                    subject=grid.value(r,subject_col)
                    if not subject or subject in ['-','—']:
                        continue
                    room=grid.value(r,room_col)
                    teacher=grid.value(r+1,subject_col)
                    if conversation:
                        match=TEACHER.search(subject)
                        teacher=teacher_name(match[0]) if match else ''
                        subject=subject[:match.start()].strip() if match else subject
                    teacher=teacher_name(teacher)
                    source=f'{sheet.title}!{sheet.cell(r,subject_col).coordinate}'
                    if not room or room==subject or not teacher or not TEACHER.fullmatch(teacher):
                        errors.append(f'{source}: проверьте преподавателя и кабинет ({teacher!r}, {room!r})')
                        continue
                    rows.append({'group':grid.value(group_row,c),'subgroup':subgroup,'subject':subject,
                        'teacher':teacher,'room':room,'day_of_week':weekday,'slot':slot,
                        'week_type':'odd' if cycle==0 else 'even','source':source})
                    if len(rows)>5000:
                        raise ValidationError('Не более 5000 записей за один импорт')
    if not rows:
        raise ValidationError('В файле нет занятий')
    # Match the two week blocks by timetable content, not by cell formatting.
    return rows,{'groups':groups,'start':start.isoformat(),'end':end.isoformat(),
        'year_start':school_year,'warnings':warnings,'course':groups[0]['course'] if groups else None},errors


def install_bells():
    for number,(start,end) in enumerate(BELLS,1):
        TimeSlot.objects.update_or_create(sort_order=number,defaults={'name':f'{number} пара',
            'start_time':time.fromisoformat(start),'end_time':time.fromisoformat(end)})
    TimeSlot.objects.update_or_create(sort_order=0,defaults={'name':'Разговоры о важном','start_time':None,'end_time':None})


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
        subject=Subject.objects.get_or_create(name=row['subject'])[0]
        payload.append({'day_of_week':row['day_of_week'],'time_slot_id':slot.pk,'group_id':group.pk,
            'subgroup_id':subgroup.pk if subgroup else None,'room_id':room.pk,'teacher_id':teacher.pk,
            'subject_id':subject.pk,'week_type':row['week_type'],'lesson_type':'','start_time':None,'end_time':None})
    return payload
