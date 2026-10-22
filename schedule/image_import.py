"""Local OCR returns suggestions only; publication always needs a reviewed form."""
import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from PIL import Image,ImageOps,ImageFilter
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import ImageImport,StudentGroup,Teacher,Subject,Room,TimeSlot,Change,Subgroup,GroupYear,Lesson,PracticePeriod
from .college_import import teacher_name
from . import services as svc
from .curriculum import curriculum_for

MAX_IMAGE_BYTES=10*1024*1024


def image_bytes(upload):
    data=upload.read(MAX_IMAGE_BYTES+1)
    if len(data)>MAX_IMAGE_BYTES:
        raise ValidationError('Изображение больше 10 МБ')
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ['PNG','JPEG','WEBP'] or source.width*source.height>16000000:
                raise ValidationError('Используйте PNG, JPEG или WebP до 16 мегапикселей')
            source.verify()
        image=Image.open(io.BytesIO(data)).convert('RGB')
    except (OSError,Image.DecompressionBombError) as exc:
        raise ValidationError('Не удалось прочитать изображение') from exc
    return image


def bands(indices):
    result=[]
    for value in indices:
        if not result or value>result[-1][-1]+1:
            result.append([value])
        else:
            result[-1].append(value)
    return [round(sum(x)/len(x)) for x in result]


def table_edges(image):
    gray=image.convert('L');w,h=image.size;p=gray.load()
    ys=bands([y for y in range(h) if sum(p[x,y]<145 for x in range(w))>w*.55])
    if len(ys)<3:
        raise ValidationError('Не найдена сетка таблицы. Загрузите изображение целиком и в большем разрешении.')
    top,bottom=ys[0],ys[-1]
    xs=bands([x for x in range(w) if sum(p[x,y]<145 for y in range(top,bottom+1))>(bottom-top)*.52])
    if len(xs)<4:
        raise ValidationError('Не найдены столбцы таблицы')
    return xs,ys


def run_ocr(image):
    factor=min(3,2500/image.width,2500/image.height)
    enlarged=image.resize((round(image.width*factor),round(image.height*factor)),Image.Resampling.LANCZOS)
    with tempfile.TemporaryDirectory(prefix='schedule-ocr-') as folder:
        path=Path(folder)/'input.png';enlarged.save(path)
        if os.name=='nt':
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                str(settings.BASE_DIR/'tools'/'ocr_windows.ps1'),'-Path',str(path)],capture_output=True,timeout=90,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise ValidationError('Локальный OCR Windows недоступен. Проверьте русский языковой пакет.')
            parsed=json.loads(result.stdout.decode('utf-8-sig'))
            words=[word for line in parsed['lines'] for word in line['words']]
        elif shutil.which('tesseract'):
            result=subprocess.run(['tesseract',str(path),'stdout','-l','rus+eng','tsv'],capture_output=True,timeout=90)
            if result.returncode:
                raise ValidationError('Для OCR установите русский языковой пакет Tesseract')
            words=[{'text':r['text'],'x':int(r['left']),'y':int(r['top']),'width':int(r['width']),'height':int(r['height'])}
                   for r in csv.DictReader(io.StringIO(result.stdout.decode('utf-8')),delimiter='\t') if r.get('text','').strip()]
        else:
            raise ValidationError('OCR не установлен. Используйте Windows OCR или Tesseract с rus+eng.')
    for word in words:
        for key in ['x','y','width','height']:
            word[key]/=factor
    return words


def in_box(words,box):
    x1,y1,x2,y2=box
    selected=[w for w in words if x1<=w['x']+w['width']/2<x2 and y1<=w['y']+w['height']/2<y2]
    lines=[]
    for word in sorted(selected,key=lambda w:(w['y']+w['height']/2,w['x'])):
        center=word['y']+word['height']/2
        line=next((line for line in lines if abs(line[0]-center)<max(4,word['height']*.6)),None)
        if line is None:
            line=[center,[]];lines.append(line)
        line[1].append(word)
    return '\n'.join(' '.join(w['text'] for w in sorted(line[1],key=lambda w:w['x'])) for line in lines)


def normalized(value):
    value=value.casefold().replace('ё','е').translate(str.maketrans({'a':'а','c':'с','e':'е','o':'о','p':'р','x':'х','y':'у','k':'к','m':'м','t':'т','b':'в'}))
    return re.sub(r'[^а-яa-z0-9]','',value).replace('2з','23')


def subject_score(value, name):
    needle,target=normalized(value),normalized(name)
    if not needle or not target:
        return 0
    if needle==target:
        return 1
    if target in ['физическаякультура','физическоевоспитание'] and needle.startswith('ф') and needle.endswith('ра') and len(needle)<=8:
        return .97 if needle=='физра' else .88
    if re.findall(r'\d+',needle) and re.findall(r'\d+',needle)!=re.findall(r'\d+',target):
        return 0
    if needle in ['физра','физкультура'] and target in ['физическаякультура','физическоевоспитание']:
        return .97
    if target in ['физическаякультура','физическоевоспитание'] and 3<=len(needle)<=8:
        likeness=SequenceMatcher(None,needle,'физра').ratio()
        if likeness>=.7:
            return .95*likeness
    words=[normalized(w) for w in re.findall(r'[\w]+',name) if normalized(w)]
    tokens=[normalized(w) for w in re.findall(r'[\w]+',value) if normalized(w)]
    score=SequenceMatcher(None,needle,target).ratio()
    if len(needle)>=3 and (target.startswith(needle) or needle==''.join(w[0] for w in words)):
        score=max(score,.94)
    # Abbreviated words must match in order: «осн менедж» → «Основы менеджмента».
    position=0
    for token in tokens:
        found=next((i for i in range(position,len(words)) if words[i].startswith(token)),None)
        if found is None:
            break
        position=found+1
    else:
        if len(needle)>=3:
            score=max(score,.94 if len(tokens)>1 else .90)
    return score


def suggestion(model,value,candidates=None):
    if not value:
        return None
    needle=normalized(value)
    group_suffix=None
    if model==StudentGroup:
        candidate=value.replace('IIC','ИС').replace('IIС','ИС')
        match=re.search(r'(.+?)[.\-– ]([0-9lIЗзsS]+)[.\-– ]([0-9ЗзSБб]{2,4})(?![0-9])',candidate)
        if not match:
            return None
        fix=lambda s:s.translate(str.maketrans({'l':'1','I':'1','З':'3','з':'3','s':'5','S':'5','Б':'5','б':'5'}))
        group_suffix=(fix(match[2]),fix(match[3]))
        needle=normalized(match[1]+''.join(group_suffix))
    scored=[]
    for record in candidates if candidates is not None else model.objects.all():
        if isinstance(record,dict):
            name,pk=record['name'],record['id']
            short=record.get('short_name','')
        else:
            name=record.number if model==Room else record.name
            pk=record.pk
            short=getattr(record,'short_name','')
        if model==StudentGroup and tuple(name.split('-')[-2:])!=group_suffix:
            continue
        target=normalized(name)
        score=SequenceMatcher(None,needle,target).ratio()
        if model==Subject:
            score=max(subject_score(value,name),subject_score(value,short))
        scored.append((score,pk))
    scored.sort(reverse=True)
    margin=.05 if model==StudentGroup else .08
    if not scored or scored[0][0]<.74 or (len(scored)>1 and scored[0][0]-scored[1][0]<margin):
        return None
    return scored[0][1]


def resolve_candidates(group_id,day,subject,teacher,curriculum=None):
    result={'subject_id':None,'teacher_id':None}
    if not group_id:
        return result
    group=StudentGroup.objects.filter(pk=group_id).first()
    if not group:
        return result
    data=curriculum if curriculum is not None else curriculum_for(group,day)
    result['subject_id']=suggestion(Subject,subject,data['subjects'])
    result['teacher_id']=suggestion(Teacher,teacher,data['teachers'])
    return result


def teacher_score(value,name):
    """OCR often damages initials; a recognizable surname carries more weight."""
    surname=normalized(name.split()[0])
    tokens=[normalized(t) for t in re.findall(r'[\w-]+',value)]
    tokens += [a+b for a,b in zip(tokens,tokens[1:]) if len(a)>1 and len(b)>1]
    score=max((SequenceMatcher(None,t,surname).ratio() for t in tokens if len(t)>=3),default=0)
    initials=re.findall(r'\b([А-ЯЁA-Z])[.,]',name)
    if not initials and len(name.split())>1:
        initials=[p[0] for p in name.split()[1:3]]
    needle=normalized(value)
    match_initials=bool(initials and normalized(''.join(initials)) in needle)
    return min(1,score*.92+(.08 if match_initials else 0))


def abbreviation_score(value,name,short=''):
    # Try prefixes as well: the OCR may fail to separate the trailing teacher's name.
    tokens=re.findall(r'[\w]+',value)
    scores=[subject_score(value,name),subject_score(value,short)]
    for size in range(1,min(5,len(tokens))+1):
        prefix=' '.join(tokens[:size])
        score=max(subject_score(prefix,name),subject_score(prefix,short))
        if size<len(tokens):
            score*=.85 if size==1 else .97
        # Match damaged abbreviated words against the starts of full words.
        target_words=[normalized(t) for t in re.findall(r'[\w]+',name)]
        position=0;similarities=[]
        for word in tokens[:size]:
            needle=normalized(word)
            matches=[(SequenceMatcher(None,needle,t[:max(3,len(needle))]).ratio(),i)
                for i,t in enumerate(target_words) if i>=position and len(needle)>=3]
            best=max(matches,default=(0,0))
            if best[0]<.65:
                break
            similarities.append(best[0]);position=best[1]+1
        else:
            if similarities:
                score=max(score,(.65+.24*sum(similarities)/len(similarities))*(.9 if size==1 and len(tokens)>1 else 1))
        scores.append(score)
    return max(scores,default=0)


def infer_row(group_id,day,row,curriculum=None,base_lessons=None):
    """Rank review proposals in the group's dictionary; never invent catalog records."""
    result={'subject_id':None,'teacher_id':None,'room_id':None,'subgroup_id':None,
            'inferred':False,'confidence':0,'inferences':[]}
    group=StudentGroup.objects.filter(pk=group_id).first() if group_id else None
    if group is None:
        result['inferences']=['Группа не найдена в загруженном расписании']
        result['inferred']=True
        return result
    data=curriculum if curriculum is not None else curriculum_for(group,day)
    texts=list(dict.fromkeys([row.get('raw',''),*row.get('raw_variants',[])]))
    subject_texts=[row.get('subject_text','')]+[d['subject_text'] for text in texts if (d:=decode_cell(text))]
    teacher_texts=[row.get('teacher_text','')]+texts
    base_lessons=base_lessons if base_lessons is not None else svc.base_schedule(day)
    originals=[l for l in base_lessons if l['slot']==row.get('slot') and any(g['id']==group.pk for g in l['groups'])]
    if row.get('subgroup_number'):
        subgroup=next((s for s in data['subgroups'] if normalized(s['name'])==str(row['subgroup_number'])),None)
        result['subgroup_id']=subgroup['id'] if subgroup else None
        if subgroup:
            originals=[l for l in originals if any(g['id']==group.pk and g['subgroup_id'] in [None,subgroup['id']] for g in l['groups'])]
    elif len(originals)==1:
        participant=next(g for g in originals[0]['groups'] if g['id']==group.pk)
        result['subgroup_id']=participant['subgroup_id']
    teacher_ranks=sorted([(max((teacher_score(text,t['name']) for text in teacher_texts),default=0),t) for t in data['teachers']],key=lambda x:(-x[0],x[1]['id']))
    recognizable_teacher=teacher_ranks[0][1]['id'] if teacher_ranks and teacher_ranks[0][0]>=.65 else None
    subjects=[]
    for subject in data['subjects']:
        numbered=re.search(r'п/г\s*(\d)',subject['name'])
        if numbered and row.get('subgroup_number') and int(numbered[1])!=row['subgroup_number']:
            continue
        score=max((abbreviation_score(text,subject['name'],subject['short_name']) for text in subject_texts),default=0)
        if recognizable_teacher and any(t['id']==recognizable_teacher for t in subject['teachers']):
            score+=.12
        if any(l['subject_id']==subject['id'] for l in originals):
            score+=.03
        if numbered and not row.get('subgroup_number'):
            score-=.02
        subjects.append((min(1,score),subject))
    subjects.sort(key=lambda x:(-x[0],x[1]['id']))
    if subjects and subjects[0][0]<.65 and teacher_ranks and teacher_ranks[0][0]>=.78:
        related=[(max(.5,score),s) for score,s in subjects if any(t['id']==recognizable_teacher for t in s['teachers'])]
        if related:
            subjects=related
            result['inferences'].append('Дисциплина: по распознанному преподавателю группы')
    scores=[]
    if subjects:
        score,subject=subjects[0];result['subject_id']=subject['id']
        ambiguous=len(subjects)>1 and score-subjects[1][0]<.08
        scores.append(score*.75 if ambiguous else score)
        if score<.85 or ambiguous:
            result['inferences'].append('Дисциплина: ближайший вариант в расписании группы')
        related={t['id'] for t in subject['teachers']}
        teacher_ranks.sort(key=lambda x:(-(x[0]+(.18 if x[1]['id'] in related else 0)),x[1]['id']))
        if teacher_ranks:
            teacher_value,teacher=teacher_ranks[0]
            if teacher_value<.65:
                teacher=next((t for t in subject['teachers'] if any(l['teacher_id']==t['id'] for l in originals)),None) or (subject['teachers'][0] if subject['teachers'] else teacher)
                teacher_value=.55
                result['inferences'].append('Преподаватель: по выбранной дисциплине группы')
            result['teacher_id']=teacher['id'];scores.append(teacher_value)
    rooms=list(Room.objects.select_related('building'))
    room_texts=[row.get('room_text','')]+[d['room_text'] for text in texts if (d:=decode_cell(text))]
    room_ranks=[]
    for room in rooms:
        score=max((SequenceMatcher(None,normalized(text),normalized(room.number)).ratio() for text in room_texts if text),default=0)
        if room.building_id==group.building_id:
            score+=.01
        room_ranks.append((score,room))
    room_ranks.sort(key=lambda x:(-x[0],x[1].pk))
    if room_ranks and room_ranks[0][0]>=.7:
        score,room=room_ranks[0];result['room_id']=room.pk;scores.append(min(1,score))
        if score<.95:
            result['inferences'].append('Кабинет: ближайшее обозначение из справочника')
    else:
        # Omitted rooms first inherit the original pair, then the subject's usual room.
        room_ids={l['room_id'] for l in originals}
        if len(room_ids)!=1 and data['version'] and result['subject_id']:
            room_ids=set(Lesson.objects.filter(version_id=data['version']['id'],participants__group=group,
                subject_id=result['subject_id'],teacher_id=result['teacher_id']).values_list('room_id',flat=True))
        if room_ids:
            result['room_id']=sorted(room_ids)[0];scores.append(.5)
            result['inferences'].append('Кабинет: из расписания, на изображении не разобран')
    if not data['subjects']:
        result['inferences'].append('У группы нет списка дисциплин на этот период')
    result['inferred']=bool(result['inferences'])
    result['confidence']=round(min(scores,default=0)*100)
    return result


def decode_cell(raw):
    raw=raw.strip()
    # Dashes mean no scheduled pair; they never turn into cancellations.
    clean=normalized(raw)
    marker=clean.translate(str.maketrans({'л':'п','з':'а'}))
    if not clean or clean in ['по','ло'] or re.match(r'^[пл][оа]р[ао]с',clean) or marker.startswith('порасп') or SequenceMatcher(None,clean,'порасп').ratio()>.65:
        return None
    cleaned=raw.replace('=','п')
    cleaned=re.sub(r'(?<![\w.])[0-9ЗзОоlI]{2,3}(?:[пnб][0-9б]?)?(?![\w.])',
        lambda m:m[0].translate(str.maketrans({'З':'3','з':'3','О':'0','о':'0','l':'1','I':'1','n':'п'})),cleaned)
    room_matches=list(re.finditer(r'(?<![\w.])(?:\d{2,3}[а-яa-z]?\d?|[сc][.\- ]?зал\w*)(?![\w.])',cleaned,re.I))
    room_match=room_matches[-1] if room_matches else None
    room=room_match[0].strip() if room_match else ''
    content=(cleaned[:room_match.start()]+' '+cleaned[room_match.end():]).strip() if room_match else cleaned
    if not normalized(content):
        return None
    content=content.translate(str.maketrans('ABCEHKMOPTXYaceopxy','АВСЕНКМОРТХУасеорху'))
    teacher_match=re.search(r'([А-ЯЁ][а-яё-]+)\s*(?:([А-ЯЁа-яё])\s*[.,_]\s*([А-ЯЁа-яё])\s*[.,_]?|([А-ЯЁ])\s*([А-ЯЁ])(?![а-яё]))',content)
    teacher=(f'{teacher_match[1]} {(teacher_match[2] or teacher_match[4]).upper()}.{(teacher_match[3] or teacher_match[5]).upper()}.') if teacher_match else ''
    subject=content[:teacher_match.start()].strip() if teacher_match else content
    return {'raw':raw,'subject_text':subject,'teacher_text':teacher,'room_text':room,
            'subject_id':None,'teacher_id':None,'room_id':None}


def yellow_fraction(image,box):
    crop=image.crop(tuple(map(int,box)))
    if not crop.width or not crop.height:
        return 0
    pixels=list(crop.getdata())
    return sum(r>175 and g>160 and r>b+15 and g>b+15 for r,g,b in pixels)/len(pixels)


def recognize_boxes(image,boxes):
    """An enlarged contact sheet preserves tiny cell text better than shrinking a wide table."""
    unique=list(dict.fromkeys(boxes));recognized={}
    for offset in range(0,len(unique),24):
        batch=unique[offset:offset+24]
        atlas=Image.new('RGB',(1800,280*((len(batch)+2)//3)),'white')
        positions=[]
        for i,box in enumerate(batch):
            crop=image.crop(tuple(map(int,box)))
            factor=min(5,560/max(1,crop.width),250/max(1,crop.height))
            crop=crop.resize((max(1,round(crop.width*factor)),max(1,round(crop.height*factor))),Image.Resampling.LANCZOS)
            crop=ImageOps.autocontrast(ImageOps.grayscale(crop)).filter(ImageFilter.UnsharpMask(radius=1,percent=130,threshold=3))
            x=(i%3)*600+18;y=(i//3)*280+14
            atlas.paste(crop,(x,y));positions.append((x,y,x+crop.width,y+crop.height))
        words=run_ocr(atlas)
        recognized.update((source,in_box(words,box)) for source,box in zip(batch,positions))
    return [recognized[box] for box in boxes]


def split_cell(image,box):
    """A partial vertical rule splits a pair into subgroup 1 and subgroup 2."""
    left,top,right,bottom=map(int,box)
    gray=image.convert('L');pixels=gray.load()
    edges=bands([x for x in range(left+8,right-8)
        if sum(pixels[x,y]<145 for y in range(top,bottom))>max(1,bottom-top)*.85])
    edge=next((x for x in edges if .3<(x-left)/(right-left)<.7),None)
    return [(left,top,edge-1,bottom),(edge+1,top,right,bottom)] if edge else [box]


def cell_ink(image,box):
    crop=image.crop(tuple(map(int,box))).convert('L')
    # A dash has very little ink; unreadable printed text still deserves a review row.
    return sum(value<140 for value in crop.getdata())>max(16,crop.width*crop.height*.025)


def analyze(image,layout,target_date=None):
    from django.utils import timezone
    target_date=target_date or timezone.localdate()
    xs,ys=table_edges(image)
    # Remove the long grid rules before OCR so they do not join adjacent letters.
    prepared=image.copy()
    from PIL import ImageDraw
    draw=ImageDraw.Draw(prepared)
    for x in xs:draw.line((x,ys[0],x,ys[-1]),fill='white',width=2)
    for y in ys:draw.line((xs[0],y,xs[-1],y),fill='white',width=2)
    words=run_ocr(prepared)
    rows=[];warnings=[];pending=[]
    if layout=='table':
        # The first column is the group; the next seven columns are pairs.
        if len(xs)<9:
            raise ValidationError('В таблице замен нужны группа и семь пар')
        for top,bottom in zip(ys[1:],ys[2:]):
            group_text=in_box(words,(xs[0],top,xs[1],bottom))
            for slot in range(1,min(8,len(xs)-1)):
                whole=in_box(words,(xs[slot],top,xs[slot+1],bottom))
                box=(xs[slot]+2,top+2,xs[slot+1]-2,bottom-2)
                cells=split_cell(image,box)
                if len(cells)==1 and whole and decode_cell(whole) is None:
                    continue
                for index,cell in enumerate(cells,1):
                    raw=in_box(words,cell if len(cells)>1 else (xs[slot],top,xs[slot+1],bottom))
                    if decode_cell(raw) or (not raw and cell_ink(image,cell)):
                        pending.append({'group_box':(xs[0]+2,top+2,xs[1]-2,min(bottom-2,top+17)),
                            'cell_box':cell,'raw':raw,'group_text':group_text,'slot':slot,
                            'subgroup_number':index if len(cells)>1 else None})
    else:
        # Locate the row of group names, then use their horizontal positions as block anchors.
        header_words=[w for w in words if re.search(r'-[0-9l]+-[0-9Зз]{2}',w['text'])]
        # Small screenshots often lose hyphens. Geometry still identifies group blocks and pairs.
        if len(ys)<9:
            raise ValidationError('Не видны заголовки и семь строк пар')
        group_top,group_bottom=ys[1],ys[2]
        # The thin line above subgroup numbers is lighter than the main row rules.
        gray=image.convert('L');pixels=gray.load()
        header_rules=bands([y for y in range(group_top+3,group_bottom-2)
            if sum(pixels[x,y]<185 for x in range(image.width))>image.width*.85])
        if header_rules:
            group_bottom=header_rules[0]
        widths=[b-a for a,b in zip(xs,xs[1:]) if image.width*.04<b-a<image.width*.18]
        if not widths:
            raise ValidationError('Не удалось определить ширину блоков групп')
        step=sorted(widths)[len(widths)//2]
        start=next((x for x in xs if x>=image.width*.055),xs[2])
        starts=list(range(start,xs[-1]-step//2,step))
        for slot,(top,bottom) in enumerate(zip(ys[2:],ys[3:]),1):
            if slot>7:
                break
            for left in starts:
                right=min(left+step,xs[-1])
                group_text=in_box(words,(left,group_top,right,group_bottom))
                box=(left+2,top+2,right-2,bottom-2)
                if yellow_fraction(image,box)<.08:
                    continue
                raw=in_box(words,box)
                pending.append({'group_box':(left+2,group_top+2,right-2,group_bottom-2),
                    'cell_box':box,'raw':raw,'group_text':group_text,'slot':slot})
    if pending:
        refined=recognize_boxes(image,[box for row in pending for box in [row['group_box'],row['cell_box']]])
        dictionaries={};base=svc.base_schedule(target_date)
        for i,row in enumerate(pending):
            group_text=refined[i*2] or row['group_text']
            group_id=suggestion(StudentGroup,group_text) or suggestion(StudentGroup,row['group_text'])
            raw=refined[i*2+1] or row['raw']
            item=decode_cell(raw) or decode_cell(row['raw'])
            if item is None and layout=='table' and (raw or row['raw']):
                continue
            item=item or {'raw':raw,'subject_text':'','teacher_text':'','room_text':'','subject_id':None,'teacher_id':None,'room_id':None}
            first=decode_cell(row['raw']) or {}
            for field in ['subject','teacher','room']:
                if not item.get(field+'_text') and first.get(field+'_text'):
                    item[field+'_text']=first[field+'_text']
            item.update(slot=row['slot'],subgroup_number=row.get('subgroup_number'),raw_variants=[row['raw'],raw])
            if group_id not in dictionaries:
                group=StudentGroup.objects.filter(pk=group_id).first()
                dictionaries[group_id]=curriculum_for(group,target_date) if group else None
            item.update(infer_row(group_id,target_date,item,dictionaries[group_id],base))
            if group_id and normalized(group_text)!=normalized(dictionaries[group_id]['group']['name']):
                item['inferred']=True;item['confidence']=min(item['confidence'],80)
                item['inferences'].insert(0,'Группа: исправлено повреждённое обозначение')
            rows.append({**item,'group_text':group_text,'group_id':group_id,'kind':'replace'})
    if not rows:
        warnings.append('Изменения не распознаны. Добавьте строки вручную или загрузите изображение чётче.')
    warnings.append('Проверьте строки по оригиналу. Поля с пометкой «Подобрано» заполнены по расписанию группы.')
    return rows,warnings


def upload_image(upload,target_date,layout,user):
    image=image_bytes(upload)
    rows=[];warnings=[]
    try:
        rows,warnings=analyze(image,layout,target_date)
    except (ValidationError,subprocess.TimeoutExpired,OSError,json.JSONDecodeError) as exc:
        warnings.append(str(exc))
    batch=ImageImport(target_date=target_date,layout=layout,payload=rows,warnings=warnings,uploaded_by=user)
    from django.core.files.base import ContentFile
    output=io.BytesIO();image.save(output,format='PNG')
    batch.image.save('changes.png',ContentFile(output.getvalue()),save=False);batch.save()
    svc.audit(user,'preview_image',batch,new={'rows':len(rows)})
    return batch


@transaction.atomic
def publish_review(batch,rows,user):
    svc.lock_schedule()
    batch=ImageImport.objects.select_for_update().get(pk=batch.pk)
    if batch.status!='review':
        raise ValidationError('Эта загрузка уже обработана')
    created=[]
    for row in rows:
        from .timing import require_teaching_slot
        require_teaching_slot(batch.target_date,row['slot'])
        group=StudentGroup.objects.get(pk=row['group_id'])
        svc.require_buildings(user,[group.building_id])
        if not GroupYear.objects.filter(group=group,year__start_date__lte=batch.target_date,year__end_date__gte=batch.target_date,status='active').exists():
            raise ValidationError(f'{group}: группа не активна на эту дату')
        if PracticePeriod.objects.filter(group=group,is_active=True,start_date__lte=batch.target_date,end_date__gte=batch.target_date).exists():
            raise ValidationError(f'{group}: на эту дату назначена практика')
        subgroup=Subgroup.objects.get(pk=row['subgroup_id']) if row.get('subgroup_id') else None
        if subgroup and subgroup.group_id!=group.pk:
            raise ValidationError('Подгруппа принадлежит другой группе')
        matches=[l for l in svc.base_schedule(batch.target_date) if l['slot']==row['slot'] and any(g['id']==group.pk and (not subgroup or not g['subgroup_id'] or g['subgroup_id']==subgroup.pk) for g in l['groups'])]
        if len(matches)>1:
            raise ValidationError(f'{group}, {row["slot"]} пара: уточните подгруппу')
        slot=TimeSlot.objects.get(sort_order=row['slot'])
        if row['kind']=='cancel' and not matches:
            raise ValidationError(f'{group}: нечего отменять на {row["slot"]} паре')
        change=Change.objects.create(target_date=batch.target_date,original_id=matches[0]['id'] if matches else None,
            kind='cancel' if row['kind']=='cancel' else 'replace' if matches else 'add',time_slot=slot,
            subject_id=row.get('subject_id'),teacher_id=row.get('teacher_id'),room_id=row.get('room_id'),
            group=group,target_group=group if matches else None,target_subgroup=subgroup if matches else None,subgroup=subgroup,created_by=user)
        change.full_clean();svc.require_buildings(user,svc.change_buildings(change))
        created.append(change)
    if not created:
        raise ValidationError('Выберите хотя бы одну проверенную строку')
    # Transaction isolation keeps these unpublished externally until the entire batch passes.
    from django.utils import timezone
    published_at=timezone.now()
    for change in created:
        change.status='published';change.published_at=published_at;change.save()
    conflicts=svc.conflicts_for(batch.target_date,svc.schedule_on(batch.target_date))
    if conflicts:
        raise ValidationError(conflicts[0]['type'])
    for change in created:
        svc.audit(user,'publish_change',change)
    batch.status='published';batch.payload=rows;batch.save()
    svc.audit(user,'publish_image',batch,new={'changes':[x.pk for x in created]})
    return created
