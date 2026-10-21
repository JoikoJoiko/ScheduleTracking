from datetime import time
from django.db import migrations


def defaults(apps,schema_editor):
    Profile=apps.get_model('schedule','ImportProfile')
    Slot=apps.get_model('schedule','TimeSlot')
    Profile.objects.get_or_create(name='Расписание колледжа',kind='college')
    bells=[('08:00','09:30'),('09:40','11:10'),('11:40','13:10'),('13:20','14:50'),
           ('15:00','16:30'),('17:00','18:30'),('18:40','20:10')]
    for i,(a,b) in enumerate(bells,1):
        Slot.objects.get_or_create(sort_order=i,defaults={'name':f'{i} пара','start_time':time.fromisoformat(a),'end_time':time.fromisoformat(b)})
    Slot.objects.get_or_create(sort_order=0,defaults={'name':'Разговоры о важном'})


class Migration(migrations.Migration):
    dependencies=[('schedule','0002_importbatch_metadata_scheduleversion_groups_and_more')]
    operations=[migrations.RunPython(defaults,migrations.RunPython.noop)]
