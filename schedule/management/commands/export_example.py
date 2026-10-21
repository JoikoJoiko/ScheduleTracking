from pathlib import Path
from openpyxl import Workbook
from django.core.management.base import BaseCommand
from schedule.importing import HEADERS,FIELDS

class Command(BaseCommand):
    help='Создать образец импортируемого XLSX'
    def add_arguments(self,parser):
        parser.add_argument('--output',default='examples/schedule.xlsx')
    def handle(self,*args,**options):
        target=Path(options['output'])
        target.parent.mkdir(parents=True,exist_ok=True)
        book=Workbook(); sheet=book.active; sheet.title='Расписание'
        sheet.append([HEADERS[k] for k in FIELDS])
        sheet.append(['Понедельник',1,'ИС-1-23','','Базы данных','Пигачева Анна Сергеевна','Корпус 1','101','all','Практика','',''])
        sheet.append(['Вторник',2,'ИС-1-23','','Математика','Смирнов Дмитрий Андреевич','Корпус 1','102','all','Лекция','',''])
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width=min(45,max(14,max(len(str(c.value or '')) for c in col)+2))
        sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
        book.save(target)
        self.stdout.write(str(target.resolve()))
