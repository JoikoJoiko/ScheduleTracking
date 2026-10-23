import re
from django.db import migrations


def group_first_complex(apps,schema_editor):
    Room=apps.get_model('schedule','Room');Campus=apps.get_model('schedule','RoomCampus')
    alias=schema_editor.connection.alias
    for name in ['Производственный','Общежитие']:
        old=Campus.objects.using(alias).filter(name=name).first()
        if not old:continue
        target=Campus.objects.using(alias).filter(name='1 корпус · '+name).first()
        if target:
            Room.objects.using(alias).filter(campus_id=old.pk).update(campus_id=target.pk)
            old.delete(using=alias)
        else:
            old.name='1 корпус · '+name;old.save(using=alias,update_fields=['name'])
    main=None
    for room in Room.objects.using(alias).filter(campus=None).iterator():
        code=re.sub(r'[\s.\-]+','',room.number.casefold())
        # Unknown letter suffixes and manually assigned locations remain untouched.
        if code.isdigit() or re.fullmatch(r'(?:сзал|спортзал|спортивныйзал)\d*',code):
            if main is None:main=Campus.objects.using(alias).get_or_create(name='1 корпус · Основной')[0]
            room.campus_id=main.pk;room.save(using=alias,update_fields=['campus'])


class Migration(migrations.Migration):
    dependencies=[('schedule','0013_fill_room_locations')]
    operations=[migrations.RunPython(group_first_complex,migrations.RunPython.noop)]
