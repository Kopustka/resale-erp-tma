# Как это развёрнуто

Копии боевых конфигов, чтобы их не приходилось вспоминать по серверу.
Файлы здесь — снимок, правится всё по месту (`/etc/systemd/system`,
`/etc/nginx`), после чего копии обновляются.

## Где что лежит

- Код: `/opt/resale-erp` (реальный каталог). На старом пути
  `/root/projects/менеджер продажи одежды/app` оставлен симлинк сюда —
  привычные пути и заметки продолжают работать.
  Каталог вынесен из `/root` намеренно: `/root` имеет режим 700, и
  сервис, работающий не от root, туда просто не прошёл бы.
- Виртуальное окружение: `/opt/resale-venv`
- Статика фронта: `/var/www/shmotkamanadjer`
- Снимки: `/opt/resale-erp/backend/media` (единственное, куда
  приложение пишет)

## От кого работает

Пользователь `resale` — системный, без оболочки и без домашнего
каталога. Код принадлежит root и сервису только читается: пробой в
приложении не даёт переписать собственный код. `.env` — `root:resale`
640.

`HOME=/run/resale` задан не для красоты: asyncpg при подключении
заглядывает в `~/.postgresql/postgresql.crt` и ловит только «файла
нет». При `ProtectHome=true` оттуда приходит «доступ закрыт», и старт
падал с PermissionError.

## База

Роль `resale`, она же владелец базы `resale` и всех объектов в ней —
владение нужно, потому что приложение само досоздаёт колонки и индексы
на старте. Суперпользователем больше не ходим: ему были видны все девять
баз сервера, включая соседние проекты.

Пароль роли: `/root/.resale-db-pw` (только root).

## nginx

Защитные заголовки вынесены в `snippets/resale-security-headers.conf` и
подключаются в КАЖДОМ location со своим `add_header`. Это не стиль:
`add_header` не наследуется в location, где объявлен свой, и заголовки,
стоявшие на уровне server, молча пропадали на самой странице приложения.

`telegram.org` в `script-src` обязателен — оттуда грузится
`telegram-web-app.js`. Без него мини-апп не стартует, и видно это только
в браузере, curl отдаёт 200.

Демо «модерация Китая» живёт на отдельном поддомене
`china.shmotkamanadjer.duckdns.org`. Со старого `/china/` стоит редирект.
Общий origin означал общие куки и общий CSP: дыра в демо доставала бы до
боевых складов.

## Проверка после изменений

    cd /opt/resale-erp/backend
    for t in admin fields oversight hardening field_limits; do
        env -u BOT_TOKEN /opt/resale-venv/bin/python ${t}_test.py
    done
    env -u BOT_TOKEN /opt/resale-venv/bin/python archive_edit_test.py
    env -u BOT_TOKEN /opt/resale-venv/bin/python upload_test.py

`api_test.py` и `smoke_test.py` НЕ запускать: они делают
`DROP SCHEMA public CASCADE`. Закрыты переменной
`ALLOW_DESTRUCTIVE_TESTS=1`.
