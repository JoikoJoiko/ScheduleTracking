'use strict';
// Mirror the server's duration and break rules for a preview without submitting the form.
function calculateShortDay(config) {
    const number=value=>value===''||value==null?null:Number(value);
    const clock=value=>{
        if(!/^\d{2}:\d{2}$/.test(value||''))return null;
        const [h,m]=value.split(':').map(Number);return h<24&&m<60?h*60+m:null;
    };
    const format=value=>String(Math.floor(value/60)).padStart(2,'0')+':'+String(value%60).padStart(2,'0');
    const count=number(config.lessonCount);
    if(count!==null&&(!Number.isInteger(count)||count<1||count>7))throw new Error('Укажите от 1 до 7 пар');
    const rows=config.rows.filter(row=>count===null||row.number<=count);
    const regular=new Map(config.regular.map(row=>[row.number,row]));
    let cursor=config.start?clock(config.start):clock(regular.get(rows[0]?.number)?.start);
    if(cursor===null)throw new Error('Укажите начало первой пары');
    const result=[];
    for(const [i,row] of rows.entries()) {
        const duration=number(row.lessonMinutes)??number(config.lessonMinutes);
        if(!Number.isInteger(duration)||duration<1||duration>180)throw new Error('Укажите длительность пар');
        const end=cursor+duration;
        if(end>=1440)throw new Error('Звонки должны закончиться в тот же день');
        let pause=0;
        if(i<rows.length-1) {
            pause=number(row.breakMinutes);
            if(pause===null) {
                const next=clock(regular.get(rows[i+1].number)?.start),previous=clock(regular.get(row.number)?.end);
                pause=config.keepBreaks?(next!==null&&previous!==null?Math.max(0,next-previous):null):number(config.breakMinutes);
            }
            if(!Number.isInteger(pause)||pause<0||pause>720)throw new Error('Укажите длительность перемен');
        }
        result.push({number:row.number,start:format(cursor),end:format(end),duration,breakAfter:pause});
        cursor=end+pause;
    }
    return result;
}
if(typeof module!=='undefined')module.exports={calculateShortDay};
