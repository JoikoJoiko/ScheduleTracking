'use strict';
const reviewField = (row, name) => row.querySelector(`[name$="-${name}"]`);
const reviewRowDeleted = row => ['on','true','1'].includes(String(reviewField(row,'DELETE')?.value).toLowerCase());
function reviewRowEmpty(row) {
    // Unreadable OCR rows still contain source evidence and need human review.
    return row.dataset.hasSource!=='1' &&
        ['group','subgroup','slot','subject','teacher','room'].every(name => !String(reviewField(row,name)?.value || '').trim()) &&
        reviewField(row,'kind')?.value!=='cancel';
}

document.querySelectorAll('.review-table').forEach(table => {
    const form=table.closest('form'), selectAll=form.querySelector('[data-review-select-all]');
    const removeEmpty=form.querySelector('[data-review-remove-empty]'), status=form.querySelector('[data-review-status]');
    if (!selectAll || !removeEmpty) return;
    const rows=() => [...table.tBodies[0].rows].filter(row => !reviewRowDeleted(row));
    const refresh=(message='') => {
        const active=rows(), filled=active.filter(row => !reviewRowEmpty(row));
        const selected=filled.filter(row => reviewField(row,'use').checked).length;
        selectAll.textContent=filled.length && selected===filled.length?'Снять выделение':'Выбрать все';
        selectAll.disabled=!filled.length;
        removeEmpty.disabled=!active.some(reviewRowEmpty);
        status.textContent=`${message}Выбрано: ${selected} из ${filled.length}`;
    };
    selectAll.addEventListener('click',() => {
        const filled=rows().filter(row => !reviewRowEmpty(row));
        const select=!filled.every(row => reviewField(row,'use').checked);
        filled.forEach(row => {reviewField(row,'use').checked=select;});
        refresh();
    });
    removeEmpty.addEventListener('click',() => {
        const empty=rows().filter(reviewRowEmpty);
        empty.forEach(row => {
            // Preserve form indices so Django and source-fragment links keep matching.
            reviewField(row,'DELETE').value='on';
            reviewField(row,'use').checked=false;
            row.hidden=true;
        });
        refresh(`Удалено: ${empty.length}. `);
    });
    [...table.tBodies[0].rows].forEach(row => {row.hidden=reviewRowDeleted(row);});
    table.addEventListener('change',() => refresh());
    table.addEventListener('input',() => refresh());
    refresh();
});

document.querySelectorAll('.review-table tbody tr').forEach(row => {
    const field = name => row.querySelector(`select[name$="-${name}"]`);
    const status = row.querySelector('.candidate-status');
    let revision = 0;
    field('group').addEventListener('change', async () => {
        const current = ++revision, group = field('group').value;
        const checked = row.querySelector('input[name$="-use"]');
        checked.checked = false;
        field('room').value='';field('room').dispatchEvent(new Event('change'));
        const fill = (name, values, selected = '') => {
            const select = field(name);
            select.replaceChildren(new Option('---------', ''));
            values.forEach(value => select.add(new Option(value.name, value.id)));
            select.value = selected || '';
        };
        ['subject','teacher','subgroup'].forEach(name => fill(name, []));
        if (!group) { status.textContent = ''; return; }
        status.textContent = 'Загрузка дисциплин…';
        const query = new URLSearchParams({group, date:row.closest('table').dataset.date,
            subject:row.dataset.subject || '', teacher:row.dataset.teacher || '',room:row.dataset.room||'',slot:row.querySelector('input[name$="-slot"]').value});
        try {
            const response = await fetch('/api/admin/image-candidates?' + query, {headers:{Accept:'application/json'}});
            const data = await response.json();
            if (current !== revision) return;
            if (!response.ok) throw new Error(data.error?.message || 'Ошибка загрузки');
            fill('subject', data.subjects, data.suggestions.subject_id);
            fill('teacher', data.teachers, data.suggestions.teacher_id);
            fill('subgroup', data.subgroups,data.suggestions.subgroup_id);
            if(data.suggestions.room_id){field('room').value=data.suggestions.room_id;field('room').dispatchEvent(new Event('change'));}
            row.classList.toggle('review-inferred',data.suggestions.inferred);
            status.textContent = data.subjects.length ? (data.suggestions.inferred?'Подобрано · '+data.suggestions.inferences.join('. '):'') : 'У группы нет опубликованных дисциплин на этот период.';
        } catch (error) { if (current === revision) status.textContent = error.message; }
    });
});
