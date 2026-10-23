import re
from django.db import migrations


def fill_locations(apps,schema_editor):
    # Keep the numbering rules here so historical migrations do not depend on app code.
    Room=apps.get_model('schedule','Room');Campus=apps.get_model('schedule','RoomCampus')
    alias=schema_editor.connection.alias
    for room in Room.objects.using(alias).all().iterator():
        code=re.sub(r'[\s.\-]+','',room.number.casefold())
        match=re.match(r'^(\d{3})(?!\d)',code)
        floor=int(match[1])//100 if match else None
        if re.fullmatch(r'(?:сзал|спортзал|спортивныйзал)\d*',code):floor=1
        suffix=re.fullmatch(r'\d+([поpo])\d*',code) or re.search(r'[.\s]([поpo])\s*$',room.number.casefold())
        campus={'п':'Производственный','p':'Производственный','о':'Общежитие','o':'Общежитие'}.get(suffix[1]) if suffix else None
        fields=[]
        if room.floor is None and floor is not None:room.floor=floor;fields.append('floor')
        if room.campus_id is None and campus:
            room.campus_id=Campus.objects.using(alias).get_or_create(name=campus)[0].pk;fields.append('campus')
        if fields:room.save(using=alias,update_fields=fields)


class Migration(migrations.Migration):
    dependencies=[('schedule','0012_manual_changes_and_room_campuses')]
    operations=[migrations.RunPython(fill_locations,migrations.RunPython.noop)]
