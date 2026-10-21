# Развёртывание, конфигурация и резервное копирование

## Локально

Достаточно Python, виртуального окружения и SQLite; команды находятся в README. Сервер по умолчанию слушает только `127.0.0.1:8000`. База, локальный ключ и резервные копии исключены из Git. Перед передачей проекта выполните миграции на целевой машине.

`.env.example` — перечень параметров. Django автоматически `.env` не читает: локально задавайте переменные через `$env:NAME='value'` в PowerShell или `export NAME=value` на Linux. Docker Compose читает файл `.env` для подстановки.

## Параметры

| Переменная | Назначение |
|---|---|
| DEBUG | `1` локально (по умолчанию), `0` на сервере |
| SECRET_KEY | Обязательный случайный секрет при DEBUG=0; локально генерируется `.secret_key` |
| ALLOWED_HOSTS | Домены через запятую, без схемы |
| CSRF_TRUSTED_ORIGINS | Адреса с https, через запятую |
| DATABASE_URL | PostgreSQL URI; без него SQLite |
| TIME_ZONE | По умолчанию Europe/Moscow |
| TRUST_PROXY | `1` только за доверенным прокси, который перезаписывает X-Forwarded-Proto |
| DOMAIN | Docker/Caddy: публичный домен |
| POSTGRES_PASSWORD | Docker: пароль PostgreSQL |

Не храните реальные секреты в репозитории. Для `SECRET_KEY` используйте `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Пароль в DATABASE_URL должен быть URL-кодирован; в предоставленном Compose используйте случайные буквы и цифры без URI-разделителей.

## Docker Compose на сервере

Требуются Docker Compose, публичный DNS домена, доступные порты 80/443.

1. Создайте `.env` в корне:

```dotenv
DOMAIN=schedule.example.org
SECRET_KEY=your-long-random-secret
POSTGRES_PASSWORD=your-random-alphanumeric-password
```

2. Выполните:

```sh
docker compose up -d --build
docker compose exec app python manage.py createsuperuser
docker compose exec app python manage.py check --deploy
```

Схема: Caddy (HTTPS и static) → Waitress/Django → PostgreSQL. PostgreSQL и приложение не публикуют свои порты на хост. Приложение работает от непривилегированного пользователя. При старте применяются миграции и собирается статика. Caddy получает сертификат автоматически для настроенного публичного домена. Том `database` содержит БД; `static` — статику, `caddy_data` — сертификаты, `backups` — копии.

После создания администратора загрузите реальные XLSX через панель. Демо-генератор удалён. Учётная запись `admin / admin` существует только в локальной БД по запросу пользователя и не создаётся миграциями.

Dockerfile включает Tesseract с русским и английским языками. Том `media` содержит оригиналы изображений замен; включите его в резервное копирование вместе с БД. Вне Docker на Windows используется системный OCR с русским языковым пакетом, на Linux установите Tesseract `rus+eng`.

Внешнее размещение и Docker в этой сессии не запускались: перед эксплуатацией проверьте конфигурацию на вашем хостинге, сетевые правила и получение сертификата. Для другого прокси обязательно запретите прямой доступ к приложению извне при TRUST_PROXY=1. В многопроцессной конфигурации добавьте общий rate limit на прокси; встроенный лимит локален для процесса.

## Обновление

1. Сделайте резервную копию.
2. Остановите запись данных на время миграций.
3. Обновите исходный код и выполните `docker compose up -d --build`.
4. Проверьте логи `docker compose logs --tail=100 app` и публичные сценарии.

Для запуска без Docker используйте Waitress за HTTPS-прокси: `waitress-serve --listen=127.0.0.1:8000 college.wsgi:application`, отдельно раздавайте STATIC_ROOT после `python manage.py collectstatic`. Не используйте `runserver` в интернете.

## Резервные копии

SQLite:

```powershell
.\venv\Scripts\python.exe manage.py backup_db --directory backups --keep 14
```

Команда использует SQLite Online Backup API, поэтому получает согласованную копию. Рекомендуется ежедневное выполнение и отдельная копия перед массовым импортом. Настройте Планировщик заданий Windows или системный таймер на сервере; укажите абсолютный путь к Python и рабочую папку проекта. Расписание резервирования задаётся средствами ОС, а не неиспользуемым параметром в БД. Храните копии также на другом устройстве.

PostgreSQL вне Docker: команда `backup_db` вызывает установленный `pg_dump`; пароль передаёт через окружение, не выводя в аргументы. В Docker образ приложения не содержит клиента pg_dump, поэтому используйте контейнер БД:

```sh
docker compose exec -T db pg_dump -U schedule -d schedule -Fc > schedule.dump
```

Для бинарных дампов на Windows используйте PowerShell 7.4+ с сохранением бинарного stdout или записывайте файл внутри контейнера и забирайте `docker compose cp`. Проверяйте ненулевой код возврата и размер файла.

## Восстановление

Сначала сделайте отдельную копию текущей БД и остановите приложение. Сохраняйте SECRET_KEY отдельно от копии БД.

SQLite: при остановленном сервере замените `db.sqlite3` выбранной копией `schedule-*.sqlite3`, затем выполните `python manage.py migrate` и `python manage.py check`. Восстановление заменяет все данные текущей БД. Не копируйте файл поверх работающего сервера.

PostgreSQL: остановите app/web, скопируйте дамп в контейнер БД и восстановите:

```sh
docker compose stop app web
docker compose cp schedule.dump db:/tmp/schedule.dump
docker compose exec db pg_restore -U schedule -d schedule --clean --if-exists --no-owner /tmp/schedule.dump
docker compose up -d app web
```

Эта команда заменяет объекты текущей БД. После восстановления проверьте версии, группы, замены и учётную запись администратора. Регулярно проверяйте восстановление в отдельной тестовой базе. Автоматическое удаление старых копий ограничено файлами `schedule-*` в выбранной папке и параметром `--keep`.
