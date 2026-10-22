'use strict';

function cascadeBells(config) {
    const clock=value=>{
        if(!/^\d{2}:\d{2}$/.test(value||''))return null;
        const [h,m]=value.split(':').map(Number);return h<24&&m<60?h*60+m:null;
    };
    const format=value=>String(Math.floor(value/60)).padStart(2,'0')+':'+String(value%60).padStart(2,'0');
    const duration=Number(config.duration),pause=Number(config.pause),from=config.from;
    if(!Number.isInteger(duration)||duration<1||duration>180)throw new Error('Длительность пары — от 1 до 180 минут');
    if(config.pause===''||!Number.isInteger(pause)||pause<0||pause>720)throw new Error('Укажите длительность перемены');
    const rows=config.rows.map(row=>({...row}));
    if(!Number.isInteger(from)||from<0||from>=rows.length)throw new Error('Выберите пару');
    const gap=row=>{
        const value=row.breakAfter===''||row.breakAfter==null?pause:Number(row.breakAfter);
        if(!Number.isInteger(value)||value<0||value>720)throw new Error('Перемена — от 0 до 720 минут');
        return value;
    };
    // A new start defines the break before this pair, without moving earlier pairs.
    if(config.field==='start'&&from>0) {
        const start=clock(rows[from].start),previous=clock(rows[from-1].end);
        if(start===null||previous===null)throw new Error('Укажите время пары');
        if(start<previous)throw new Error('Начало пары пересекается с предыдущей');
        rows[from-1].breakAfter=start-previous;
    }
    let first=config.field==='break'?from+1:from;
    for(let i=first;i<rows.length;i++) {
        const previous=i>from||config.field==='break'?clock(rows[i-1].end):null;
        if((i>from||config.field==='break')&&previous===null)throw new Error('Укажите окончание предыдущей пары');
        const start=i===from?clock(rows[i].start):previous+gap(rows[i-1]);
        if(start===null)throw new Error('Укажите начало пары');
        const end=i===from&&config.field==='end'?clock(rows[i].end):start+duration;
        if(end===null||end<=start)throw new Error('Окончание должно быть позже начала');
        if(start<0||end>=1440)throw new Error('Звонки должны закончиться в тот же день');
        rows[i].start=format(start);rows[i].end=format(end);
    }
    return rows;
}

function initBellEditor(form) {
    const rows=[...form.querySelectorAll('#weekday-bell-table tbody tr')];
    const auto=form.querySelector('[data-bell-auto]'),duration=form.querySelector('[data-bell-duration]');
    const pause=form.querySelector('[data-bell-pause]'),status=form.querySelector('[data-bell-status]');
    const field=(row,name)=>row.querySelector(`[data-bell-${name}]`);
    const read=()=>rows.map(row=>({start:field(row,'start').value,end:field(row,'end').value,
        breakAfter:field(row,'break')?.value??''}));
    const update=(from,name)=>{
        status.textContent='';
        if(!auto.checked)return;
        try {
            const result=cascadeBells({rows:read(),from,field:name,duration:duration.value,pause:pause.value});
            result.forEach((pair,i)=>{
                if(i>=from&&(name!=='break'||i>from)) {
                    field(rows[i],'start').value=pair.start;field(rows[i],'end').value=pair.end;
                    rows[i].querySelector('input[type=checkbox]').checked=true;
                }
                const gap=field(rows[i],'break');
                if(gap)gap.value=pair.breakAfter;
            });
        }catch(error){status.textContent=error.message;}
    };
    rows.forEach((row,index)=>{
        for(const name of ['start','end','break']) {
            const input=field(row,name);if(!input)continue;
            input.addEventListener('input',()=>{
                if(name!=='break')row.querySelector('input[type=checkbox]').checked=true;
                update(index,name);
            });
        }
    });
    for(const input of [duration,pause])input.addEventListener('input',()=>{
        rows.forEach(row=>{const gap=field(row,'break');if(gap)gap.placeholder=pause.value;});
        if(rows.length)update(0,'start');
    });
}

if(typeof module!=='undefined')module.exports={cascadeBells,initBellEditor};
if(typeof document!=='undefined')document.querySelectorAll('#weekday-bell-form').forEach(initBellEditor);
