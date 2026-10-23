# Установка за своим Nginx

Нужны Linux, Docker Engine с плагином Compose v2 и Python 3.10+ для подготовки конфигурации. По умолчанию используется домен и HTTPS вашего Nginx. Отдельный режим IP + HTTP описан ниже. Docker устанавливается по [инструкции для вашей ОС](https://docs.docker.com/engine/install/).

## Debian 13: IP + HTTP на 185.171.83.215:5555

Это **тестовый режим для доверенной сети**: HTTP не шифрует логины, пароли и сессии. Ограничьте доступ к порту 5555 доверенными IP или VPN средствами имеющегося firewall. Ни скрипты, ни приведённые команды не меняют firewall и не открывают PostgreSQL наружу.

### Получить код и запустить Docker через sudo

```bash
git clone https://github.com/JoikoJoiko/ScheduleTracking.git
cd ScheduleTracking
python3 deploy/prepare.py 185.171.83.215 --scheme http --public-port 5555 --port 8080
sudo bash deploy/server.sh up
sudo bash deploy/server.sh admin
```

Если репозиторий уже склонирован, перейдите в его папку и выполните `git pull --ff-only` вместо `git clone`. При уже существующей `.env.nginx` с параметрами старого HTTPS-запуска сначала выполните:

```bash
python3 deploy/prepare.py 185.171.83.215 --scheme http --public-port 5555 --port 8080 --reconfigure
```

`--reconfigure` сохраняет `SECRET_KEY`, пароль PostgreSQL, имя Compose-проекта и его томы. Не удаляйте `.env.nginx` и не меняйте `SCHEDULE_PROJECT`/пароль БД для смены режима. Если этот файл принадлежит root, запускайте `prepare.py` через `sudo python3`.

Сгенерированные параметры: `DOMAIN=185.171.83.215`, `DEPLOY_SCHEME=http`, `PUBLIC_PORT=5555`, `HOST_PORT=8080`, `PUBLIC_ORIGIN=http://185.171.83.215:5555`. В Django остаётся `DEBUG=0`, `ALLOWED_HOSTS` содержит IP **без порта**, а `CSRF_TRUSTED_ORIGINS` — полный origin **с портом 5555**. CSRF и проверка Origin работают; отключены только HTTPS-перенаправление, Secure у cookies и HSTS. Имена HTTP-cookies `schedule_sessionid` и `schedule_csrftoken` отличаются от стандартных, чтобы не сталкиваться с другими проектами на том же IP. Cookies сами по себе не изолируются по номеру порта.

Docker публикует только `127.0.0.1:8080`; PostgreSQL и Django не публикуют порты. Внешний Nginx будет слушать **только новый порт 5555**. Порты 80, 3333 и 4444 не затрагиваются; `compose.yaml` с Caddy для этого режима не запускайте.

### Добавить отдельный сайт Nginx

Посмотрите `deploy/generated/nginx.conf`: в нём `listen 5555`, `server_name 185.171.83.215` и `proxy_pass http://127.0.0.1:8080`. Host передаётся вместе с `:5555`; X-Forwarded-Proto и X-Forwarded-For перезаписываются внешним Nginx. Все пути, включая `/admin/`, `/api/`, `/static/` и `/healthz`, идут через один прокси.

Следующий блок создаёт **два новых файла** для этого сайта и отказывается перезаписывать существующие. Конфигурации других проектов не редактируются:

```bash
(
  set -e
  site_file=/etc/nginx/sites-available/schedule-ip-5555.conf
  site_link=/etc/nginx/sites-enabled/schedule-ip-5555.conf
  if sudo test -e "$site_file" || sudo test -e "$site_link" || sudo test -L "$site_link"; then
    echo 'schedule-ip-5555.conf already exists; inspect it instead of overwriting.' >&2
    exit 1
  fi
  sudo install -m 644 deploy/generated/nginx.conf "$site_file"
  sudo ln -s "$site_file" "$site_link"
  sudo nginx -t
  sudo systemctl reload nginx
)
```

Это использует стандартные каталоги пакетного Nginx Debian. Если Nginx управляется панелью с иным расположением vhost, добавьте тот же **отдельный** блок `server` через её механизм подключения, сохранив `listen 5555`; существующие сайты менять не нужно.

### Проверить

На сервере:

```bash
sudo bash deploy/server.sh status
curl -fsS -H 'Host: 185.171.83.215:5555' http://127.0.0.1:5555/healthz
curl -fsS -H 'Host: 185.171.83.215:5555' http://127.0.0.1:5555/api/meta > /dev/null
curl -fsS -H 'Host: 185.171.83.215:5555' http://127.0.0.1:5555/static/app.js > /dev/null
```

`/healthz` возвращает `{"status":"ok"}`. В браузере откройте **http://185.171.83.215:5555/admin/** и войдите созданным администратором. Перенаправления на HTTPS нет. Если браузер сохранил прежние настройки сайта, удалите cookies для IP перед повторным входом.

Обновление, резервирование и журналы при Docker через sudo:

```bash
git pull --ff-only
sudo bash deploy/server.sh update
sudo bash deploy/server.sh backup
sudo bash deploy/server.sh logs
```

В HTTP-режиме команда `check --deploy --fail-level WARNING` подавляет только ожидаемые TLS-предупреждения W004/W008/W012/W016. Проверки CSRF middleware, секретного ключа, DEBUG и остальных защит сохраняются; при старте печатается предупреждение о незащищённом HTTP.

## HTTPS с доменом (режим по умолчанию)

## 1. Загрузить проект и запустить

Распакуйте архив проекта на сервере и перейдите в его папку. Либо клонируйте свой Git-репозиторий. Затем:

```bash
python3 deploy/prepare.py schedule.example.org
bash deploy/server.sh up
bash deploy/server.sh admin
```

Вместо `schedule.example.org` укажите свой домен. Последняя команда создаёт администратора и запрашивает его логин/пароль. Тестовый `admin/admin` автоматически не создаётся.

Секреты генерируются в `.env.nginx`; повторная подготовка с теми же параметрами сохраняет их. Сервис занимает **только `127.0.0.1:8080`**. Если порт занят, используйте `python3 deploy/prepare.py ваш-домен --port 9080` при первой подготовке. Nginx сервера продолжает владеть портами 80/443.

Состав: PostgreSQL → Django/Waitress и внутренний Nginx для статики. Миграции, сборка статики, проверки готовности, перезапуск после перезагрузки сервера и OCR с русским языком уже включены. Изображения замен выдаются только через защищённые страницы админки.

## 2. Подключить Nginx

**Если домен и HTTPS уже настроены:** откройте `deploy/generated/location.conf` и перенесите его содержимое в HTTPS-блок `server` своего сайта. Замените существующий `location /`, чтобы не создавать два одинаковых блока. Эти настройки должны применяться и к `/admin/`, `/api/`, `/static/`: уберите прежние перехватывающие `location` для этих путей. Затем:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

**Если создаёте новый сайт**, на Debian/Ubuntu:

```bash
sudo install -m 644 deploy/generated/nginx.conf /etc/nginx/sites-available/schedule.conf
sudo ln -s /etc/nginx/sites-available/schedule.conf /etc/nginx/sites-enabled/schedule.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d schedule.example.org --redirect
```

Для последней команды нужен установленный Certbot с плагином Nginx; [инструкция установки](https://certbot.eff.org/instructions?ws=nginx&os=snap). Если панель хостинга управляет сертификатом сама, подключите `location.conf` в её HTTPS-конфигурации. В режиме по умолчанию обычный HTTP перенаправляется на HTTPS, cookies имеют Secure и включён HSTS.

Откройте `https://ваш-домен/admin/`, войдите созданным пользователем и загрузите XLSX. Проверка: `https://ваш-домен/healthz` должна вернуть `{"status":"ok"}`.

## Команды после установки

```bash
bash deploy/server.sh status
bash deploy/server.sh logs
bash deploy/server.sh manage check --deploy
bash deploy/server.sh backup
```

Обновление: сначала получите новый код (`git pull --ff-only`, если используете Git, либо замените исходные файлы новой версией архива), сохранив `.env.nginx`, затем:

```bash
bash deploy/server.sh update
```

Сначала собирается новый образ, затем сохраняются база, оригиналы загрузок, секреты и версия кода. Запись ненадолго останавливается для согласованной копии; затем применяются миграции и проверяется готовность. При ошибке сборки работающий сайт сохраняется. При ошибке резервирования сервисы возобновляются, обновление прерывается. Ошибка миграции/старта требует просмотра `logs`; автоматический откат схемы не выполняется.

Копии лежат в `backups/server/ДАТА/`; только каталог с файлом `COMPLETE` считается законченным. Они содержат пароли, поэтому держите папку закрытой и копируйте её на другое устройство. Томы базы и изображений сохраняются при пересоздании контейнеров. `bash deploy/server.sh stop` останавливает проект, сохраняя данные.

### Восстановление

Восстанавливайте на версии кода из `revision.txt`. Для нового сервера положите сохранённый `production.env` в `.env.nginx`, выполните `prepare.py` с тем же адресом, режимом и портами, затем `up`. Для HTTP это `python3 deploy/prepare.py 185.171.83.215 --scheme http --public-port 5555 --port 8080`. На существующем сервере сначала сохраните отдельную свежую копию. Следующие команды **заменяют данные** выбранным архивом; если Docker требует sudo, добавьте `sudo` первым элементом массива `dc`:

```bash
backup_dir=/полный/путь/к/законченной/копии
test -f "$backup_dir/COMPLETE" || { echo 'Incomplete backup'; exit 1; }
dc=(docker compose --env-file .env.nginx -f compose.nginx.yaml)
"${dc[@]}" stop web app
"${dc[@]}" exec -T db pg_restore -U schedule -d schedule --clean --if-exists --no-owner --exit-on-error < "$backup_dir/database.dump"
# Use a fresh media volume or restore alongside existing originals; DB controls access.
"${dc[@]}" run --rm --no-deps --entrypoint tar app -C /app/media -xzf - < "$backup_dir/media.tar.gz"
"${dc[@]}" up -d --wait --wait-timeout 240
```

Ранее оставшиеся оригиналы в томе media не публикуются и не заменяют записи восстановленной БД. Не используйте `docker compose down -v` на рабочем сервере: это удаляет томы с данными.

Локальная SQLite-база не включается в архив релиза. Самый простой перенос расписания — повторно загрузить четыре Excel через админку; для переноса истории замен и пользователей нужен отдельный перенос данных SQLite → PostgreSQL.

## Проверка комплекта

Сценарии генерации конфигурации, резервирования и ошибок проверяются автоматическими тестами. Тесты с `DEBUG=0` проверяют реальный вход в админку, cookies и CSRF для обоих режимов. Workflow `.github/workflows/deployment.yml` на Linux поднимает Docker-стек в матрице HTTPS/HTTP, проверяет Nginx, статику, OCR и резервирование. В HTTP-прогоне дополнительный внешний Nginx слушает 5555 и проверяется вместе с обеими ступенями прокси, портом в Host и CSRF Origin. На локальной машине без Docker сборку контейнеров выполнить нельзя — перед включением сайта запустите `up` и проверьте его на своём сервере.

Использованы стандартные механизмы [готовности Docker Compose](https://docs.docker.com/compose/how-tos/startup-order/), [проксирования Nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) и [доверенных заголовков Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/reverse-proxy.html).
## nodecor.ru и www.nodecor.ru по HTTP вместе с IP

В существующей схеме доменный Nginx на 80 направляет запросы на `127.0.0.1:5555`, а сайт на 5555 — на Docker `127.0.0.1:8080`. В `compose.nginx.yaml` оба домена добавляются к `DOMAIN` и необязательному списку `ALLOWED_HOSTS` из окружения. CSRF origins складываются из `PUBLIC_ORIGIN`, существующего `CSRF_TRUSTED_ORIGINS` и origins обоих доменов с текущим `DEPLOY_SCHEME`. При `DEPLOY_SCHEME=http` HTTPS-перенаправления и Secure cookies не включаются. Прежний IP и порт сохраняются.

Для этого обновления **не запускайте prepare.py и не заменяйте .env.nginx**: действующие секреты, порты, схема и имя Compose-проекта остаются прежними. Другие конфигурации Nginx менять не требуется. `.env.example` содержит примеры, а штатный сценарий использует именно `.env.nginx`. В этом изменении нет миграций и изменений бизнес-логики.

После публикации коммита выполните на VPS из существующей папки проекта:

```bash
cd /путь/к/ScheduleTracking
git pull --ff-only
sudo bash deploy/server.sh update
sudo bash deploy/server.sh status
```

`update` собирает образ, делает штатную резервную копию и пересоздаёт сервисы с новым окружением, сохраняя томы. Обычный `docker restart` не применяет изменения переменных Compose. Не меняйте имя папки проекта или `SCHEDULE_PROJECT`.

Проверка фактически применённых списков (секреты не выводятся):

```bash
sudo bash deploy/server.sh manage shell -c "from django.conf import settings; print('ALLOWED_HOSTS:', settings.ALLOWED_HOSTS); print('CSRF_TRUSTED_ORIGINS:', settings.CSRF_TRUSTED_ORIGINS); print('DEBUG:', settings.DEBUG, 'HTTPS redirect:', settings.SECURE_SSL_REDIRECT)"
```

В списках должны остаться прежние хосты/origins и появиться `nodecor.ru`, `www.nodecor.ru`, `http://nodecor.ru`, `http://www.nodecor.ru`. `DEBUG` и `HTTPS redirect` — `False`. Проверки с VPS:

```bash
curl --fail --show-error -H 'Host: nodecor.ru' http://127.0.0.1:8080/healthz
curl --fail --show-error -H 'Host: www.nodecor.ru' http://127.0.0.1:8080/healthz
curl --fail --show-error http://185.171.83.215:5555/healthz
curl --fail --show-error http://nodecor.ru/healthz
curl --fail --show-error http://www.nodecor.ru/healthz
for base in http://185.171.83.215:5555 http://nodecor.ru http://www.nodecor.ru; do
    for path in / /admin/login/ /api/meta /static/app.js; do
        curl --fail --show-error --silent --output /dev/null --write-out '%{http_code} %{url_effective}\n' "$base$path"
    done
done
```

`/healthz` должен вернуть `{"status":"ok"}`, остальные проверки — 200. Также проверьте вход в админку браузером с обоих доменов. Если локальная проверка с `Host` проходит, а внешний домен возвращает 400, проверьте выбранный виртуальный сервер и передачу `Host` в существующей конфигурации **этого** сайта: обе ступени должны сохранять `proxy_set_header Host $http_host;`. Внешняя ступень должна устанавливать `proxy_set_header X-Forwarded-Proto $scheme;`. В доменном блоке на 80 должно быть `server_name nodecor.ru www.nodecor.ru;`: если `www` ещё не указан, добавьте только этот алиас в конфигурацию данного сайта, затем выполните `sudo nginx -t` и `sudo systemctl reload nginx`. Не заменяйте конфигурации соседних проектов. Для `www` также нужна действующая DNS-запись.

## HTTPS без домена

Для публичного IP и HTTPS на `:5555` используйте [пошаговую инструкцию](INSTALL-IP-HTTPS.md). Она сохраняет локальный Docker на 8080 и предусматривает автоматическое продление IP-сертификата без остановки других проектов.
