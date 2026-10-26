# Перенос в рабочий тенант

Только после того, как тестовый прогон сошёлся с 1С, и только по явному «да» пользователя.

1. **Дамп**: `mysqldump -h <host> -u <user> --single-transaction --routines --default-character-set=utf8mb4 <db> > backups/<db>_before_<что>_<время>.sql`; убедиться, что файл кончается строкой `Dump completed`.
2. **Что в тенанте сейчас**: количество пациентов, счетов, записей, филиалы, кресла. Если там данные другой клиники или уже залитый импорт — спросить пользователя: очистить (откат из дампа «до импорта») или нет. Поверх загруженного не заливать.
3. **Филиал**: импорт занимает первый филиал с пустым `import_id` и переименовывает его. Поставить нужный `time_zone` до загрузки записей.
4. **Почта, рассылки, внешние запросы выключены** (локально это делает `fid_run.php`; на сервере — то же через окружение).
5. **Загрузка** по шагам: `WORK=<work> php scripts/fid_run.php sync:<шаг> <tenant>` для branch, price, price_price, diagnoses, profession, users, employees, patient_cart, patient_passport, dependents, timetables, appointment, invoice_new, payment, payment_cart.
6. **Кресла**: `php scripts/chairs_by_appointments.php <tenant>` (перестраивает расписание врачей по записям).
7. **Подписка**: проверить `tenants.date_end`.
8. **Карточка предприятия** (`legal_entities`): реквизиты из 1С (`Организации`) или от клиники.
9. **Сверка**: `export_tenant.php` + `reconcile.py`, итог пользователю.

Дамп с сервера на проверку: поднять в отдельную локальную базу и сверять `export_tenant.php db:<база> …`.
