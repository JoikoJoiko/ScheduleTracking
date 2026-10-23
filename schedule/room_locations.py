"""Display locations inferred from college room numbers, without changing import identity."""
import re
from .models import RoomCampus


def location_from_number(number):
    code=re.sub(r'[\s.\-]+','',number.casefold())
    # Named rooms share the same defaults for every spelling used in Excel.
    if code=='аз':return 1,'1 корпус · Основной'
    if code in ['библ','библиотека']:return 2,'1 корпус · Основной'
    numeric=re.match(r'^(\d{3})(?!\d)',code)
    floor=int(numeric[1])//100 if numeric else None
    if re.fullmatch(r'(?:сзал|спортзал|спортивныйзал)\d*',code):
        floor=1
    suffix=re.fullmatch(r'\d+([поpo])\d*',code) or re.search(r'[.\s]([поpo])\s*$',number.casefold())
    campus={'п':'Производственный','p':'Производственный','о':'Общежитие','o':'Общежитие'}.get(suffix[1]) if suffix else None
    if not campus and (code.isdigit() or re.fullmatch(r'(?:сзал|спортзал|спортивныйзал)\d*',code)):
        campus='Основной'
    if campus:campus='1 корпус · '+campus
    return floor,campus


def campus_sort_key(name):
    # The leading complex number keeps future second-complex sections together.
    match=re.match(r'^(\d+) корпус · (.+)$',name)
    number=int(match[1]) if match else 999
    section=match[2] if match else name
    order={'Основной':0,'Производственный':1,'Общежитие':2}.get(section,3)
    return number,order,section


def fill_room_location(room):
    # Fill only missing values: a saved manual correction always wins on reimport.
    floor,campus=location_from_number(room.number)
    fields=[]
    if room.floor is None and floor is not None:
        room.floor=floor;fields.append('floor')
    if room.campus_id is None and campus:
        room.campus=RoomCampus.objects.get_or_create(name=campus)[0];fields.append('campus')
    if fields:room.save(update_fields=fields)
    return fields
