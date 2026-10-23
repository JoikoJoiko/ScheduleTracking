import re
from django.db import migrations


def fill_named_rooms(apps,schema_editor):
    Room=apps.get_model('schedule','Room');Campus=apps.get_model('schedule','RoomCampus')
    alias=schema_editor.connection.alias
    main=None
    for room in Room.objects.using(alias).all().iterator():
        code=re.sub(r'[\s.\-]+','',room.number.casefold())
        floor=1 if code=='аз' else 2 if code in ['библ','библиотека'] else None
        if floor is None:continue
        fields=[]
        # Fill missing metadata without replacing saved manual corrections.
        if room.floor is None:room.floor=floor;fields.append('floor')
        if room.campus_id is None:
            if main is None:main=Campus.objects.using(alias).get_or_create(name='1 корпус · Основной')[0]
            room.campus_id=main.pk;fields.append('campus')
        if fields:room.save(using=alias,update_fields=fields)


class Migration(migrations.Migration):
    dependencies=[('schedule','0014_main_room_campus')]
    operations=[migrations.RunPython(fill_named_rooms,migrations.RunPython.noop)]
