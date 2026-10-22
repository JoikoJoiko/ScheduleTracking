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
