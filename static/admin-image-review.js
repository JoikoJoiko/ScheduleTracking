'use strict';
document.querySelectorAll('.review-table tbody tr').forEach(row => {
    const field = name => row.querySelector(`select[name$="-${name}"]`);
    const status = row.querySelector('.candidate-status');
    let revision = 0;
    field('group').addEventListener('change', async () => {
        const current = ++revision, group = field('group').value;
        const checked = row.querySelector('input[name$="-use"]');
        checked.checked = false;
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
            subject:row.dataset.subject || '', teacher:row.dataset.teacher || ''});
        try {
            const response = await fetch('/api/admin/image-candidates?' + query, {headers:{Accept:'application/json'}});
            const data = await response.json();
            if (current !== revision) return;
            if (!response.ok) throw new Error(data.error?.message || 'Ошибка загрузки');
            fill('subject', data.subjects, data.suggestions.subject_id);
            fill('teacher', data.teachers, data.suggestions.teacher_id);
            fill('subgroup', data.subgroups);
            status.textContent = data.subjects.length ? '' : 'У группы нет опубликованных дисциплин на этот период.';
        } catch (error) { if (current === revision) status.textContent = error.message; }
    });
});
