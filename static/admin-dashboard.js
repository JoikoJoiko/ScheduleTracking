'use strict';
(() => {
    const dashboard = document.querySelector('.upload-dashboard');
    if (!dashboard) return;
    const dateFormat = new Intl.DateTimeFormat('ru-RU', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
    const uploadQueue = [];
    let sending = false;
    // Serialize uploads from this page so four course files do not compete for SQLite writes.
    function nextUpload() {
        if (sending || !uploadQueue.length) return;
        sending = true; uploadQueue.shift()();
    }

    function paintHistory(rows) {
        const target = document.querySelector('#upload-history');
        target.replaceChildren();
        for (const row of rows) {
            const tr = document.createElement('tr');
            const name = document.createElement('td');
            const icon = document.createElement('span'); icon.className = 'history-icon'; icon.textContent = row.kind === 'schedule' ? '▦' : '⇄'; icon.setAttribute('aria-hidden','true');
            name.append(icon, document.createTextNode(row.name));
            const date = document.createElement('td'); date.textContent = dateFormat.format(new Date(row.date));
            const state = document.createElement('td');
            const badge = document.createElement('span'); badge.className = 'history-state state-'+row.state; badge.textContent = row.label; state.append(badge);
            const action = document.createElement('td'); const link = document.createElement('a'); link.href = row.url; link.textContent = 'Открыть ↗'; action.append(link);
            tr.append(name,date,state,action); target.append(tr);
        }
    }

    async function refresh() {
        try {
            const response = await fetch(dashboard.dataset.statusUrl, {headers:{Accept:'application/json'}});
            if (!response.ok) return;
            const data = await response.json();
            for (const course of data.courses) {
                const card = dashboard.querySelector(`[data-course="${course.number}"]`); if (!card) continue;
                const state = card.querySelector('.course-status'); state.textContent = course.groups ? 'Загружен' : 'Нет файла'; state.classList.toggle('loaded',Boolean(course.groups));
                card.querySelector('.course-meta').textContent = course.groups ? `Групп: ${course.groups}` : 'Расписание ещё не загружено';
                card.querySelector('.course-last-update').textContent = course.published_at ? 'Обновлено '+dateFormat.format(new Date(course.published_at)) : 'Другие курсы сохранятся';
            }
            document.querySelector('#pending-image-count').textContent = data.pending_images;
            paintHistory(data.history);
        } catch { /* An interrupted status refresh must not erase a successful upload. */ }
    }

    for (const form of dashboard.querySelectorAll('.live-upload')) {
        const input = form.querySelector('input[type=file]');
        const dropzone = form.querySelector('.upload-dropzone');
        const button = form.querySelector('.upload-submit');
        const feedback = form.querySelector('.upload-feedback');
        const message = form.querySelector('.upload-message');
        const errors = form.querySelector('.upload-errors');
        const link = form.querySelector('.upload-result-link');
        const progress = form.querySelector('progress');
        const defaultLabel = dropzone.querySelector('.file-label').textContent;
        const defaultButton = button.innerHTML;
        let busy = false;

        function showFile() {
            dropzone.classList.toggle('has-file',Boolean(input.files.length));
            dropzone.querySelector('.file-label').textContent = input.files[0]?.name || defaultLabel;
        }
        input.addEventListener('change',showFile);
        for (const type of ['dragenter','dragover']) dropzone.addEventListener(type,event=>{event.preventDefault();if(!busy)dropzone.classList.add('drag-over');});
        for (const type of ['dragleave','drop']) dropzone.addEventListener(type,event=>{event.preventDefault();dropzone.classList.remove('drag-over');});
        dropzone.addEventListener('drop',event=>{
            if(busy || !event.dataTransfer.files.length)return;
            if(event.dataTransfer.files.length>1){message.textContent='Выберите один файл для этого курса или набора замен.';return;}
            input.files=event.dataTransfer.files;showFile();
        });

        form.addEventListener('submit',event=>{
            event.preventDefault(); if(busy || !form.reportValidity())return;
            errors.replaceChildren(); errors.hidden=true; link.hidden=true; feedback.className='upload-feedback';
            const file=input.files[0];
            if(file.size>10*1024*1024){feedback.classList.add('is-error');message.textContent='Файл больше 10 МБ.';return;}
            if(form.dataset.kind==='schedule' && !file.name.toLowerCase().endsWith('.xlsx')){feedback.classList.add('is-error');message.textContent='Выберите файл XLSX.';return;}
            const data=new FormData(form);
            if(form.dataset.kind==='schedule')data.set('publish',document.querySelector('#auto-publish').checked?'1':'');
            const xhr=new XMLHttpRequest();
            xhr.open('POST',form.action);xhr.setRequestHeader('Accept','application/json');xhr.setRequestHeader('X-CSRFToken',form.querySelector('[name=csrfmiddlewaretoken]').value);
            xhr.timeout=240000;
            busy=true;form.classList.add('is-busy');form.setAttribute('aria-busy','true');button.disabled=true;input.disabled=true;progress.hidden=true;progress.value=0;message.textContent='Ожидаем обработки…';button.textContent='В очереди…';
            xhr.upload.addEventListener('progress',event=>{if(event.lengthComputable){progress.value=Math.round(event.loaded/event.total*100);message.textContent=`Загрузка ${progress.value}%`;}});
            xhr.upload.addEventListener('load',()=>{progress.removeAttribute('value');message.textContent=form.dataset.kind==='schedule'?'Разбираем таблицу и проверяем расписание…':'Распознаём изображение…';});
            function finish(){busy=false;form.classList.remove('is-busy');form.removeAttribute('aria-busy');button.disabled=false;input.disabled=false;button.innerHTML=form.dataset.kind==='schedule'?(document.querySelector('#auto-publish').checked?'Загрузить и обновить <span>↗</span>':'Загрузить на проверку <span>↗</span>'):defaultButton;progress.hidden=true;sending=false;setTimeout(nextUpload,0);}
            xhr.addEventListener('load',()=>{
                finish();
                let result;
                try{result=JSON.parse(xhr.responseText);}catch{feedback.classList.add('is-error');message.textContent='Сессия завершена или сервер недоступен. Обновите страницу.';return;}
                if(xhr.status<200 || xhr.status>=300){feedback.classList.add('is-error');message.textContent=result.error||'Не удалось загрузить файл.';return;}
                feedback.classList.add(result.state==='published'?'is-success':'needs-review');message.textContent=result.message;
                if(result.errors?.length){errors.hidden=false;for(const text of result.errors.slice(0,10)){const item=document.createElement('li');item.textContent=text;errors.append(item);}}
                if(result.url){link.href=result.url;link.textContent=result.state==='published'?'Посмотреть результат →':'Проверить и опубликовать →';link.hidden=false;}
                input.value='';showFile();refresh();
            });
            xhr.addEventListener('error',()=>{finish();feedback.classList.add('is-error');message.textContent='Соединение прервалось. Проверьте историю загрузок перед повторной отправкой.';refresh();});
            xhr.addEventListener('timeout',()=>{finish();feedback.classList.add('is-error');message.textContent='Обработка ещё не завершилась. Проверьте историю загрузок.';refresh();});
            uploadQueue.push(()=>{
                progress.hidden=false;message.textContent='Загружаем файл…';button.textContent='Обработка…';
                try{xhr.send(data);}catch{finish();feedback.classList.add('is-error');message.textContent='Не удалось отправить файл. Обновите страницу и повторите.';}
            });
            nextUpload();
        });
    }
    const toggle=document.querySelector('#auto-publish');
    toggle?.addEventListener('change',()=>dashboard.querySelectorAll('[data-kind=schedule] .upload-submit').forEach(button=>{if(!button.disabled)button.innerHTML=toggle.checked?'Загрузить и обновить <span>↗</span>':'Загрузить на проверку <span>↗</span>';}));
    setInterval(()=>{if(!document.hidden)refresh();},30000);
})();
