'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const {calculateShortDay}=require('../static/short-day.js');

function page() {
    const events={},requests=[],history=[],intervals=[];
    const search={value:'',matches:()=>false};
    const document={hidden:false,activeElement:{matches:()=>false},
        querySelector:selector=>selector==='#search'?search:selector==='#app'?{innerHTML:''}:null,
        querySelectorAll:()=>[],addEventListener:(name,handler)=>{events[name]=handler;}};
    const location={pathname:'/groups',search:'',href:'http://localhost/groups'};
    const navigate=(_,__,url)=>{history.push(url);const parts=url.split('?');location.pathname=parts[0];location.search=parts[1]?'?'+parts[1]:'';};
    const context=vm.createContext({document,location,history:{replaceState:navigate,pushState:navigate},
        URLSearchParams,Intl,Date,Event,FormData:class {constructor(form){this.form=form;}get(key){return this.form[key];}},
        fetch:url=>{requests.push(url);return new Promise(()=>{});},
        window:{addEventListener:()=>{}},localStorage:{getItem:()=>null},
        setInterval:callback=>intervals.push(callback),setTimeout:()=>{}});
    vm.runInContext(source,context);
    return {events,requests,history,search,intervals,context,document};
}

test('typing a group does not navigate or replace the input before submission',()=>{
    const p=page(),before=p.requests.length;
    for(const value of ['И','ИС','ИС-1-23']) {
        p.search.value=value;
        p.events.input?.({target:{id:'search',value}});
    }
    assert.equal(p.requests.length,before);assert.equal(p.history.length,0);
    let prevented=false;
    p.events.submit({target:{id:'search-form',q:'ИС-1-23'},preventDefault:()=>{prevented=true;}});
    assert.ok(prevented);assert.equal(new URLSearchParams(p.history.at(-1).split('?')[1]).get('q'),'ИС-1-23');
    assert.equal(p.requests.length,before+1);
});

test('minute refresh preserves an unfinished search after focus moves away',()=>{
    const p=page(),before=p.requests.length;
    p.search.value='ИС-';p.intervals[0]();assert.equal(p.requests.length,before);
    p.search.value='';p.intervals[0]();assert.equal(p.requests.length,before+1);
});

test('arrows skip Sunday in both directions',()=>{
    const p=page();
    assert.equal(vm.runInContext("teachingDate('2026-10-25',1)",p.context),'2026-10-26');
    assert.equal(vm.runInContext("teachingDate('2026-10-25',-1)",p.context),'2026-10-24');
    assert.equal(vm.runInContext('weekdays.length',p.context),6);
});

test('teacher windows use vacant pairs while room availability retains breaks',()=>{
    const p=page();
    vm.runInContext("var day={date:'2026-10-19',has_data:true,lessons:[],windows:[],free_intervals:[{start:'10:00',end:'10:10'}]}",p.context);
    assert.doesNotMatch(vm.runInContext('dayPanel(day,true)',p.context),/Окна:/);
    assert.match(vm.runInContext('dayPanel(day,true,true)',p.context),/Свободно: 10:00–10:10/);
});

test('practice and subgroup cards carry their own visible labels',()=>{
    const p=page();
    const practice=vm.runInContext("dayPanel({date:'2026-10-19',lessons:[],practice:{label:'Практика до 26.10.2026',organization:''}})",p.context);
    assert.match(practice,/Практика до 26\.10\.2026/);assert.doesNotMatch(practice,/Занятий нет/);
    const subgroup=vm.runInContext("lessonCard({date:'2026-10-19',status:'regular',slot:1,subject:'Базы данных',groups:[{id:1,name:'ИС-1-23',subgroup:'2'}]})",p.context);
    assert.match(subgroup,/subgroup-lesson/);assert.match(subgroup,/Подгруппа 2/);
});

test('curator hours have their alias and shift bells without a numbered pair',()=>{
    const p=page();
    const card=vm.runInContext("lessonCard({date:'2026-10-26',status:'regular',slot:0,start:'08:20',end:'08:55',subject:'Разговоры о важном',event_kind:'class_hour',groups:[]})",p.context);
    assert.match(card,/Кураторский час/);assert.doesNotMatch(card,/0 пара/);
    vm.runInContext("meta={today:'2026-10-26',now:'2026-10-26T08:25:00',slots:[{number:1,start:'09:00',end:'10:30'}],week:{label:'1-я неделя'},curator_bells:[{shift:1,start:'08:20',end:'08:55'},{shift:2,start:'14:15',end:'14:45'}]}; var result=''; app.innerHTML=''; Object.defineProperty(app,'innerHTML',{set:v=>result=v}); bells()",p.context);
    const html=vm.runInContext('result',p.context);
    assert.match(html,/Кураторский час/);assert.match(html,/1-я смена/);assert.match(html,/14:15–14:45/);
    assert.match(html,/09:00/);assert.doesNotMatch(html,/0 ПАРА/);
});

const shortScheme=()=>({lessonCount:3,lessonMinutes:40,start:'08:00',keepBreaks:true,breakMinutes:'',
    regular:[{number:1,start:'08:00',end:'09:30'},{number:2,start:'09:40',end:'11:10'},{number:3,start:'11:40',end:'13:10'}],
    rows:[{number:1,lessonMinutes:'',breakMinutes:5},{number:2,lessonMinutes:'',breakMinutes:25},
          {number:3,lessonMinutes:30,breakMinutes:''}]});

test('short day preview respects count, per-pair duration and individual long break',()=>{
    const config=shortScheme(),preview=calculateShortDay(config);
    assert.deepEqual(preview.map(row=>[row.start,row.end]),[['08:00','08:40'],['08:45','09:25'],['09:50','10:20']]);
    config.lessonCount=2;
    assert.equal(calculateShortDay(config).length,2);
    assert.equal(calculateShortDay(config)[1].breakAfter,0);
});

test('zero-minute breaks and ordinary weekday breaks remain distinct',()=>{
    const config=shortScheme();config.rows[0].breakMinutes=0;config.rows[1].breakMinutes='';
    const rows=calculateShortDay(config);
    assert.equal(rows[1].start,'08:40');assert.equal(rows[2].start,'09:50');
    config.keepBreaks=false;config.breakMinutes=15;
    assert.equal(calculateShortDay(config)[2].start,'09:35');
});

test('short day preview rejects invalid parameters and midnight overflow',()=>{
    for(const patch of [{start:'23:30'},{lessonCount:8},{lessonCount:0},{lessonMinutes:0},{lessonMinutes:''}])
        assert.throws(()=>calculateShortDay({...shortScheme(),...patch}));
    const config=shortScheme();config.rows[0].breakMinutes=720;config.start='20:00';
    assert.throws(()=>calculateShortDay(config),/тот же день/);
});

test('public short-day caption uses the complete schedule label',()=>{
    const p=page();
    const html=vm.runInContext("dayPanel({date:'2026-10-19',has_data:true,lessons:[],shortened_day:{label:'Сокращённый день · 4 пары по 40 мин'}})",p.context);
    assert.match(html,/4 пары по 40 мин/);
});

function reviewPage(values) {
    const control=(value='')=>({value,checked:false,disabled:false,textContent:'',events:{},
        addEventListener(name,handler){this.events[name]=handler;}});
    const selectAll=control(),removeEmpty=control(),status=control(),events={};
    const controls={'[data-review-select-all]':selectAll,'[data-review-remove-empty]':removeEmpty,'[data-review-status]':status};
    const form={querySelector:selector=>controls[selector]};
    const rows=values.map((values,index)=>{
        const fields=Object.fromEntries(['use','DELETE','allow_room_overlap','group','subgroup','slot','kind','subject','teacher','room']
            .map(name=>[name,control(values[name] ?? (name==='kind'?'replace':''))]));
        fields.use.checked=!!values.checked;
        fields.allow_room_overlap.checked=!!values.allow_room_overlap;
        fields.group.name=`form-${index}-group`;
        const row={fields,dataset:{hasSource:values.source?'1':''},hidden:false,
            querySelector(selector){return selector==='.candidate-status'?control():fields[selector.match(/-([A-Za-z_]+)"/)[1]];}};
        Object.entries(fields).forEach(([name,field])=>{field.name=`form-${index}-${name}`;field.closest=()=>row;});
        return row;
    });
    const table={tBodies:[{rows}],closest:()=>form,addEventListener:(name,handler)=>{events[name]=handler;}};
    const document={querySelectorAll:selector=>selector==='.review-table'?[table]:rows};
    vm.runInNewContext(fs.readFileSync('static/admin-image-review.js','utf8'),{document});
    return {rows,selectAll,removeEmpty,status,events};
}

test('select all toggles populated and unresolved source rows without selecting blank extras',()=>{
    const p=reviewPage([{group:'1',slot:'1'},{},{source:true},{subject:'2'}]);
    p.selectAll.events.click();
    assert.deepEqual(p.rows.map(row=>row.fields.use.checked),[true,false,true,true]);
    assert.equal(p.selectAll.textContent,'Снять выделение');
    assert.match(p.status.textContent,/3 из 3/);
    p.selectAll.events.click();
    assert.ok(p.rows.every(row=>!row.fields.use.checked));
    assert.equal(p.selectAll.textContent,'Выбрать все');
});

test('removing empty rows preserves form indices, partial edits and unreadable image evidence',()=>{
    const p=reviewPage([{group:'1'}, {}, {source:true}, {slot:'2'}, {kind:'cancel'}, {DELETE:'on'}]);
    const names=p.rows.map(row=>row.fields.group.name);
    p.removeEmpty.events.click();
    assert.deepEqual(p.rows.map(row=>row.hidden),[false,true,false,false,false,true]);
    assert.equal(p.rows[1].fields.DELETE.value,'on');
    assert.deepEqual(p.rows.map(row=>row.fields.group.name),names);
    assert.equal(p.removeEmpty.disabled,true);
    p.selectAll.events.click();
    assert.deepEqual(p.rows.map(row=>row.fields.use.checked),[true,false,true,true,true,false]);
});

test('bulk controls react to manual selection and become disabled when no active rows remain',()=>{
    const p=reviewPage([{group:'1',DELETE:'False'}]);
    assert.equal(p.rows[0].hidden,false);
    p.rows[0].fields.use.checked=true;p.events.change();
    assert.equal(p.selectAll.textContent,'Снять выделение');
    p.rows[0].fields.group.value='';p.events.input();
    assert.equal(p.selectAll.disabled,true);assert.equal(p.removeEmpty.disabled,false);
    p.removeEmpty.events.click();
    assert.equal(p.rows[0].fields.use.checked,false);assert.equal(p.removeEmpty.disabled,true);
    assert.match(p.status.textContent,/0 из 0/);
});

test('bulk selection never approves room conflicts and editing a row clears its approval',()=>{
    const p=reviewPage([{group:'1',room:'2'},{source:true}]);
    p.selectAll.events.click();
    assert.ok(p.rows.every(row=>row.fields.use.checked));
    assert.ok(p.rows.every(row=>!row.fields.allow_room_overlap.checked));
    p.rows[0].fields.allow_room_overlap.checked=true;
    p.events.change({target:p.rows[0].fields.use});
    assert.equal(p.rows[0].fields.allow_room_overlap.checked,true);
    p.events.change({target:p.rows[0].fields.room});
    assert.equal(p.rows[0].fields.allow_room_overlap.checked,false);
    p.rows[0].fields.allow_room_overlap.checked=true;
    p.events.input({target:p.rows[0].fields.slot});
    assert.equal(p.rows[0].fields.allow_room_overlap.checked,false);
});
