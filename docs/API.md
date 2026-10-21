# Контракт JSON API v1

API расположен в `/api`, без завершающего `/`. Даты `YYYY-MM-DD`, время `HH:MM`, идентификаторы — целые числа. Время занятий относится к часовому поясу организации (по умолчанию Europe/Moscow). Сервер рассчитывает итоговое расписание; клиент его не объединяет самостоятельно.

## Общие правила

Ошибки: `{"error":{"code":"validation","message":"Описание"}}`. Коды HTTP: 200 — успех, 201 — создание, 400 — валидация, 401 — нужна сессия, 403 — права/CSRF, 404 — запись не найдена, 405 — метод, 429 — лимит, 500 — внутренняя ошибка без раскрытия деталей.

Списки: `{"results":[],"count":0,"page":1,"page_size":50}`. Параметры `page>=1`, `page_size=1..100`. Поиск `q` нечувствителен к регистру, поддерживает кириллицу и инициалы преподавателей.

Ограничение — 180 API-запросов в минуту с IP на процесс, ответ 429 с `Retry-After`. На многопроцессном сервере настройте общее ограничение на обратном прокси. Административные операции используют Django session cookie и CSRF: войдите в `/admin/login/`, передавайте cookie и `X-CSRFToken` из `csrftoken`. CSRF не отключается для JSON или multipart.

## Публичные маршруты

| GET | Параметры / результат |
|---|---|
| `/api/meta` | Текущее время, учебные годы, корпуса, специальности, типы аудиторий, звонки, время обновления, флаг сотрудника |
| `/api/groups` | `q, course, specialty, building, year, archive=1` |
| `/api/teachers` | `q, archive=1` |
| `/api/rooms` | `q`; каталог помещений |
| `/api/groups/{id}/schedule` | `date, view=day\|week` |
| `/api/teachers/{id}/schedule` | `date, view=day\|week` |
| `/api/rooms/{id}/schedule` | `date, view=day\|week` |
| `/api/rooms/availability` | `date, start, end, slot, building, floor, capacity, type, status, q, grid=1` |
| `/api/changes` | `date, group, teacher, building`; опубликованная история редакций на исходную дату |
| `/api/academic-years` | Учебные годы и статусы |

`date` по умолчанию сегодня. Неделя начинается с понедельника. Флаг `archive=1` включает архив; `year` выбирает членство группы в конкретном году. Без архивного режима показываются действующие группы выбранного или активного года.

### Ответ расписания

```json
{
  "id": 1,
  "name": "ИС-1-23",
  "kind": "groups",
  "days": [{
    "date": "2026-10-19",
    "has_data": true,
    "free_intervals": [{"start":"10:00","end":"11:40"}],
    "lessons": [{
      "id": 1,
      "date": "2026-10-19",
      "slot": 1,
      "start": "08:30",
      "end": "10:00",
      "subject_id": 1,
      "subject": "Базы данных",
      "teacher_id": 1,
      "teacher": "Пигачева Анна Сергеевна",
      "room_id": 2,
      "room": "102",
      "building_id": 1,
      "building": "Корпус 1",
      "groups": [{"id":1,"name":"ИС-1-23","subgroup_id":null,"subgroup":""}],
      "lesson_type": "Практика",
      "status": "changed",
      "change_id": 1,
      "reason": "Смена аудитории",
      "original": {},
      "current": false,
      "next": true
    }]
  }]
}
```

`original` содержит исходный DTO занятия либо null для дополнительного занятия. Статусы: `regular`, `changed`, `cancelled`, `moved`, `moved_out`, `added`, `reassigned`. Последний — отметка в расписании прежнего преподавателя/помещения. `moved_out` содержит `new_date`. У дополнительного занятия `id=null`, идентификатор записи — `change_id`. Отмены и отметки переноса не занимают ресурсы.

Свободные интервалы вычисляются в пределах минимального/максимального времени звонков. Для аудитории также вычитаются блокировки. `has_data=false` означает отсутствие опубликованной базовой версии; пустое расписание не считается гарантией доступности помещения.

### Занятость

По умолчанию проверяется текущая минута. `slot` заменяет start/end временем выбранной пары. Интервал `[start,end)` не пересекается с занятием, заканчивающимся точно в start. Интервал должен находиться внутри одних суток и иметь start < end.

Статусы `free`, `busy`, `unavailable`, `unknown`. Приоритет: недоступность/блокировка → занятость → свободно при наличии версии → нет данных. Ответ содержит параметры интервала и список помещений с `lessons`, `blocks`, `next`; при `grid=1` также `grid: [{slot,status}]`.

## Административные маршруты

| Метод и путь | Назначение |
|---|---|
| POST `/api/admin/imports` | Multipart: `file, profile, period, start, end`; возвращает id, payload, errors |
| GET `/api/admin/imports/{id}` | Статус, сопоставленные строки и report с конфликтами/сравнением |
| POST `/api/admin/imports/{id}/confirm` | Атомарная публикация, `version_id` |
| POST `/api/admin/changes` | Создать черновик замены |
| PATCH `/api/admin/changes/{id}` | Изменить только черновик |
| POST `/api/admin/changes/{id}/preview` | Итоговые занятия и конфликты на дату назначения |
| POST `/api/admin/changes/{id}/publish` | Проверить все затронутые даты и опубликовать |
| POST `/api/admin/changes/{id}/withdraw` | Отозвать публикацию с проверкой отката |
| GET `/api/admin/conflicts?date=...` | Конфликты выбранной даты в доступных корпусах |
| POST `/api/admin/academic-years` | Администратор: `name, start_date, end_date` |
| GET `/api/admin/rollover?source=1&target=2` | Администратор: предпросмотр перевода |
| POST `/api/admin/rollover` | Администратор: JSON `source, target`; подтверждение перевода |

Для публикации версии, её отката, отмены предварительного импорта, активации года и CRUD справочников используйте HTML-панель `/admin/`.

### Поля черновика замены

`target_date, original_id, kind, new_date, time_slot_id, start_time, end_time, subject_id, teacher_id, room_id, group_id, subgroup_id, whole_group, reason`.

`kind`: `replace`, `cancel`, `move`, `add`. Для add обязательны группа/пара/дисциплина/преподаватель/аудитория и отсутствующий original_id. Для move обязательна new_date. Произвольные поля и ручное назначение автора/статуса запрещены.

```json
{"target_date":"2026-10-19","original_id":1,"kind":"replace","room_id":2,"reason":"Смена аудитории"}
```

После создания ответ `201 {"id":1,"status":"draft"}`. Черновик не влияет на публичные ответы. Публикация и откат регистрируются в журнале. Диспетчер видит собственные импорты и может менять данные только разрешённых корпусов; администратор имеет полный доступ.
