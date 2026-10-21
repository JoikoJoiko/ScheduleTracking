from datetime import date, time, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from schedule.models import *


class Command(BaseCommand):
    help = 'Создать демонстрационные справочники и расписание без паролей по умолчанию'
    @transaction.atomic
    def handle(self,*args,**options):
        today=timezone.localdate()
        start_year=today.year if today.month>=9 else today.year-1
        year,_=AcademicYear.objects.get_or_create(name=f'{start_year}/{start_year+1}',defaults={
            'start_date':date(start_year,9,1),'end_date':date(start_year+1,8,31),'status':'active'})
        period,_=Period.objects.get_or_create(name='Учебный год',year=year,defaults={'start_date':year.start_date,'end_date':year.end_date})
        building,_=Building.objects.get_or_create(name='Корпус 1',defaults={'address':'Учебная улица, 1'})
        building2,_=Building.objects.get_or_create(name='Корпус 2',defaults={'address':'Учебная улица, 2'})
        lecture,_=RoomType.objects.get_or_create(name='Учебная аудитория')
        computer,_=RoomType.objects.get_or_create(name='Компьютерный класс')
        lab,_=RoomType.objects.get_or_create(name='Лаборатория')
        rooms=[]
        for b in [building,building2]:
            for i,number in enumerate(['101','102','201','202','301','302']):
                room,_=Room.objects.get_or_create(building=b,number=number,defaults={'floor':int(number[0]),'capacity':24+i*2,
                    'room_type':[lecture,computer,lab][i%3],'description':'Проектор, доска' if i%3==0 else 'Учебное оборудование'})
                rooms.append(room)
        specialty,_=Specialty.objects.get_or_create(code='09.02.07',defaults={'name':'Информационные системы и программирование'})
        groups=[]
        for i,name in enumerate(['ИС-1-23','ИС-2-24','ПР-1-25','ИС-1-26']):
            group,_=StudentGroup.objects.get_or_create(name=name,defaults={'specialty':specialty,'building':building,
                'admission_year':start_year-min(3,3-i),'graduation_year':start_year+1+i})
            GroupYear.objects.get_or_create(group=group,year=year,defaults={'course':4-i})
            for sub in ['1','2']:
                Subgroup.objects.get_or_create(group=group,name=sub)
            groups.append(group)
        teachers=[Teacher.objects.get_or_create(name=name)[0] for name in ['Пигачева Анна Сергеевна','Смирнов Дмитрий Андреевич','Кузнецова Елена Викторовна','Морозов Алексей Игоревич']]
        subjects=[Subject.objects.get_or_create(name=name)[0] for name in ['Разработка программных модулей','Базы данных','Математика','Иностранный язык','Информационные технологии','Компьютерные сети']]
        slots=[]
        for i,(a,b) in enumerate([('08:30','10:00'),('10:10','11:40'),('12:10','13:40'),('13:50','15:20'),('15:30','17:00'),('17:10','18:40')],1):
            slot,_=TimeSlot.objects.get_or_create(sort_order=i,defaults={'name':f'{i} пара','start_time':time.fromisoformat(a),'end_time':time.fromisoformat(b)})
            slots.append(slot)
        version,created=ScheduleVersion.objects.get_or_create(name='Демонстрационное расписание',period=period,
            defaults={'effective_from':period.start_date,'effective_to':period.end_date,'status':'published','published_at':timezone.now()})
        if created:
            for day in range(6):
                for slot_index in range(4):
                    for i,group in enumerate(groups):
                        lesson=Lesson.objects.create(version=version,day_of_week=day,time_slot=slots[slot_index],
                            subject=subjects[(day+slot_index+i)%len(subjects)],teacher=teachers[(slot_index+i)%4],room=rooms[i],lesson_type='Практическое занятие')
                        Participant.objects.create(lesson=lesson,group=group)
        ImportProfile.objects.get_or_create(name='Стандартная таблица XLSX',defaults={'kind':'rows'})
        SystemSetting.objects.get_or_create(key='organization',defaults={'value':'Расписание колледжа','description':'Наименование организации'})
        self.stdout.write(self.style.SUCCESS('Демонстрационные данные готовы. Создайте администратора: python manage.py createsuperuser'))
