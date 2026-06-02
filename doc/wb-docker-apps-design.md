# wb-docker-apps: единый способ ставить докер-сервисы на Wiren Board

Этот документ — дизайн и обоснование подхода к установке контейнерных
сервисов (Node-RED, далее Home Assistant и прочие утилиты) на контроллеры
Wiren Board поверх уже готового репака Docker (см.
[wb-docker-repack.md](wb-docker-repack.md)). Адресован WB-инженеру, который
будет это реализовывать и сопровождать. Сначала — зачем и какая модель
поставки, затем разобраны все принятые решения с обоснованиями, в конце —
сквозной пример на Node-RED, открытые вопросы и что валидировать на
контроллере.

Статус: **дизайн согласован, реализации ещё нет.** Документ — артефакт
проектной сессии, фиксирующий принятые решения и развилки.

## 1. Задача и контекст

Исходная боль (из фиче-реквеста): Node-RED ставится сложно — надо уметь не
только команды, но и править конфиги; плюс он завязан на версию nodejs,
которую WB контролирует, и эти версии «разъезжаются». Хочется «ставится в
одну-две команды без плясок вокруг конфигов и сервисов».

В реквесте было два пути: (1) собрать нативный `.deb`, совместимый с нашей
nodejs; (2) сделать удобный Docker и ставить сервис в контейнере без оглядки
на системные библиотеки. Путь (2) выбран и уже реализован на уровне Docker
(репак `docker-ce`). Контейнер несёт свою nodejs — боль с версиями исчезает.

Эта работа — **слой поверх Docker**: универсальный способ ставить и первично
настраивать такие сервисы одной командой, с заделом на лёгкое добавление и
сопровождение новых докер-утилит (Home Assistant и др.).

## 2. Обзор архитектуры

Три типа артефактов:

```
┌─ wb-docker-app ──────────────┐   общий пакет-хелпер (один на систему):
│  • CLI: install/remove/...   │   • шаблонный systemd-юнит
│  • shell-библиотека          │   • docker-сеть wb + listener mosquitto
│  • wb-docker-app@.service    │   • генерация nginx server-block'ов
└──────────────────────────────┘   ставится один раз как зависимость

┌─ wb-node-red ────────────────┐   пакеты сервисов (Architecture: all):
│  • base docker-compose.yml   │   • Depends: docker-ce, wb-docker-app
│    (image, ports, labels)    │   • несут только обвязку, не образ
│  • дефолтный конфиг          │   • postinst: wb-docker-app install <app>
│  Depends: docker-ce,         │   • prerm:    wb-docker-app remove  <app>
│           wb-docker-app      │
└──────────────────────────────┘

┌─ образы в registry.wirenboard.com ┐  контейнерные образы:
│  • mirror — побайтово upstream    │  • зеркала ванильных образов
│  • derived — FROM upstream + WB   │  • производные с вшитой WB-интеграцией
└────────────────────────────────────┘  собираются отдельным image-pipeline
```

Пользовательский опыт: `apt install wb-node-red` тянет `docker-ce` и
`wb-docker-app` (если их нет), разворачивает Node-RED в контейнере, поднимает
его за единым логином веб-интерфейса WB. Одна команда.

## 3. Принятые решения

Пятнадцать решений, сгруппированы по темам. Для каждого — суть и почему.

### 3.1 Модель доставки — per-app `.deb` + общий хелпер

Каждый сервис — отдельный маленький нативный `.deb` (`wb-node-red`,
`wb-home-assistant`), `Architecture: all` (внутри только текст: compose,
конфиг, maintainer-скрипты). Не репак (нет upstream-`.deb`, который мы
модифицируем — апстрим это образ) и не чистый мета-пакет (несёт payload).
Общая логика вынесена в отдельный пакет-хелпер `wb-docker-app`.

**Почему:** apt-native, в духе существующего репака; добавить новый сервис =
ещё один `.deb` по шаблону. Альтернатива «менеджер-CLI + удалённый каталог»
менее apt-native и требует сети для каталога; «голые compose-файлы + доки» —
не «одна команда».

### 3.2 Хелпер — отдельный пакет, общая зависимость

`wb-docker-app` ставится один раз, автоматически, через `Depends:`, и
refcount'ится apt'ом (останется, пока от него зависит хоть один сервис; уедет
по `apt autoremove`). Живёт **отдельным пакетом**, не внутри `docker-ce`:
репак остаётся чистым «про сам Docker», бамп логики хелпера не требует
перерелиза `docker-ce`.

**Почему:** хелпер — это и есть материализация «универсального подхода». Вся
хитрая/опасная логика (seed на `/mnt/data`, systemd-обвязка, compose-вызов,
nginx, сеть для MQTT) написана и протестирована **один раз**; багфикс в
жизненном цикле = обновление одного пакета, чинит все сервисы. Без хелпера —
copy-paste в каждый `.deb`, что таска просит избегать.

### 3.3 Источник образов — собственный registry WB

Образы перекладываются в инфраструктуру WB (она есть): явный `pull` →
`tag` → `push` (а не pull-through-кэш). Пакет сервиса ссылается на
`registry.wirenboard.com/.../node-red@sha256:…` (или `:X.Y.Z`).

**Почему:** РФ-угрозовая модель — иностранные registry/сервисы могут отрезать
в любой момент; явный mirror кладёт образ физически к нам и переживает
блокировку/удаление апстрима. Бонусом — воспроизводимость (пин по digest).
Тот же принцип, что уже применён к зеркалу `docker-ce-cli`/`containerd`/
`compose-plugin` в apt-репо.

### 3.4 Mirror vs derived — зеркалим ванильные, собираем производные

- **Mirror** — побайтовый ретег ванильного образа, когда WB-интеграция не
  нужна.
- **Derived** — `FROM upstream` + WB-добавки, когда нужно «из коробки».
  Node-RED → derived: вшиты `node-red-contrib-wirenboard`, `settings.js` и
  дефолтный `flows.json` с broker-нодой на адрес из §3.7.

**Почему:** WB-палет ставится через npm (registry.npmjs.org) — блокируем так
же, как docker.io. Производный образ затягивает npm-зависимость **на сборке**
в наш registry → в рантайме npm не нужен, работает офлайн и суверенно.
Согласуется со всей философией «зеркалим к себе». Цена: Dockerfile + CI +
ребилд на бамп upstream — только для кастомизируемых сервисов.

### 3.5 Рантайм — `docker compose` везде

`docker compose` — единая единица правды для всех сервисов; одноконтейнерный
Node-RED описывается тривиальным compose из одного сервиса, multi-container
HA (с соседями вроде Zigbee2MQTT, БД) — тем же механизмом.

**Почему:** compose несущий для двух других решений — модели override (§3.6,
слияние base+override — встроенная фича compose) и `ExecStart` systemd
(§3.5.1, `compose up -d` пересоздаёт контейнер при изменении конфига). Плагин
`docker-compose-plugin` уже стоит. Один путь кода в хелпере вместо «run для
одиночных, compose для составных».

#### 3.5.1 systemd — один шаблонный юнит в хелпере

Живучесть (крэш-рестарт, переживание ребута) обеспечивает `restart:
unless-stopped` в compose. Сверху — **один** шаблонный юнит
`wb-docker-app@.service` в хелпере (не per-app): `wb-docker-app@node-red`,
`wb-docker-app@home-assistant` — инстансы одного шаблона, `%i` → путь к
compose. `ExecStart=docker compose up -d`, `After=docker.service`.

**Почему:** юнит даёт не живучесть, а **управляемость и наблюдаемость в
родном для WB идиоме**: `systemctl status/restart wb-docker-app@node-red`,
видимость в `wb-diag-collect`, детерминированный порядок на буте, чистый
enable/disable, единый путь «применить новый конфиг». Ценой одного файла на
всю систему, per-app сложность не растёт.

### 3.6 Раскладка на `/mnt/data` — base в пакете + override у пользователя

- `base` (`docker-compose.yml` с image-тегом, портами, labels) — в
  `/usr/lib/wb-docker-app/<app>/`, **собственность пакета**, свободно
  перезаписывается на upgrade.
- `docker-compose.override.yml`, `.env`, каталог `data/` — на
  `/mnt/data/wb-docker-apps/<app>/`, **seed-если-нет**, не conffile, dpkg их
  не отслеживает → никаких conffile-prompt'ов, апгрейд их не трогает.
- Запуск: `docker compose -f base -f override up -d`.
- Данные сервиса (flows Node-RED, config HA) — bind-mount в `data/`.

**Почему:** надо одновременно дать пользователю править конфиг и дать `apt
upgrade` бампать образ/дефолты, не затирая правки. Если бы compose лежал
целиком в пакете (`/usr/lib` non-conffile) — правка пользователя молча
терялась бы на upgrade; если в `/etc` как conffile — на upgrade всплыл бы
dpkg-prompt и под неинтерактивным apt новый тег не применился бы (апдейты
молча застряли). Слой override снимает обе проблемы: base обновляется чисто,
override неприкосновенен.

### 3.7 MQTT-связность — выделенная docker-сеть + listener в хелпере

Хелпер создаёт docker-сеть `wb` с фиксированным subnet/gateway (например
`172.29.0.1`), кладёт mosquitto drop-in с отдельным listener на gateway
(например `listener 11883 172.29.0.1`), делает `restart mosquitto` **один
раз** — при своей установке. Все сервисы цепляются к сети `wb` и ходят на
`172.29.0.1:11883`.

**Почему:** связность контейнер↔брокер — задокументированная боль №1
([тред поддержки](https://support.wirenboard.com/t/home-assistant-i-node-red-ustanovlennye-v-docker-ne-vidyat-mqtt-broker/20922)).
Listener `1883` на части контроллеров забинден только на `127.0.0.1` —
особенно после включения пароля на веб-UI, то есть ровно в нашей конфигурации
с логином. Народный костыль «открыть `1883` на `0.0.0.0`» выставляет брокер
анонимно в LAN. Выделенная сеть + listener на её gateway: детерминированно,
контейнерам видно, в LAN не торчит, не зависит от бинда `1883`.

Перезапуск mosquitto — **не** на каждый сервис: listener и сеть — собственность
хелпера, ставятся один раз; установка второй/третьей утилиты mosquitto не
трогает. `reload` (SIGHUP) не открывает новые listener'ы, поэтому нужен
именно `restart`, но он разовый и до старта любого сервиса; WB-сервисы штатно
переподключаются к брокеру.

Boot-order: mosquitto, забинденный на gateway docker-сети, требует, чтобы
docker поднял сеть раньше старта брокера — решается drop-in'ом
`After=docker.service` для mosquitto (хелпер добавляет его вместе с собой),
либо биндом на `0.0.0.0:<порт>` + iptables-правилом «только из docker-подсети».

### 3.8 Веб-доступ — port-for-all + reverse-proxy + bind на localhost

Контейнер слушает только `127.0.0.1:<внутренний>`. Хелпер генерит **отдельный
nginx server-block** на публичном «родном» порту приложения (`0.0.0.0:1880`
→ proxy на внутренний loopback-порт) — единый механизм для всех сервисов
(«port-for-all»), без subpath-костылей. Внутренний порт аллоцирует хелпер,
label `wb.proxy.port` несёт публичный.

**Почему:** HA категорически не дружит с subpath (`/ha/`) — хочет корень
порта/хоста. Subpath дал бы аккуратный `controller/node-red/`, но потребовал
бы per-app base-path и не работал бы для HA. Port-for-all — один путь кода,
работает для любого приложения; цена — URL вида `controller:ПОРТ` и учёт
портов. (Как следствие, `httpAdminRoot` у Node-RED не нужен — сервис на корне
своего порта.)

### 3.9 Аутентификация — переиспользуем `auth_request` homeui, роль admin

В каждый server-block добавляется `auth_request /auth/check;` с
`set $required_user_type "admin";` — тот же механизм, что защищает `/mqtt`,
`/fwupdate/` и пр. в homeui. Контейнер за единым логином веб-UI WB,
отдельного пароля нет.

**Почему:** логин homeui (роли Administrator/Operator/User, с wb-2602)
enforced на уровне приложения через nginx `auth_request` →
`wb-homeui-backend` (unix-сокет), статика `/` без гейта. Это **переиспользуемо**:
наш server-block с `auth_request` оказывается за тем же логином. Node-RED =
RCE (exec-нода, доступ к шине/GPIO), поэтому гейтим на роль `admin`
(настраивается через label/override). cookie сессии привязан к хосту, не к
порту, поэтому единый логин работает и на отдельном порту.

Полировка: `auth_request` отдаёт голый `401` — нужен `error_page 401` →
редирект на форму логина homeui.

### 3.10 Контракт хелпера — WB-метаданные как labels в compose

Единственный источник правды — base compose; WB-метаданные (proxy-порт,
роль, title) — в `labels`:

```yaml
services:
  node-red:
    image: registry.wirenboard.com/wb/node-red:4.0.2-wb1
    restart: unless-stopped
    networks: [wb]
    ports: ["127.0.0.1:21880:1880"]      # внутренний loopback-порт
    volumes:
      - /mnt/data/wb-docker-apps/node-red/data:/data
    labels:
      wb.app: node-red
      wb.title: "Node-RED"
      wb.proxy.port: "1880"              # публичный порт nginx
      wb.proxy.role: admin               # required_user_type
networks:
  wb:
    external: true
```

Хелпер читает их (`docker compose config` / `docker ps --filter
label=wb.app`), генерит из них nginx server-block и список установленного.
Running-контейнер самоописателен → `wb-docker-app list/status` тривиальны.

CLI: `install/remove` (зовут postinst/prerm), пользовательские
`status/logs/restart/update/list`.

**Почему:** один файл вместо «манифест + compose» (нет рассинхрона, напр.
порта в двух местах); метки несёт сам контейнер → интроспекция в runtime.

### 3.11 Обновления — пин к версии пакета, апгрейд явный

Тег образа пинится в base; обновление = новая версия `.deb` сервиса с
бампнутым тегом. `apt upgrade <сервис>` → новый base → postinst `compose up
-d` подтягивает образ и пересоздаёт контейнер. Эти пакеты **исключены из
unattended-upgrades** — обновление только осознанно пользователем.

**Почему:** контейнерные приложения рискованнее системных пакетов (мажор
Node-RED/HA может сломать пользовательские flows/конфиги, мигрировать данные
вперёд-несовместимо). Авто-апгрейд → неожиданный перезапуск посреди работы
автоматизаций. Явный апгрейд предсказуем. Откат — `apt downgrade` (работает,
если образ ещё в кэше/registry); в доке предупредить: «бэкап
`/mnt/data/wb-docker-apps/<app>/data` перед мажором».

#### 3.11.1 Day-2: эксплуатация, наблюдаемость, откат (issue #5)

Сервис ведёт себя как родной WB-сервис, апгрейд — только осознанно.

**CLI-глаголы хелпера** (тонкая обёртка над модулями A–H). `list` и `status`
интроспектят runtime по меткам `wb.*` контейнеров (self-describing, без
отдельного стейта); остальные оперируют именованным приложением:

```
wb-docker-app list                 # установленные docker-сервисы (по меткам)
wb-docker-app status   <app>       # systemctl status инстанса
wb-docker-app logs     <app>       # journalctl -u wb-docker-app@<app> -n 200
wb-docker-app restart  <app>       # systemctl restart инстанса
wb-docker-app update   <app>       # compose pull + up -d (пересоздать контейнер)
```

Те же действия родным идиомом: `systemctl status/restart
wb-docker-app@<app>` (модуль G).

**Исключение из unattended-upgrades.** Хелпер кладёт
`/etc/apt/apt.conf.d/52wb-docker-app-no-unattended` с
`Unattended-Upgrade::Package-Blacklist` для `wb-docker-app` и пакетов
сервисов (`wb-node-red`, …). Авто-апгрейд их не трогает — обновление только
явным `apt upgrade wb-<сервис>` (его postinst делает `compose pull` + `up
-d`). Каждый новый пакет-сервис при необходимости добавляет своё имя
аналогичным drop-in'ом, если слаг не покрыт уже имеющейся записью.

**Наблюдаемость в `wb-diag-collect`.** Хелпер кладёт drop-in
`/usr/share/wb-diag-collect/conf.d/60wb-docker-app.conf` и сборщик
`/usr/lib/wb-docker-app/diag/wb-docker-app-diag-collect`. Сборщик по меткам
`wb.app` (тот же источник, что у CLI) обходит инстансы и пишет в архив
`systemctl status` и последние логи каждого сервиса; журналы
`wb-docker-app@<app>.service` и так попадают под glob `wb-*.service` основного
конфига. Состояние и логи контейнерных сервисов оказываются в диагностическом
архиве рядом с родными WB-сервисами.

**Откат / downgrade.** Тег образа пинится к версии пакета, поэтому откат
сервиса — это откат пакета. Перед мажорным апгрейдом — бэкап данных:

```
# ⚠️ ПЕРЕД МАЖОРНЫМ АПГРЕЙДОМ сделать бэкап данных приложения:
tar czf /mnt/data/wb-docker-apps/<app>/data-backup-$(date +%F).tgz \
        -C /mnt/data/wb-docker-apps/<app> data

# Откат на предыдущую версию пакета (тянет прежний тег образа):
apt-get install wb-<сервис>=<предыдущая-версия>   # «apt downgrade»
# затем хелпер/postinst пересоздаст контейнер на старом образе:
wb-docker-app update <app>
```

`apt downgrade` работает, пока прежний образ ещё в локальном кэше Docker или в
registry. Мажор может мигрировать данные вперёд-несовместимо — поэтому бэкап
`/mnt/data/wb-docker-apps/<app>/data` обязателен перед мажорным апгрейдом:
после форвардной миграции откат пакета вернёт старый код, но не старые данные.

### 3.12 Нейминг и версионирование

- Имена пакетов: хелпер `wb-docker-app`, сервисы `wb-<service>`.
- Образы: зеркала — upstream-тег побайтово как есть; derived — upstream +
  WB-суффикс (`4.0.2-wb1`), как `+wbNNN` у `docker-ce`.
- Версия пакета сервиса = версия приложения + WB-ревизия (`wb-node-red
  4.0.2-1` везёт образ `:4.0.2(-wbN)`); самодокументирует содержимое,
  согласуется с «тег привязан к версии пакета»; `-N` — WB-правки без бампа
  приложения. Хелпер — свой независимый semver.

### 3.13 Структура репозиториев и сборки

Репо = пакет (WB-конвенция): `wb-docker-app`, `wb-node-red`, … — каждый со
своим Jenkinsfile, едет в apt-репо WB. Два артефакта сборки:
1. `.deb`-пакеты — WB Jenkins;
2. контейнерные образы (derived + зеркала) — **отдельный** image-pipeline
   (`docker build`/ретег + push в WB-registry), в репо сервиса.

## 4. Сквозной пример: Node-RED

```
apt install wb-node-red
  │
  ├─ apt тянет docker-ce (если нет) и wb-docker-app (если нет)
  │
  ├─ configure wb-docker-app  (один раз на систему):
  │     • docker network create wb (172.29.0.0/24, gw 172.29.0.1)
  │     • mosquitto drop-in: listener 11883 172.29.0.1  → restart mosquitto
  │     • кладёт wb-docker-app@.service, CLI, библиотеку
  │
  └─ configure wb-node-red:
        • base compose → /usr/lib/wb-docker-app/node-red/
        • seed override.yml/.env/data/ → /mnt/data/wb-docker-apps/node-red/ (если нет)
        • postinst: wb-docker-app install node-red
              - docker compose pull  (образ из registry.wirenboard.com)
              - nginx server-block на :1880 → 127.0.0.1:21880, auth_request admin
              - systemctl enable --now wb-docker-app@node-red
```

Итог: `http://<контроллер>:1880` за логином веб-UI WB; WB-ноды и broker-нода
(`172.29.0.1:11883`) уже в палитре; flows и конфиг — на `/mnt/data`, переживут
переустановку. Добавить Home Assistant позже = пакет `wb-home-assistant` по
тому же шаблону (compose с labels, derived/mirror-образ), mosquitto и сеть уже
готовы — не трогаются.

## 5. Открытые вопросы и реализационные нюансы

- **`error_page 401` → логин** в генерируемом server-block (иначе голый 401).
- **Аллокация внутреннего loopback-порта** хелпером (реестр, без коллизий).
- **mosquitto boot-order** drop-in (`After=docker.service`) либо firewalled
  `0.0.0.0:<порт>` — выбрать при реализации, подтвердить на контроллере.
- **Точная команда/механизм** вшивания палета и дефолтного `flows.json` в
  derived-образ — сверить с реальным образом `nodered/node-red`.
- **Откат и миграция данных** на мажоре приложения — задокументировать бэкап.
- **HA-специфика**: `trusted_proxies`/`use_x_forwarded_for` в
  `configuration.yaml` при проксировании; набор соседних контейнеров.

## 6. Что валидировать на контроллере (HITL)

Требуется ручная проверка на реальном контроллере (кандидат — `aat3d5fw`,
wb-2602/wb7, уже на репаке `docker-ce`):

1. `auth_request` в стороннем server-block реально гейтит на роль admin и
   отдаёт доступ по cookie homeui.
2. Поведение `reload` vs `restart` mosquitto и bind listener'а на gateway
   docker-сети (в т.ч. на буте).
3. Связность контейнер→брокер на `172.29.0.1:11883` и видимость
   `/devices/#` из Node-RED.
4. Идемпотентность install/upgrade/remove и сохранность `/mnt/data` при
   `apt purge`.

## 7. Ссылки

- [wb-docker-repack.md](wb-docker-repack.md) — репак `docker-ce`, на котором
  всё стоит.
- [Node-RED — WB wiki](https://wiki.wirenboard.com/wiki/Node-RED)
- [Home Assistant — WB wiki](https://wiki.wirenboard.com/wiki/Home_Assistant)
- [Веб-интерфейс (wb-mqtt-homeui)](https://wiki.wirenboard.com/wiki/Wiren_Board_Web_Interface)
- [HA/Node-RED в Docker не видят брокер](https://support.wirenboard.com/t/home-assistant-i-node-red-ustanovlennye-v-docker-ne-vidyat-mqtt-broker/20922)
