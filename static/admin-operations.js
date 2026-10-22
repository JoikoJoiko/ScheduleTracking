'use strict';
const keep = document.querySelector('#id_keep_breaks'), pause = document.querySelector('#id_break_minutes');
if (keep && pause) {
    const sync = () => { pause.disabled = keep.checked; pause.required = !keep.checked; };
    keep.addEventListener('change', sync); sync();
}
const shortForm=document.querySelector('#short-day-form');
if(shortForm) {
    const data=JSON.parse(document.querySelector('#short-day-base').textContent);
    const rows=[...shortForm.querySelectorAll('#short-bell-table tbody tr')];
    const status=document.querySelector('#short-preview-status');
    const update=()=>{
        const count=Number(shortForm.elements.lesson_count.value)||7;
        const date=new Date(shortForm.elements.date.value+'T12:00:00');
        const weekday=(date.getDay()+6)%7,regular=data.find(day=>day.weekday===weekday)?.slots||[];
        const visible=rows.filter(row=>Number(row.dataset.number)<=count);
        rows.forEach(row=>{
            const active=visible.includes(row),last=row===visible.at(-1);
            row.hidden=!active;
            row.querySelector('input[name$="-lesson_minutes"]').disabled=!active;
            const gap=row.querySelector('input[name$="-break_minutes"]');gap.disabled=!active||last;gap.hidden=last;
            row.querySelector('.last-pair-note').hidden=!last;
            row.querySelector('.short-pair-time').textContent='—';
        });
        try {
            if(!shortForm.elements.date.value)throw new Error('Укажите дату');
            if(weekday===6)throw new Error('Выберите день с понедельника по субботу');
            const preview=calculateShortDay({lessonCount:shortForm.elements.lesson_count.value,
                lessonMinutes:shortForm.elements.lesson_minutes.value,start:shortForm.elements.start_time.value,
                keepBreaks:shortForm.elements.keep_breaks.checked,breakMinutes:shortForm.elements.break_minutes.value,regular,
                rows:rows.map(row=>({number:Number(row.dataset.number),
                    lessonMinutes:row.querySelector('input[name$="-lesson_minutes"]').value,
                    breakMinutes:row.querySelector('input[name$="-break_minutes"]').value}))});
            preview.forEach(pair=>{
                const row=rows.find(row=>Number(row.dataset.number)===pair.number);
                row.querySelector('.short-pair-time').textContent=pair.start+'–'+pair.end;
                row.querySelector('input[name$="-lesson_minutes"]').placeholder=shortForm.elements.lesson_minutes.value||'Общая';
                row.querySelector('input[name$="-break_minutes"]').placeholder=String(pair.breakAfter);
            });
            status.textContent='';
        }catch(error){status.textContent=error.message;}
    };
    shortForm.addEventListener('input',update);shortForm.addEventListener('change',update);update();
}
