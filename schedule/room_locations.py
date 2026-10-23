"""Display locations inferred from college room numbers, without changing import identity."""
import re
from .models import RoomCampus


def location_from_number(number):
    code=re.sub(r'[\s.\-]+','',number.casefold())
    numeric=re.match(r'^(\d{3})(?!\d)',code)
    floor=int(numeric[1])//100 if numeric else None
    if re.fullmatch(r'(?:сзал|спортзал|спортивныйзал)\d*',code):
        floor=1
    suffix=re.fullmatch(r'\d+([поpo])\d*',code) or re.search(r'[.\s]([поpo])\s*$',number.casefold())
    campus={'п':'Производственный','p':'Производственный','о':'Общежитие','o':'Общежитие'}.get(suffix[1]) if suffix else None
    return floor,campus


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
