'use strict';
// Keep native selects for validation and POST; search only filters the popup locally.
let activePicker;
document.querySelectorAll('.operation-form select,.review-table select,.room-place-form select').forEach((select,index) => {
    const trigger=document.createElement('button');trigger.type='button';trigger.className='admin-picker';
    const label=select.closest('label')?.firstChild?.textContent?.trim()||select.getAttribute('aria-label')||select.name.split('-').at(-1);
    trigger.setAttribute('aria-label',label);trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
    select.classList.add('admin-native-select');select.tabIndex=-1;select.after(trigger);
    const sync=()=>{trigger.textContent=select.selectedOptions[0]?.textContent||'Выбрать';trigger.disabled=select.disabled;};
    select.addEventListener('change',()=>{sync();trigger.removeAttribute('aria-invalid');});
    new MutationObserver(sync).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected']});sync();
    const open=()=>{
        if(activePicker?.trigger===trigger){activePicker.close();return;}
        activePicker?.close();
        const popup=document.createElement('div');popup.className='admin-picker-popup';
        const rect=trigger.getBoundingClientRect(),width=Math.min(Math.max(rect.width,260),window.innerWidth-24);
        popup.style.width=width+'px';popup.style.left=Math.min(rect.left,window.innerWidth-width-12)+'px';
        popup.style.top=Math.min(rect.bottom+6,Math.max(12,window.innerHeight-300))+'px';
        const search=document.createElement('input');search.type='search';search.placeholder='Найти';search.setAttribute('aria-label','Поиск варианта');
        const list=document.createElement('div');list.className='admin-picker-options';list.id='admin-options-'+index;
        list.setAttribute('role','listbox');list.setAttribute('aria-label',label);trigger.setAttribute('aria-controls',list.id);
        popup.append(search,list);document.body.append(popup);trigger.setAttribute('aria-expanded','true');
        const close=()=>{popup.remove();trigger.setAttribute('aria-expanded','false');trigger.removeAttribute('aria-controls');activePicker=null;};
        activePicker={trigger,popup,close};
        const fill=()=>{
            list.replaceChildren();
            [...select.options].filter(option=>!option.disabled&&option.textContent.toLocaleLowerCase('ru').includes(search.value.toLocaleLowerCase('ru'))).forEach(option=>{
                const button=document.createElement('button');button.type='button';button.setAttribute('role','option');
                button.setAttribute('aria-selected',String(option.selected));button.textContent=option.textContent;
                button.addEventListener('click',()=>{select.value=option.value;close();select.dispatchEvent(new Event('change',{bubbles:true}));trigger.focus();});list.append(button);
            });
            if(!list.children.length){const empty=document.createElement('p');empty.textContent='Ничего не найдено';list.append(empty);}
        };
        popup.addEventListener('keydown',event=>{
            const rows=[...list.querySelectorAll('button')],i=rows.indexOf(document.activeElement);
            if(event.key==='Escape'){event.preventDefault();close();trigger.focus();}
            if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();rows[(i+(event.key==='ArrowDown'?1:-1)+rows.length)%rows.length]?.focus();}
        });
        search.addEventListener('input',fill);fill();search.focus();
    };
    trigger.addEventListener('click',open);
    select.addEventListener('invalid',event=>{event.preventDefault();trigger.setAttribute('aria-invalid','true');trigger.focus();});
});
document.addEventListener('click',event=>{if(activePicker&&!activePicker.popup.contains(event.target)&&activePicker.trigger!==event.target)activePicker.close();});
document.addEventListener('focusin',event=>{if(activePicker&&!activePicker.popup.contains(event.target)&&activePicker.trigger!==event.target)activePicker.close();});
window.addEventListener('resize',()=>activePicker?.close());
document.addEventListener('scroll',event=>{if(activePicker&&!activePicker.popup.contains(event.target))activePicker.close();},true);
