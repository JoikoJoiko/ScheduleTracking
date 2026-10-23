# HTTPS по IP без домена

Адрес после настройки: **https://185.171.83.215:5555**. Docker остаётся на `127.0.0.1:8080`, PostgreSQL наружу не публикуется. Команды выполняются на Debian 13 из папки ScheduleTracking. Файлы других проектов не заменяются, Nginx не останавливается.

Let's Encrypt выдаёт доверенные IP-сертификаты сроком 160 часов; для `--webroot` нужен Certbot **5.4 или новее**. Поэтому продление автоматическое. Официальные инструкции: [IP и Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot), [проверка через webroot](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot).

## 1. Подготовить файлы

```bash
python3 deploy/prepare.py 185.171.83.215 --scheme https --public-port 5555 --port 8080 --reconfigure
```

`--reconfigure` сохраняет пароль БД, SECRET_KEY и имя Compose-проекта. Новая `.env.nginx` включает HTTPS, Secure cookies, CSRF origin с портом 5555 и доверие только прокси-заголовкам вашего Nginx. DEBUG остаётся выключенным. Конфигурация `deploy/generated/nginx.conf` слушает только 5555 и ссылается на будущий сертификат; **пока не подключайте её**.

## 2. Разрешить только проверку сертификата на существующем порту 80

Публичный `http://185.171.83.215/.well-known/acme-challenge/...` должен доходить до каталога проверки. Порт 5555 для HTTP-01 не подходит. DNS-проверка для IP не применяется.

```bash
sudo install -d -m 755 /var/lib/letsencrypt/schedule/.well-known/acme-challenge
sudo install -m 644 deploy/generated/acme-location.conf /etc/nginx/snippets/schedule-acme.conf
```

В **уже существующий** блок `server`, который обслуживает этот IP на порту 80, добавьте одну строку:

```nginx
include /etc/nginx/snippets/schedule-acme.conf;
```

Это добавляет только `/.well-known/acme-challenge/`, остальные маршруты сохраняются. Комплект не создаёт нового `listen 80`, не выбирает `default_server` и не редактирует чужие конфигурации автоматически. Если добавлять этот путь в существующий сервер нельзя, сначала согласуйте проверку IP через 80 или TLS-ALPN через 443; без доступного канала проверки публичный IP-сертификат получить нельзя. Не останавливайте другие проекты ради `certbot --standalone`.

```bash
sudo nginx -t
sudo systemctl reload nginx
echo schedule-acme-ok | sudo tee /var/lib/letsencrypt/schedule/.well-known/acme-challenge/check
curl --fail http://185.171.83.215/.well-known/acme-challenge/check
```

Проверьте этот URL также с другого подключения. Порты 3333 и 4444 не меняются.

## 3. Получить сертификат

Отдельное окружение Certbot не заменяет системный Python и существующий Certbot других проектов:

```bash
sudo apt update
sudo apt install -y python3-venv
sudo python3 -m venv /opt/schedule-certbot
sudo /opt/schedule-certbot/bin/pip install 'certbot>=5.4'
sudo /opt/schedule-certbot/bin/certbot --version
```

Сначала проверка на тестовом центре (его сертификат браузеры не считают доверенным):

```bash
sudo /opt/schedule-certbot/bin/certbot certonly --staging --non-interactive --agree-tos --email YOUR_EMAIL --preferred-profile shortlived --webroot -w /var/lib/letsencrypt/schedule --ip-address 185.171.83.215 --cert-name schedule-ip-185.171.83.215-staging
```

Замените `YOUR_EMAIL` на свою почту. После успешной проверки получите рабочий сертификат с отдельным именем:

```bash
sudo /opt/schedule-certbot/bin/certbot certonly --non-interactive --agree-tos --email YOUR_EMAIL --preferred-profile shortlived --webroot -w /var/lib/letsencrypt/schedule --ip-address 185.171.83.215 --cert-name schedule-ip-185.171.83.215
```

Ключ хранится в `/etc/letsencrypt/live/schedule-ip-185.171.83.215/privkey.pem`, цепочка — в `fullchain.pem`. Флаг `--nginx -d IP` здесь не используется.

## 4. Переключить только сайт ScheduleTracking на TLS

Сначала обновите приложение, чтобы оно ожидало HTTPS и использовало правильный CSRF origin:

```bash
sudo bash deploy/server.sh update
```

Сохраните копию **текущего файла ScheduleTracking** и замените только его новым `deploy/generated/nginx.conf`. Ниже предполагается, что ваш сайт уже подключён как `/etc/nginx/sites-available/schedule-tracking`; если имя другое, используйте своё. Не копируйте поверх конфигурации другого проекта.

```bash
sudo cp /etc/nginx/sites-available/schedule-tracking /etc/nginx/sites-available/schedule-tracking.before-https
sudo install -m 644 deploy/generated/nginx.conf /etc/nginx/sites-available/schedule-tracking
sudo nginx -t
sudo systemctl reload nginx
curl --fail https://185.171.83.215:5555/healthz
```

Проверьте браузером `/admin/`, `/api/meta`, загрузку `/static/` и вход. При включённом firewall разрешите TCP 5555 и оставьте доступным существующий HTTP-01 маршрут на 80. Приложение на 8080 должно оставаться доступным только локально. Прежний HTTP на 5555 станет HTTPS на том же порту; Nginx обрабатывает случайный HTTP-запрос перенаправлением.

## 5. Включить автоматическое продление

```bash
sudo install -m 644 deploy/generated/schedule-ip-certificate.service /etc/systemd/system/schedule-ip-certificate.service
sudo install -m 644 deploy/generated/schedule-ip-certificate.timer /etc/systemd/system/schedule-ip-certificate.timer
sudo systemctl daemon-reload
sudo systemctl enable --now schedule-ip-certificate.timer
sudo /opt/schedule-certbot/bin/certbot renew --cert-name schedule-ip-185.171.83.215 --dry-run
sudo systemctl list-timers schedule-ip-certificate.timer
```

Таймер раз в час проверяет **только этот сертификат**. После реального продления выполняются `nginx -t` и мягкая перезагрузка Nginx. Ошибки: `sudo journalctl -u schedule-ip-certificate.service`. Служебный путь на 80 должен оставаться доступным для следующих продлений. Сертификаты и ключи не добавляются в Git.
