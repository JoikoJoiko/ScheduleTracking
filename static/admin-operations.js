'use strict';
// Editing a time explicitly opts the row into a weekday override.
document.querySelectorAll('.operation-table input[type=time]').forEach(input => input.addEventListener('input', () => {
    const checkbox = input.closest('tr')?.querySelector('input[type=checkbox]');
    if (checkbox) checkbox.checked = true;
}));
const keep = document.querySelector('#id_keep_breaks'), pause = document.querySelector('#id_break_minutes');
if (keep && pause) {
    const sync = () => { pause.disabled = keep.checked; pause.required = !keep.checked; };
    keep.addEventListener('change', sync); sync();
}
