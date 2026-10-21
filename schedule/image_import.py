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
from PIL import Image,ImageOps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import ImageImport,StudentGroup,Teacher,Subject,Room,TimeSlot,Change,Subgroup,GroupYear
from .college_import import teacher_name
from . import services as svc

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
    enlarged=image.resize((round(image.width*factor),round(image.height*factor)))
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
    return ' '.join(' '.join(w['text'] for w in sorted(line[1],key=lambda w:w['x'])) for line in lines)


def normalized(value):
    value=value.casefold().replace('ё','е').translate(str.maketrans({'a':'а','c':'с','e':'е','o':'о','p':'р','x':'х','y':'у','k':'к','m':'м','t':'т','b':'в'}))
    return re.sub(r'[^а-яa-z0-9]','',value).replace('2з','23')


def suggestion(model,value):
    if not value:
        return None
    needle=normalized(value)
    group_suffix=None
    if model==StudentGroup:
        candidate=value.replace('IIC','ИС').replace('IIС','ИС')
        match=re.search(r'(.+?)[- ]([0-9lIЗзsS]+)[- ]([0-9ЗзS]+)$',candidate)
        if not match:
            return None
        fix=lambda s:s.translate(str.maketrans({'l':'1','I':'1','З':'3','з':'3','s':'5','S':'5'}))
        group_suffix=(fix(match[2]),fix(match[3]))
        needle=normalized(match[1]+''.join(group_suffix))
    scored=[]
    for record in model.objects.all():
        name=record.number if model==Room else record.name
        if model==StudentGroup and tuple(name.split('-')[-2:])!=group_suffix:
            continue
        target=normalized(name)
        score=SequenceMatcher(None,needle,target).ratio()
        if model==Subject and (target.startswith(needle) or needle.startswith(target)):
            score=max(score,.93)
        scored.append((score,record.pk))
    scored.sort(reverse=True)
    if not scored or scored[0][0]<.74 or (len(scored)>1 and scored[0][0]-scored[1][0]<.08):
        return None
    return scored[0][1]


def decode_cell(raw):
    raw=raw.strip()
    # Dashes mean no scheduled pair; they never turn into cancellations.
    clean=normalized(raw)
    if not clean or clean in ['по','ло'] or re.match(r'^[пл][оа]р[ао]с',clean) or SequenceMatcher(None,clean,'порасп').ratio()>.65:
        return None
    room_match=re.search(r'(?:\b\d{2,3}[а-яa-z]?\d?\b|[сc][.\- ]?зал\w*)\s*$',raw,re.I)
    room=room_match[0].strip() if room_match else ''
    content=raw[:room_match.start()].strip() if room_match else raw
    teacher_match=re.search(r'([А-ЯЁ][а-яё-]+)\s+(?:([А-ЯЁа-яё])\s*[.,_]\s*([А-ЯЁа-яё])\s*[.,_]?|([А-ЯЁ])\s*([А-ЯЁ])(?![а-яё]))',content)
    teacher=(f'{teacher_match[1]} {(teacher_match[2] or teacher_match[4]).upper()}.{(teacher_match[3] or teacher_match[5]).upper()}.') if teacher_match else ''
    subject=content[:teacher_match.start()].strip() if teacher_match else content
    return {'raw':raw,'subject_text':subject,'teacher_text':teacher,'room_text':room,
            'subject_id':suggestion(Subject,subject),'teacher_id':suggestion(Teacher,teacher),'room_id':suggestion(Room,room)}


def yellow_fraction(image,box):
    crop=image.crop(tuple(map(int,box)))
    if not crop.width or not crop.height:
        return 0
    pixels=list(crop.getdata())
    return sum(r>175 and g>160 and r>b+15 and g>b+15 for r,g,b in pixels)/len(pixels)


def recognize_boxes(image,boxes):
    """An enlarged contact sheet preserves tiny cell text better than shrinking a wide table."""
    result=[]
    for offset in range(0,len(boxes),32):
        batch=boxes[offset:offset+32]
        atlas=Image.new('RGB',(1920,240*((len(batch)+3)//4)),'white')
        positions=[]
        for i,box in enumerate(batch):
            crop=image.crop(tuple(map(int,box)))
            factor=min(5,450/max(1,crop.width),210/max(1,crop.height))
            crop=crop.resize((max(1,round(crop.width*factor)),max(1,round(crop.height*factor))))
            x=(i%4)*480+10;y=(i//4)*240+10
            atlas.paste(crop,(x,y));positions.append((x,y,x+crop.width,y+crop.height))
        words=run_ocr(atlas)
        result.extend(in_box(words,box) for box in positions)
    return result


def analyze(image,layout):
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
                raw=in_box(words,(xs[slot],top,xs[slot+1],bottom))
                item=decode_cell(raw)
                if item:
                    pending.append({'group_box':(xs[0]+2,top+2,xs[1]-2,bottom-2),
                        'cell_box':(xs[slot]+2,top+2,xs[slot+1]-2,bottom-2),'raw':raw,'group_text':group_text,'slot':slot})
    else:
        # Locate the row of group names, then use their horizontal positions as block anchors.
        header_words=[w for w in words if re.search(r'-[0-9l]+-[0-9Зз]{2}',w['text'])]
        # Small screenshots often lose hyphens. Geometry still identifies group blocks and pairs.
        if len(ys)<9:
            raise ValidationError('Не видны заголовки и семь строк пар')
        group_top,group_bottom=ys[1],ys[2]
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
                item=decode_cell(raw) or {'raw':raw,'subject_text':'','teacher_text':'','room_text':'',
                    'subject_id':None,'teacher_id':None,'room_id':None}
                pending.append({'group_box':(left+2,group_top+2,right-2,group_bottom-2),
                    'cell_box':box,'raw':raw,'group_text':group_text,'slot':slot})
    if pending:
        refined=recognize_boxes(prepared,[box for row in pending for box in [row['group_box'],row['cell_box']]])
        for i,row in enumerate(pending):
            group_text=refined[i*2] or row['group_text']
            group_id=suggestion(StudentGroup,group_text) or suggestion(StudentGroup,row['group_text'])
            raw=refined[i*2+1] or row['raw']
            item=decode_cell(raw)
            if item is None and layout=='table':
                continue
            item=item or {'raw':raw,'subject_text':'','teacher_text':'','room_text':'','subject_id':None,'teacher_id':None,'room_id':None}
            first=decode_cell(row['raw']) or {}
            for field in ['subject','teacher','room']:
                if not item.get(field+'_id') and first.get(field+'_id'):
                    item[field+'_id']=first[field+'_id'];item[field+'_text']=first.get(field+'_text','')
            rows.append({**item,'group_text':group_text,'group_id':group_id,
                         'slot':row['slot'],'subgroup_id':None,'kind':'replace'})
    if not rows:
        warnings.append('Изменения не распознаны. Добавьте строки вручную или загрузите изображение чётче.')
    warnings.append('Проверьте каждую выбранную строку по оригиналу. Пустые поля не будут заполнены автоматически.')
    return rows,warnings


def upload_image(upload,target_date,layout,user):
    image=image_bytes(upload)
    rows=[];warnings=[]
    try:
        rows,warnings=analyze(image,layout)
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
        group=StudentGroup.objects.get(pk=row['group_id'])
        svc.require_buildings(user,[group.building_id])
        if not GroupYear.objects.filter(group=group,year__start_date__lte=batch.target_date,year__end_date__gte=batch.target_date,status='active').exists():
            raise ValidationError(f'{group}: группа не активна на эту дату')
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
            group=group,target_group=group if matches else None,subgroup=subgroup,created_by=user)
        change.full_clean();svc.require_buildings(user,svc.change_buildings(change))
        created.append(change)
    if not created:
        raise ValidationError('Выберите хотя бы одну проверенную строку')
    # Check the whole batch together, so a legitimate exchange of rooms can publish atomically.
    all_lessons=svc.schedule_on(batch.target_date)
    for change in created:
        base=next((l for l in all_lessons if l['id']==change.original_id),None) if change.original_id else None
        if base:
            from copy import deepcopy
            selected=deepcopy(base)
            selected['groups']=[g for g in base['groups'] if g['id']==change.target_group_id]
            base['groups']=[g for g in base['groups'] if g['id']!=change.target_group_id]
            if not base['groups']:all_lessons.remove(base)
            base=selected
        all_lessons.append(svc.apply_change(change,base))
    conflicts=svc.conflicts_for(batch.target_date,all_lessons)
    if conflicts:
        raise ValidationError(conflicts[0]['type'])
    from django.utils import timezone
    for change in created:
        change.status='published';change.published_at=timezone.now();change.save()
        svc.audit(user,'publish_change',change)
    batch.status='published';batch.payload=rows;batch.save()
    svc.audit(user,'publish_image',batch,new={'changes':[x.pk for x in created]})
    return created
