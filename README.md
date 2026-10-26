# fid-1c-import

Навык Claude и инструменты для переноса клиник из 1С в Future IT Dent: проверка выгрузки, отчёт о готовности, загрузка в тестовый тенант, построчная сверка и итог «совпало N/M».

## Состав

```
fid-import-1c/            навык (устанавливается в Claude)
  SKILL.md                порядок работы и формат отчётов
  references/             справочники: две конфигурации 1С, изучение выгрузки, ловушки, перенос на рабочий тенант
  scripts/                check_dump.py, scan_1c.py, fid_run.php, create_tenant.php, pipeline.sh,
                          chairs_by_appointments.php, export_tenant.php, reconcile.py
  evals/                  тесты навыка на синтетических выгрузках (без персональных данных)
tools/make_fixtures.py    генератор синтетических выгрузок для тестов
```

Скрипты работают и без Claude:

```bash
python -I fid-import-1c/scripts/check_dump.py dump.xml
python -I fid-import-1c/scripts/scan_1c.py dump.xml out/
WORK=C:/OSPanel/home/import-klinika/work TENANT=klinika bash fid-import-1c/scripts/pipeline.sh
```

Код импорта, который навык дорабатывает, живёт в репозитории Future-IT-Dent (`app/Services/Import/ImportFrom1CDBServices.php`, `app/Jobs/Import/I1C/*`).

## Тесты

```bash
python tools/make_fixtures.py                       # пересобрать входные файлы
claude plugin eval fid-import-1c --allow-tools Write,Edit,Bash --scaffold
```

Реальные выгрузки клиентов в репозиторий не кладутся: в них персональные данные пациентов.
