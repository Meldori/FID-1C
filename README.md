# FID-1С

**FID-1С**: навык для ИИ-ассистентов и консольная команда для переноса клиник из 1С в Future IT Dent. Работа идёт в три шага, после каждого — отчёт:

1. **Готовность**: цел ли файл, какая это 1С, всего ли хватает. Вердикт: «можно», «можно с оговорками» или «нельзя».
2. **Загрузка**: в тестовый тенант с нуля. В рабочий — только со свежим дампом и явным согласием.
3. **Итог**: построчная сверка с 1С. По каждой сущности и в целом: «совпало 2 587/2 587 (100%)», плюс метрики (пациенты, счета, оплаты наличными и безналом, записи по месяцам, статусам и врачам, расстановка по креслам) и каждое расхождение.

**Подробно, как всё устроено: [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md). Примеры отчётов: [examples/](examples/).**

Один и тот же навык работает в **Claude** и в **ChatGPT/Codex**: он сделан по открытому формату Agent Skills. Можно обойтись и без ИИ, вся механика — обычная команда `fid1c.py`.

## Установка

```bash
git clone https://github.com/Meldori/FID-1C.git
cd FID-1C
python tools/install.py --all
```

| Ассистент | Куда ставится | Как вызвать |
| --- | --- | --- |
| Claude Code (терминал, десктоп, IDE) | `~/.claude/skills/fid-import-1c` | сам по описанию задачи или `/fid-import-1c` |
| ChatGPT (десктоп-приложение), Codex CLI/IDE | `~/.agents/skills/fid-import-1c` | сам по описанию или `$fid-import-1c` |
| Claude.ai, ChatGPT — загрузка через интерфейс | `python tools/build.py` → `dist/fid-import-1c.zip` | раздел «Навыки» / Skills → загрузить |

Ключи установщика:
- `--claude` или `--agents` — поставить только для одной платформы;
- `--link` — поставить ссылкой на репозиторий, тогда после `git pull` ничего переустанавливать не нужно;
- `--uninstall` — удалить.

В облачных чатах (Claude.ai, ChatGPT в браузере) нет доступа к Future IT Dent. Там работает только проверка выгрузки и сверка по присланным файлам, подробнее в `fid-import-1c/references/platforms.md`.

## Что нужно на компьютере

- Python 3.8+ (без дополнительных пакетов).
- Для загрузки: локальный Future-IT-Dent с доработанным импортом 1С, PHP, MySQL и Redis (OSPanel).
- Для дампа перед рабочей базой: mysqldump.

`fid1c.py doctor` проверяет всё это и подсказывает, что поправить.

## Без ИИ: перенос клиники из консоли

```bash
S=fid-import-1c/scripts
python $S/fid1c.py init "Выгрузка.zip" --clinic "Клиника" --dir C:/OSPanel/home/import-klinika --test-tenant klinika
cd C:/OSPanel/home/import-klinika
python $S/fid1c.py doctor                 # окружение
python $S/fid1c.py readiness              # шаг 1 → reports/readiness.md
python $S/fid1c.py load                   # шаг 2 и 3 → reports/final_klinika.md
python $S/fid1c.py status                 # что сделано
```

Перенос в рабочий тенант, только по согласию клиники:

```bash
python $S/fid1c.py backup --tenant lip
python $S/fid1c.py load --tenant lip --production --backup backups/tenantlip_<время>.sql --yes
```

Сверка дампа с сервера: поднять дамп в локальную базу `chk`, затем `python $S/fid1c.py verify --db chk`.

### Защита от ошибок

| Ситуация | Что делает `fid1c.py` |
| --- | --- |
| Повреждённая выгрузка (файл «начинается заново» после 4 ГиБ) | вердикт «нельзя», разбор не запускается |
| Тестовая загрузка в `lip` или `1` | отказ: это рабочие тенанты (список в `fid1c.json` → `protected_tenants`) |
| Пересоздание тенанта, созданного не `fid1c` | отказ без `--adopt` |
| Рабочий тенант без свежего (до 3 ч) и полного дампа или без `--yes` | отказ |
| В рабочем тенанте уже есть данные из 1С | отказ: повторная заливка задвоила бы счета и оплаты |
| Второй прогон в тот же тенант | отказ, пока идёт первый |
| Ctrl+C посреди шага | PHP останавливается; продолжить этот шаг нельзя (`--resume` откажет), только начать заново |
| Почта и рассылки | на время загрузки отключены, внешние запросы запрещены |

## Устройство

```
fid-import-1c/              навык (Agent Skills: Claude и ChatGPT/Codex)
  SKILL.md                  порядок работы и правила
  agents/openai.yaml        название и подсказка для интерфейса ChatGPT/Codex
  references/               конфигурации 1С, изучение выгрузки, ловушки, перенос, платформы
  scripts/
    fid1c.py                единая команда (шаги, состояние, замок, отчёты)
    fid_bridge.php          действия внутри Future-IT-Dent (тенанты, разбор, загрузка, кресла, выгрузка для сверки)
    check_dump.py           целостность XML
    scan_1c.py              обзор объектов и конфигурация
    reconcile.py            сверка «совпало N/M»
    reports.py              тексты отчётов «Готовность» и «Итог»
  evals/                    тесты поведения ассистента на выдуманных выгрузках
tests/                      автотесты скриптов (python -m unittest discover -s tests)
tools/                      make_fixtures.py, build.py, install.py
```

Папка клиники:

| Что | Где |
| --- | --- |
| Настройки | `fid1c.json`: выгрузка, путь к FID, тестовый тенант, защищённые тенанты, часовой пояс |
| Что сделано | `fid1c_state.json` |
| Отчёты | `reports/` |
| Журналы | `logs/` |
| Дампы | `backups/` |
| Разобранная выгрузка | `work/` |

Код импорта, который навык запускает и при необходимости дорабатывает, живёт в репозитории Future-IT-Dent: `app/Services/Import/ImportFrom1CDBServices.php` и `app/Jobs/Import/I1C/*`.

## Тесты

```bash
python -m unittest discover -s tests          # скрипты: 41 тест, без PHP и базы
python tools/make_fixtures.py                 # пересобрать выдуманные выгрузки
claude plugin eval fid-import-1c --allow-tools Write,Edit,Bash --scaffold   # поведение ассистента
```

GitHub Actions прогоняет автотесты на Windows и Linux (Python 3.8 и 3.12) при каждом push.

Реальные выгрузки клиник в репозиторий не кладутся: в них персональные данные пациентов.
