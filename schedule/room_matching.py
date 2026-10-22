"""Room numbers need stricter matching than abbreviated subject names."""
import re
from collections import Counter
from functools import lru_cache
from .models import Room,Lesson,CalendarDay
from .timing import week_info

GLYPHS={'0':'[0ОоOo]','1':'[1IiIlLі]','3':'[3Зз]','5':'[5Ss]','6':'[6бБ]',
        'п':'(?:п|П|n|N|rr|гг|7|т)','о':'[оОoO0]','л':'[лЛlL]'}


def code(value):
    return re.sub(r'[^а-яa-z0-9]','',str(value).casefold().translate(str.maketrans({'n':'п','o':'о','c':'с'})))


@lru_cache(maxsize=512)
def room_pattern(name):
    name=code(name)
    if re.fullmatch(r'сз(?:ал)?[п12]?',name):
        return re.compile(r'(?<!\w)'+r'[.\s-]*'.join('[сc]' if ch=='с' else re.escape(ch) for ch in name)+r'\.?(?!\w)',re.I)
    if not re.fullmatch(r'\d{2,3}[а-яa-z]*\d?',name):
        return None
    return re.compile(r'(?<![\w.])'+''.join(GLYPHS.get(ch,re.escape(ch)) for ch in name)+r'(?!\w)',re.I)


def room_proposal(group,day,row,decoded,originals,curriculum,subject_id,teacher_id,confident_subject):
    rooms=list(Room.objects.all())
    room_texts=[t for t in [row.get('room_text',''),*(d['room_text'] for d in decoded)] if t]
    raw_texts=[row.get('raw',''),*row.get('raw_variants',[])]
    # Teacher initials «С.З.» must not turn into the sports-room alias «С.з.».
    raw_texts=[re.sub(r'(?<!\w)[А-ЯЁA-Z]\s*[.,]\s*[А-ЯЁA-Z]\s*[.,]?(?!\w)',' ',text,flags=re.I) for text in raw_texts]
    texts=list(dict.fromkeys([*room_texts,*raw_texts]))
    week=week_info(day)
    calendar=CalendarDay.objects.filter(date=day).first()
    weekday=calendar.weekday_override if calendar and calendar.weekday_override is not None else day.weekday()
    history=[]
    if curriculum.get('version') and subject_id:
        lessons=Lesson.objects.filter(version_id=curriculum['version']['id'],participants__group=group,subject_id=subject_id)
        if teacher_id:
            lessons=lessons.filter(teacher_id=teacher_id)
        history=list(lessons.distinct().values('room_id','week_type','day_of_week','time_slot__sort_order'))
    current=[l for l in history if l['week_type'] in ['all',week['type']]]
    relevant=current or history
    context=Counter()
    for lesson in relevant:
        context[lesson['room_id']]+=2+(2 if lesson['day_of_week']==weekday else 0)+(1 if lesson['time_slot__sort_order']==row.get('slot') else 0)
    original_rooms={l['room_id'] for l in originals}
    same_subject=any(l['subject_id']==subject_id and l['teacher_id']==teacher_id for l in originals)
    for room_id in original_rooms:
        context[room_id]+=8 if same_subject or not confident_subject else 1
    sports=any(re.search(r'(?<!\w)[сc][.\s-]*(?:зал\w*|sza\w*|[зэ][аоиэс][лтс]?)(?!\w)',text,re.I) for text in texts)
    ranks=[]
    for room in rooms:
        canonical=code(room.number);pattern=room_pattern(room.number)
        matches=[m.group() for text in texts for m in ([pattern.search(text)] if pattern else []) if m]
        exact=any(code(match)==canonical for match in matches)
        score=1.0 if exact else .92 if matches else 0
        # A bare 103 can mean 103п1; a clearly printed 411 cannot become 101.
        if not score:
            for text in room_texts:
                source=code(text)
                number=re.match(r'\d{2,3}',source)
                target=re.match(r'\d{2,3}',canonical)
                if number and target and number[0]==target[0] and (canonical.startswith(source) or source.startswith(canonical)):
                    score=max(score,.78)
        is_gym=bool(re.match(r'с(?:зал|з)',canonical))
        if sports and is_gym:
            score=max(score,.86)
        if score:
            ranks.append((score,len(matches),context[room.pk],room.building_id==group.building_id,room.pk,room))
    ranks.sort(key=lambda r:(-r[0],-r[1],-r[2],-r[3],r[4]))
    notes=[]
    if ranks:
        best=ranks[0];room=best[-1]
        ambiguous=len(ranks)>1 and abs(best[0]-ranks[1][0])<.04
        confidence=1 if best[0]==1 and not ambiguous else .85 if best[0]>=.9 else .65
        if best[0]<1 or ambiguous:
            notes.append(f'Кабинет: по фрагменту и расписанию группы → {room.number}')
        if ambiguous:
            alternatives=', '.join(r[-1].number for r in ranks[:3] if abs(best[0]-r[0])<.04)
            notes.append(f'Проверьте кабинет: {alternatives}')
            confidence=min(confidence,.6)
        return {'room_id':room.pk,'confidence':confidence,'notes':notes,
                'candidates':[{'id':r[-1].pk,'name':r[-1].number} for r in ranks[:5]]}
    if confident_subject and not same_subject and context and relevant:
        possible={l['room_id'] for l in relevant}
        chosen=max(possible,key=lambda pk:(context[pk],-pk))
        reason=f'по дисциплине группы, {week["label"]}' if current else 'по дисциплине группы, вне текущей недели'
    elif original_rooms:
        chosen=max(original_rooms,key=lambda pk:(context[pk],-pk));reason=f'исходная пара группы, {week["label"]}'
    elif context:
        chosen=max(context,key=lambda pk:(context[pk],-pk));reason='по дисциплине группы'
    else:
        return {'room_id':None,'confidence':0,'notes':['Кабинет не распознан'],'candidates':[]}
    room=next(r for r in rooms if r.pk==chosen)
    note=f'Кабинет: {reason} → {room.number}; на изображении не разобран'
    if room_texts:
        note=f'Кабинет «{room_texts[0]}» не найден; предложен {room.number} ({reason})'
    return {'room_id':chosen,'confidence':.5,'notes':[note],'candidates':[{'id':chosen,'name':room.number}]}
