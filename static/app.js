'use strict';
const app = document.querySelector('#app');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statuses = {regular:'',changed:'Замена',cancelled:'Отменено',moved:'Перенос',moved_out:'Перенесено',added:'Доп. занятие',reassigned:'Занятие перенесено'};
const roomStatuses = {free:'Свободен',busy:'Занят',unavailable:'Недоступен',unknown:'Нет данных'};
const weekdays = ['Пн','Вт','Ср','Чт','Пт','Сб'];
let meta, token = 0;
const params = () => new URLSearchParams(location.search);
const selectedDate = () => params().get('date') || meta.today;
const formatDate = (value, options = {weekday:'long',day:'numeric',month:'long'}) => new Intl.DateTimeFormat('ru-RU',options).format(new Date(value+'T12:00:00'));
function shiftDate(value, amount) {
    const d = new Date(value+'T12:00:00'); d.setDate(d.getDate()+amount);
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
// Sunday is outside the teaching week, including arrow and date-picker navigation.
function teachingDate(value, direction = 1) {
    return new Date(value+'T12:00:00').getDay()===0 ? shiftDate(value,direction<0?-1:1) : value;
}
function favorite() { try { return JSON.parse(localStorage.getItem('college-favorite-v2') || 'null'); } catch { return null; } }
function toast(message) {
    const element = document.querySelector('#toast'); element.textContent = message; element.style.display = 'block';
    setTimeout(() => element.style.display = 'none', 3000);
}
async function get(url) {
    const response = await fetch(url, {headers:{Accept:'application/json'}});
    const value = await response.json(); if (!response.ok) throw new Error(value.error?.message || 'Ошибка загрузки'); return value;
}
function nav(url, replace = false) { history[replace?'replaceState':'pushState']({},'',url); render(); }
function update(values) {
    if(values.date)values.date=teachingDate(values.date);
    const p = params(); for (const [key,value] of Object.entries(values)) value === null || value === '' ? p.delete(key) : p.set(key,value);
    nav(location.pathname+(p.size?'?'+p:''),true);
}
function heading(title, subtitle = '', extra = '') {
    return `<div class="page-heading"><div><h1>${esc(title)}</h1>${subtitle?`<p class="subtitle">${esc(subtitle)}</p>`:''}</div>${extra}</div>`;
}
function options(rows, selected, empty = 'Все') {
    return `<option value="">${empty}</option>` + rows.map(x => `<option value="${x.id}" ${String(x.id)===String(selected)?'selected':''}>${esc(x.name)}</option>`).join('');
}
function toolbar(title = 'Расписание', withWeek = true) {
    const day = selectedDate(), week = params().get('view') === 'week';
    return `<div class="toolbar"><div class="toolbar-left"><h2>${esc(title)}</h2><span class="count">${week&&withWeek?`${formatDate(meta.week.start,{day:'numeric',month:'short'})} – ${formatDate(shiftDate(meta.week.start,5),{day:'numeric',month:'short'})}`:formatDate(day,{day:'numeric',month:'long'})}</span><span class="week-badge">${esc(meta.week.label)}</span></div><div class="toolbar-right"><div class="segmented"><button data-date="${meta.today}" class="${day===teachingDate(meta.today)&&!week?'active':''}">${teachingDate(meta.today)===meta.today?'Сегодня':'Ближайший день'}</button><button data-date="${shiftDate(meta.today,1)}" class="${day===teachingDate(shiftDate(meta.today,1))&&!week?'active':''}">${teachingDate(shiftDate(meta.today,1))===shiftDate(meta.today,1)?'Завтра':'Понедельник'}</button>${withWeek?`<button data-view="${week?'day':'week'}" class="${week?'active':''}">Неделя</button>`:''}</div><button class="btn small" data-shift="-1" aria-label="Назад">‹</button><input type="date" name="date" data-filter aria-label="Дата" value="${day}"><button class="btn small" data-shift="1" aria-label="Вперёд">›</button></div></div>`;
}
function lessonCard(l) {
    const label = l.current ? 'Сейчас' : l.next ? 'Далее' : statuses[l.status];
    const original = l.original;
    const subgroups=l.groups.filter(g=>g.subgroup);
    return `<article class="lesson ${esc(l.status)} ${l.current?'current':''} ${subgroups.length?'subgroup-lesson':''}"><div class="lesson-time">${l.start?`${esc(l.start)}<br><span style="color:var(--muted)">${esc(l.end)}</span>`:'—'}<small>${l.slot?l.slot+' пара':'Кураторский час'}</small></div><div class="lesson-info">${subgroups.length?`<div class="subgroup-badges">${subgroups.map(g=>`<span>${l.groups.length>1?esc(g.name)+' · ':''}Подгруппа ${esc(g.subgroup)}</span>`).join('')}</div>`:''}<h3>${esc(l.subject)}</h3>${l.event_kind==='class_hour'?'<span class="badge">Кураторский час</span>':''}${label?`<span class="badge ${l.current?'current':esc(l.status)}">${esc(label)}</span>`:''}<div class="meta"><a data-nav href="/teachers/${l.teacher_id}?date=${l.date}">${esc(l.teacher)}</a>${l.groups.map(g=>`<a data-nav href="/groups/${g.id}?date=${l.date}">${esc(g.name)}</a>`).join('')}</div>${l.change_id?`<details><summary>Изменения</summary>${original?`<p>Было: ${esc(original.subject)} · ${esc(original.teacher)} · ${esc(original.room)}${original.start?' · '+esc(original.start):''}</p>`:''}${l.new_date?`<p>Перенос на ${formatDate(l.new_date)}</p>`:''}${l.reason?`<p>${esc(l.reason)}</p>`:''}</details>`:''}</div><div class="lesson-room"><a data-nav href="/rooms/${l.room_id}?date=${l.date}">${esc(l.room)}</a></div></article>`;
}
function dayPanel(day, gaps = true, room = false) {
    const intervals=room?day.free_intervals:day.windows;
    return `<section class="panel"><div class="panel-head"><h2>${formatDate(day.date,{weekday:'long',day:'numeric',month:'short'})}</h2><span class="count">${day.lessons.filter(x=>!['cancelled','moved_out','reassigned'].includes(x.status)).length} занятий</span></div>${day.shortened_day?`<div class="day-notice">${esc(day.shortened_day.label)}</div>`:''}${day.practice?`<div class="practice-notice"><span>↗</span><div><strong>${esc(day.practice.label)}</strong>${day.practice.organization?`<p>${esc(day.practice.organization)}</p>`:''}</div></div>`:day.lessons.map(lessonCard).join('')||`<div class="empty"><strong>${day.has_data?'Занятий нет':'Нет расписания'}</strong>${day.empty_reason?`<p>${esc(day.empty_reason)}</p>`:''}${day.next_date?`<button class="btn small" data-date="${day.next_date}">Открыть ${formatDate(day.next_date,{weekday:'short',day:'numeric',month:'long'})} →</button>`:''}</div>`}${gaps&&intervals?.length?`<div class="panel-body count">${room?'Свободно':'Окна'}: ${intervals.map(x=>`${x.start}–${x.end}`).join(' · ')}</div>`:''}</section>`;
}
function sidebar() {
    const f = favorite();
    return `<aside><section class="side-card accent"><div class="side-title">ИЗБРАННОЕ</div><h3>${f?esc(f.name):'Ваше расписание'}</h3>${f?`<a class="btn" data-nav href="/${f.kind}/${Number(f.id)}">Открыть ↗</a>`:'<p>Сохраните группу или преподавателя с помощью ☆</p>'}</section><section class="side-card"><div class="side-title">БЫСТРЫЙ ДОСТУП</div><div class="quick-links"><a data-nav href="/rooms?status=free">Свободные кабинеты <span>↗</span></a><a data-nav href="/changes">Замены <span>↗</span></a><a data-nav href="/bells">Звонки <span>↗</span></a></div></section></aside>`;
}
function searchPanel(kind, home = false) {
    const p = params();
    return `<section class="search-panel">${home?`<div class="tabs">${[['groups','Группы'],['teachers','Преподаватели'],['rooms','Кабинеты']].map(([key,label])=>`<button class="tab ${key===kind?'active':''}" data-mode="${key}">${label}</button>`).join('')}</div>`:''}<form id="search-form" class="searchbox"><input class="field" type="search" id="search" name="q" aria-label="Поиск" autocomplete="off" placeholder="${kind==='teachers'?'Фамилия преподавателя':kind==='rooms'?'Кабинет':'Группа'}" value="${esc(p.get('q')||'')}"><button class="btn primary">Найти ↗</button></form>${kind==='groups'?`<div class="filters"><label>Курс<select name="course" data-filter><option value="">Все курсы</option>${[1,2,3,4].map(x=>`<option ${p.get('course')==x?'selected':''}>${x}</option>`).join('')}</select></label><label>Учебный год<select name="year" data-filter>${options(meta.years,p.get('year'))}</select></label><label class="filter-check"><input type="checkbox" name="archive" data-filter ${p.get('archive')==='1'?'checked':''}>Архив</label><button class="btn small" data-reset>Сбросить</button></div>`:''}</section>`;
}
function pagination(result) {
    if (result.count<=result.page_size) return '';
    return `<div class="pagination"><button class="btn small" data-page="${result.page-1}" ${result.page===1?'disabled':''}>←</button>${result.page} / ${Math.ceil(result.count/result.page_size)}<button class="btn small" data-page="${result.page+1}" ${result.page*result.page_size>=result.count?'disabled':''}>→</button></div>`;
}
function entityCards(rows, kind) {
    return `<div class="cards">${rows.map(x=>`<a data-nav class="entity-card" href="/${kind}/${x.id}?date=${selectedDate()}"><span class="arrow">↗</span><span class="card-icon">${kind==='groups'?'▦':kind==='teachers'?'♙':'▤'}</span><h3>${esc(x.name)}</h3>${x.course?`<p>${x.course} курс</p>`:''}${x.status&&x.status!=='active'?'<p>Архив</p>':''}</a>`).join('')}</div>`;
}
async function home(current) {
    const kind = ['groups','teachers','rooms'].includes(params().get('mode')) ? params().get('mode') : 'groups';
    app.innerHTML = `<section class="hero"><div><div class="eyebrow">ВАШ УЧЕБНЫЙ ДЕНЬ</div><h1>Всё по расписанию.</h1><span class="hero-date">${formatDate(meta.today)}</span></div><div class="hero-symbol" aria-hidden="true">◷</div></section>`+searchPanel(kind,true)+'<div id="home-content"></div>';
    if (params().get('q')) {
        const result = await get(`/api/${kind}?${params()}`); if (current!==token) return;
        document.querySelector('#home-content').innerHTML = entityCards(result.results,kind)+pagination(result)+(result.count?'':'<div class="empty">Ничего не найдено</div>'); return;
    }
    const groupQuery=params();groupQuery.set('page_size','100');groupQuery.delete('group');groupQuery.delete('page');
    const groups = await get('/api/groups?'+groupQuery);
    let f = favorite();
    if (f?.kind==='groups'&&!groups.results.some(g=>g.id===f.id)) f=null;
    const requested=Number(params().get('group'));
    const selected = groups.results.some(g=>g.id===requested)?requested:(f?.kind==='groups'?f.id:groups.results[0]?.id);
    let schedule;
    if (selected) schedule = await get(`/api/groups/${Number(selected)}/schedule?date=${selectedDate()}&view=${params().get('view')||'day'}`);
    const changes = await get(`/api/changes?date=${selectedDate()}&page_size=3`); if (current!==token) return;
    document.querySelector('#home-content').innerHTML = `<div class="split"><div>${toolbar('Расписание')}<div class="filters" style="margin:0 0 16px"><label class="sr-only" for="home-group">Группа</label><select id="home-group" name="group" data-filter>${options(groups.results,selected,'Выберите группу')}</select></div>${schedule?(params().get('view')==='week'?`<div class="week-grid">${schedule.days.map(d=>dayPanel(d,false)).join('')}</div>`:dayPanel(schedule.days[0],false)):'<div class="panel empty">Расписание ещё не загружено</div>'}<div class="toolbar"><h2>Замены <span class="count">${changes.count}</span></h2><a class="count" data-nav href="/changes?date=${selectedDate()}">Все →</a></div><section class="panel">${changes.results.map(lessonCard).join('')||'<div class="panel-body count">На эту дату замен нет</div>'}</section></div>${sidebar()}</div>`;
}
async function catalog(kind,current) {
    app.innerHTML = heading(kind==='groups'?'Группы':'Преподаватели')+searchPanel(kind)+'<div id="results" class="empty">Загрузка…</div>';
    const result = await get(`/api/${kind}?${params()}`); if(current!==token)return;
    const target = document.querySelector('#results'); target.className='';
    target.innerHTML = `<p class="result-label">Найдено: ${result.count}</p>`+(result.count?entityCards(result.results,kind):'<div class="empty">Ничего не найдено</div>')+pagination(result);
}
async function detail(kind,id,current) {
    const result = await get(`/api/${kind}/${id}/schedule?date=${selectedDate()}&view=${params().get('view')||'day'}`); if(current!==token)return;
    let location='';
    if (kind==='teachers'&&selectedDate()===meta.today) {
        const lessons=result.days.flatMap(x=>x.lessons), live=lessons.find(x=>x.current), next=lessons.find(x=>x.next);
        location=`<div class="location"><strong>${live?`Сейчас: ${esc(live.room)} · до ${live.end}`:'Сейчас занятий нет'}</strong>${!live&&next?`<p>Далее в ${next.start} · ${esc(next.room)}</p>`:''}</div>`;
    }
    app.innerHTML=`<a class="back" data-nav href="/${kind}">← ${kind==='groups'?'Группы':kind==='teachers'?'Преподаватели':'Кабинеты'}</a>`+heading(kind==='rooms'?'Кабинет '+result.name:result.name,'',`${kind==='groups'?`<a class="btn" data-nav href="/subjects?group=${id}&date=${selectedDate()}">Дисциплины ↗</a>`:''}<button class="btn" data-favorite="${kind}:${id}" data-name="${esc(result.name)}">☆</button><button class="btn" data-copy>Поделиться ↗</button>`)+toolbar()+location+(params().get('view')==='week'?`<div class="week-grid">${result.days.map(d=>dayPanel(d,false)).join('')}</div>`:`<div class="split"><div>${dayPanel(result.days[0],kind!=='groups',kind==='rooms')}</div>${sidebar()}</div>`);
}
async function rooms(current) {
    const p=params();
    app.innerHTML=heading('Кабинеты')+`<section class="search-panel"><form id="search-form" class="searchbox"><input id="search" name="q" class="field" type="search" aria-label="Кабинет" placeholder="Номер или обозначение" value="${esc(p.get('q')||'')}"><button class="btn primary">Найти ↗</button></form><div class="filters"><label>Пара<select name="slot" data-filter><option value="">По времени</option>${meta.slots.map(x=>`<option value="${x.number}" ${p.get('slot')==x.number?'selected':''}>${x.number} · ${x.start}–${x.end}</option>`).join('')}</select></label><label>С<input type="time" name="start" data-filter value="${esc(p.get('start')||meta.now.slice(11,16))}"></label><label>До<input type="time" name="end" data-filter value="${esc(p.get('end')||'')}"></label><label>Статус<select name="status" data-filter><option value="">Все</option>${Object.entries(roomStatuses).map(([key,label])=>`<option value="${key}" ${p.get('status')===key?'selected':''}>${label}</option>`).join('')}</select></label><button class="btn small" data-reset>Сбросить</button></div></section>`+toolbar('Занятость',false)+'<div id="room-results"></div>';
    if(!p.has('start'))p.set('start',meta.now.slice(11,16));
    if(!p.has('end')){const d=new Date('2000-01-01T'+p.get('start'));d.setMinutes(d.getMinutes()+1);p.set('end',d.toTimeString().slice(0,5));}
    const result=await get('/api/rooms/availability?'+p);if(current!==token)return;
    const target=document.querySelector('#room-results');
    target.innerHTML=`<div class="toolbar"><span class="count">${result.count} кабинетов · ${result.start}–${result.end}</span><div class="segmented"><button data-grid="0" class="${p.get('grid')!=='1'?'active':''}">Карточки</button><button data-grid="1" class="${p.get('grid')==='1'?'active':''}">Сетка</button></div></div>`;
    if(!result.count){target.innerHTML+='<div class="empty">Нет подходящих кабинетов</div>';return;}
    if(p.get('grid')==='1')target.innerHTML+=`<div class="panel room-grid" style="--slots:${meta.slots.length}"><div class="grid-row header"><span>Кабинет</span>${meta.slots.map(x=>`<span>${x.number} пара<br>${x.start}–${x.end}</span>`).join('')}</div>${result.results.map(r=>`<div class="grid-row"><a data-nav href="/rooms/${r.id}?date=${selectedDate()}">${esc(r.number)}</a>${r.grid.map(s=>`<div class="grid-cell ${s.status}"><span>${roomStatuses[s.status]}</span></div>`).join('')}</div>`).join('')}</div>`;
    else target.innerHTML+=`<div class="cards">${result.results.map(r=>`<a data-nav class="entity-card" href="/rooms/${r.id}?date=${selectedDate()}"><span class="status ${r.status}" style="float:right">${roomStatuses[r.status]}</span><div class="room-number">${esc(r.number)}</div>${r.lessons.map(l=>`<p>${esc(l.subject)}<br>${esc(l.teacher)} · до ${l.end}</p>`).join('')}${r.blocks.length?`<p>${r.blocks.map(esc).join(', ')}</p>`:r.status==='free'&&r.next?`<p>Далее в ${r.next.start}</p>`:''}</a>`).join('')}</div>`;
    target.innerHTML+=pagination(result);
}
async function changes(current) {
    app.innerHTML=heading('Замены','',meta.staff?'<a class="btn primary" href="/admin/schedule/imageimport/upload/">Загрузить изображение ↗</a>':'')+toolbar('Изменения',false)+'<div id="change-results"></div>';
    const result=await get('/api/changes?'+params());if(current!==token)return;
    document.querySelector('#change-results').innerHTML=`<section class="panel">${result.results.map(lessonCard).join('')||'<div class="empty"><strong>Замен нет</strong></div>'}</section>`+pagination(result);
}
function bells() {
    const now=meta.now.slice(11,16), day=selectedDate(), index=(new Date(day+'T12:00:00').getDay()+6)%7;
    app.innerHTML=heading('Звонки',formatDate(day,{weekday:'long'}),meta.manage_bells?`<a class="btn" href="/admin/bells/?weekday=${index}">Изменить звонки ↗</a>`:'')+toolbar('Расписание звонков',false)+`<div class="weekday-tabs segmented">${weekdays.map((name,i)=>`<button data-date="${shiftDate(day,i-index)}" class="${i===index?'active':''}">${name}</button>`).join('')}</div>${meta.shortened_day?`<div class="day-notice">${esc(meta.shortened_day.label)}${meta.shortened_day.note?' · '+esc(meta.shortened_day.note):''}</div>`:''}<div class="bell-grid">${meta.slots.map((s,i)=>`<section class="bell-card ${day===meta.today&&s.start<=now&&now<s.end?'current':''}"><div class="bell-index">${s.number} ПАРА</div><div class="bell-time">${s.start}<span style="color:var(--faint)"> — </span>${s.end}</div>${meta.slots[i+1]?`<p>Перерыв ${Math.max(0,Math.round((new Date('2000-01-01T'+meta.slots[i+1].start)-new Date('2000-01-01T'+s.end))/60000))} мин</p>`:''}</section>`).join('')||'<div class="empty">Звонки не заданы</div>'}</div>${meta.curator_bells?.length?`<section class="panel curator-bells"><div class="panel-head"><h2>Кураторский час</h2><span class="count">Разговоры о важном · понедельник</span></div><div class="bell-grid panel-body">${meta.curator_bells.map(s=>`<div><strong>${s.shift}-я смена</strong><p>${s.start}–${s.end}</p></div>`).join('')}</div></section>`:''}`;
}

async function subjects(current) {
    const p=params(), requestedPeriod=meta.periods.find(x=>String(x.id)===p.get('period'));
    const year=p.get('year')||requestedPeriod?.year_id;
    const groups=await get('/api/groups?page_size=100'+(year?'&year='+encodeURIComponent(year):''));
    if(current!==token)return;
    const selected=groups.results.find(g=>String(g.id)===p.get('group'))||groups.results[0];
    const activePeriod=meta.periods.find(x=>x.start_date<=selectedDate()&&selectedDate()<=x.end_date);
    const period=meta.periods.find(x=>String(x.id)===p.get('period'))||activePeriod;
    const periods=meta.periods.map(x=>({...x,name:x.name+' · '+(meta.years.find(y=>y.id===x.year_id)?.name||'')}));
    app.innerHTML=heading('Дисциплины')+`<section class="search-panel"><div class="filters"><label>Группа<select name="group" data-filter>${options(groups.results,selected?.id,'Выберите группу')}</select></label><label>Учебный период<select name="period" data-filter>${options(periods,period?.id,'На выбранную дату')}</select></label><label>Дата<input type="date" name="date" data-filter value="${selectedDate()}"></label></div><form id="search-form" class="searchbox subject-search"><input class="field" type="search" id="search" name="q" placeholder="Найти дисциплину" aria-label="Дисциплина" value="${esc(p.get('q')||'')}"><button class="btn primary">Найти ↗</button></form></section><div id="subject-results"></div>`;
    if(!selected){document.querySelector('#subject-results').innerHTML='<div class="empty">Группы ещё не загружены</div>';return;}
    const query=new URLSearchParams({group:selected.id,date:selectedDate(),q:p.get('q')||''});
    if(period)query.set('period',period.id);
    const data=await get('/api/subjects?'+query);if(current!==token)return;
    document.querySelector('#subject-results').innerHTML=`<div class="toolbar"><h2>${esc(selected.name)} <span class="count">${data.subjects.length} дисциплин</span></h2><a class="btn small" data-nav href="/groups/${selected.id}?date=${selectedDate()}">Расписание ↗</a></div>${data.period?`<p class="count">${esc(data.period.name)} · ${formatDate(data.period.start,{day:'numeric',month:'short',year:'numeric'})} – ${formatDate(data.period.end,{day:'numeric',month:'short',year:'numeric'})}</p>`:''}<section class="panel">${data.subjects.map(s=>`<article class="subject-row"><div><h3>${esc(s.name)}</h3><div class="meta">${s.teachers.map(t=>`<a data-nav href="/teachers/${t.id}?date=${selectedDate()}">${esc(t.name)}</a>`).join('')}</div></div><span class="week-badge">${s.weeks.length===2?'Обе недели':s.weeks[0]+'-я неделя'}</span></article>`).join('')||'<div class="empty">Дисциплины не найдены</div>'}</section>`;
}
// Keep a real select as the form control; the popup supports search and keyboard navigation.
function upgradeSelects() {
    document.querySelectorAll('#app select').forEach((select,index)=>{
        const label=select.closest('label')?.firstChild?.textContent?.trim()||'Выбор';
        const wrap=document.createElement('div');wrap.className='select-wrap';select.before(wrap);wrap.append(select);
        select.classList.add('sr-only');select.tabIndex=-1;select.setAttribute('aria-hidden','true');
        const trigger=document.createElement('button');trigger.type='button';trigger.className='select-trigger';trigger.textContent=select.selectedOptions[0]?.textContent||'Выбрать';trigger.setAttribute('aria-label',label);trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');wrap.append(trigger);
        let popup;
        const close=()=>{popup?.remove();popup=null;trigger.setAttribute('aria-expanded','false');};
        trigger.addEventListener('click',event=>{
            event.preventDefault();if(popup){close();return;}
            document.querySelectorAll('.select-popup').forEach(p=>p.dispatchEvent(new Event('dismiss')));
            popup=document.createElement('div');popup.className='select-popup';popup.addEventListener('dismiss',close);trigger.setAttribute('aria-expanded','true');wrap.append(popup);
            const search=document.createElement('input');search.type='search';search.className='select-search';search.placeholder='Поиск';search.setAttribute('aria-label','Поиск варианта');popup.append(search);
            const list=document.createElement('div');list.className='select-options';list.setAttribute('role','listbox');list.setAttribute('aria-label',label);popup.append(list);
            const fill=()=>{list.replaceChildren();[...select.options].filter(o=>o.textContent.toLowerCase().includes(search.value.toLowerCase())).forEach(option=>{
                const row=document.createElement('div');row.className='select-option';row.tabIndex=0;row.setAttribute('role','option');row.setAttribute('aria-selected',String(option.selected));row.textContent=option.textContent;
                const choose=()=>{select.value=option.value;trigger.textContent=option.textContent;close();select.dispatchEvent(new Event('change',{bubbles:true}));};
                row.addEventListener('click',event=>{event.preventDefault();choose();});row.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}});list.append(row);
            });};fill();search.addEventListener('input',fill);
            popup.addEventListener('keydown',event=>{const rows=[...list.children];let idx=rows.indexOf(document.activeElement);if(event.key==='Escape'){event.preventDefault();close();trigger.focus();}else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();rows[(idx+(event.key==='ArrowDown'?1:-1)+rows.length)%rows.length]?.focus();}});search.focus();
        });
        wrap.addEventListener('focusout',()=>setTimeout(()=>{if(!wrap.contains(document.activeElement))close();},0));
    });
}
async function render() {
    const current=++token;
    try {
        const nextMeta=await get('/api/meta'+(params().get('date')?'?date='+encodeURIComponent(params().get('date')):''));if(current!==token)return;meta=nextMeta;
        if(teachingDate(selectedDate())!==selectedDate()){update({date:teachingDate(selectedDate())});return;}
        document.querySelector('#year-label').textContent=meta.years.find(x=>x.status==='active')?.name||'';
        document.querySelector('#today-label').textContent=formatDate(meta.today,{day:'numeric',month:'long'});
        document.querySelector('#current-week').textContent='Сейчас '+meta.current_week.label;
        document.querySelector('#current-week').title=meta.current_week.number?`${formatDate(meta.current_week.start)} — ${formatDate(meta.current_week.end)}`:'';
        if(meta.updated_at)document.querySelector('#updated').textContent='Обновлено '+new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'}).format(new Date(meta.updated_at));
        document.querySelectorAll('.sidebar nav a').forEach(a=>a.classList.toggle('active',a.pathname==='/'?location.pathname==='/':location.pathname.startsWith(a.pathname)));
        const match=location.pathname.match(/^\/(groups|teachers|rooms)(?:\/(\d+))?\/?$/);
        if(match?.[2])await detail(match[1],Number(match[2]),current);
        else if(match?.[1]==='rooms')await rooms(current);
        else if(match)await catalog(match[1],current);
        else if(location.pathname==='/changes')await changes(current);
        else if(location.pathname==='/bells')bells();
        else if(location.pathname==='/subjects')await subjects(current);
        else await home(current);
        if(current===token){upgradeSelects();document.title=(document.querySelector('h1')?.textContent||'Расписание')+' · Колледж';}
    }catch(error){if(current===token)app.innerHTML=`<div class="error"><h2>Не удалось загрузить</h2><p>${esc(error.message)}</p><button class="btn" data-retry>Повторить</button></div>`;}
}
document.addEventListener('click',async event=>{
    if(!event.target.closest('.select-wrap'))document.querySelectorAll('.select-popup').forEach(p=>p.dispatchEvent(new Event('dismiss')));
    const el=event.target.closest('a[data-nav],button');if(!el)return;
    if(el.matches('a[data-nav]')){if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();nav(el.getAttribute('href'));return;}
    if(el.id==='theme-toggle'){const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;el.setAttribute('aria-pressed',String(theme==='dark'));try{localStorage.setItem('college-theme',theme);}catch{}return;}
    if(el.hasAttribute('data-mode'))update({mode:el.dataset.mode,q:null,page:null});
    if(el.hasAttribute('data-date'))update({date:el.dataset.date,view:null,page:null});
    if(el.hasAttribute('data-view'))update({view:el.dataset.view});
    if(el.hasAttribute('data-grid'))update({grid:el.dataset.grid});
    if(el.hasAttribute('data-shift'))update({date:teachingDate(shiftDate(selectedDate(),Number(el.dataset.shift)*(params().get('view')==='week'?7:1)),Number(el.dataset.shift))});
    if(el.hasAttribute('data-page'))update({page:el.dataset.page});
    if(el.hasAttribute('data-reset'))nav(location.pathname);
    if(el.hasAttribute('data-retry'))render();
    if(el.hasAttribute('data-favorite')){const[kind,id]=el.dataset.favorite.split(':');try{localStorage.setItem('college-favorite-v2',JSON.stringify({kind,id:Number(id),name:el.dataset.name}));toast('Сохранено');}catch{toast('Сохранение недоступно');}}
    if(el.hasAttribute('data-copy')){try{await navigator.clipboard.writeText(location.href);toast('Ссылка скопирована');}catch{toast('Скопируйте адрес из строки браузера');}}
});
document.addEventListener('submit',event=>{if(event.target.id==='search-form'){event.preventDefault();update({q:new FormData(event.target).get('q'),page:null});}});
document.addEventListener('change',event=>{const el=event.target;if(el.matches('[data-filter]'))update({[el.name]:el.type==='checkbox'?(el.checked?'1':null):el.value,page:null,...(el.name==='start'||el.name==='end'?{slot:null}:{})});});
// Search is submitted explicitly, so typing never replaces the focused input.
window.addEventListener('popstate',render);render();
setInterval(()=>{const search=document.querySelector('#search');if(document.hidden||document.activeElement?.matches('input,select,button.select-trigger')||document.querySelector('.select-popup')||(search&&search.value!==(params().get('q')||'')))return;render();},60000);
