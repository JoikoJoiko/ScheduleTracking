# Установка за своим Nginx

Нужны Linux, Docker Engine с плагином Compose v2, Python 3.10+ для подготовки конфигурации и домен, направленный на сервер. HTTPS обслуживает ваш Nginx. Docker устанавливается по [инструкции для вашей ОС](https://docs.docker.com/engine/install/).

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

Для последней команды нужен установленный Certbot с плагином Nginx; [инструкция установки](https://certbot.eff.org/instructions?ws=nginx&os=snap). Если панель хостинга управляет сертификатом сама, подключите `location.conf` в её HTTPS-конфигурации. Приложение рассчитано на HTTPS; обычный HTTP перенаправляется на него.

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

Восстанавливайте на версии кода из `revision.txt`. Для нового сервера положите сохранённый `production.env` в `.env.nginx`, выполните `prepare.py` с тем же доменом/портом, затем `up`. На существующем сервере сначала сохраните отдельную свежую копию. Следующие команды **заменяют данные** выбранным архивом:

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

Сценарии генерации конфигурации, резервирования и ошибок проверяются автоматическими тестами. Workflow `.github/workflows/deployment.yml` на Linux поднимает настоящий Docker-стек, проверяет Nginx, HTTPS-заголовки, статику, OCR и резервирование. На локальной машине без Docker сборку контейнеров выполнить нельзя — перед переключением домена запустите `up` и проверьте сайт на своём сервере.

Использованы стандартные механизмы [готовности Docker Compose](https://docs.docker.com/compose/how-tos/startup-order/), [проксирования Nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) и [доверенных заголовков Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/reverse-proxy.html).
