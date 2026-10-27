# Как изучать незнакомую выгрузку и дорабатывать импорт

## Порядок изучения

1. `fid1c.py readiness` → `reports/scan/scan.json`: количества по типам, помеченные на удаление, проведённые, период документов и по 2 примера. По набору типов видно конфигурацию.
2. Для каждого объекта, который нужен импорту (см. таблицу в `config-*.md`), найди в `scan.json` пример и выпиши поля: кто пациент, кто врач, где даты, суммы, статусы.
3. После `fid1c.py parse` (или первого `load`) читай `WORK/V8Exch_Data/v8_<Тип>.json` (одна запись = объект; поля с префиксом `v8_`; даты как `2026-03-02T17_00_00`; пустая ссылка `00000000-0000-0000-0000-000000000000`; пустая дата `0001-01-01`). Файл, который не читается как JSON, — обычно HTML в полях: ищи данные в другом объекте.
4. Распределения: `collections.Counter` по статусам, видам оплат, `ТипКонтрагента`, `ВидНоменклатуры`/`ЭтоУслуга`, `ВидЦен`, `DeletionMark`, `Posted`, группам (`Parent` → папка с `IsFolder = true`).
5. Связи: на что ссылается `ДокументОснование` оплаты, в каком поле врач у записи и у счёта, совпадает ли `СуммаДокумента` с суммой строк (если нет — есть ещё табличная часть: `Запасы`, `Товары`).
6. Телефоны, паспорта, цены, графики почти всегда в регистрах сведений (`InformationRegisterRecordSet.*`). Нет регистров — проверь повреждение файла.

## Где что в коде FID

| Что | Файл | Метод |
| --- | --- | --- |
| Разбор XML по типам | `app/Services/Import/ImportFrom1CDBServices.php` | `get_from_1c` |
| Сборщики файлов импорта | там же | `get_branch_from_1c`, `get_price_from_1c`, `get_price_price_from_1c`, `get_users_from_1c`, `get_employees_from_1c`, `get_profession_from_1c`, `get_patient_cart_from_1c`, `get_patient_passport_from_1c`, `get_dependents_from_1c`, `get_timetables_from_1c`, `get_appointment_from_1c` (+ `get_appointment_from_requests`), `get_invoice_new`, `get_payment_from_1c`, `get_payment_cart_from_1c` |
| Порядок загрузки | `app/Services/Import/ImportFrom1CInDBServices.php` | `import` |
| Задачи загрузки | `app/Jobs/Import/I1C/*` | `Calendar/Branch`, `Calendar/Appointment1C` (карта статусов), `Employee/Employees`, `Employee/Profession`, `Patient/Patients`, `Patient/Dependent1C`, `Invoice/InvoiceNew1C`, `Payment/Payment1C`, `Price/Price`, `Price/PricePrice` |

Новый шаг загрузки: сборщик и задача в FID + строка в `STEPS` в `scripts/fid_bridge.php` и `scripts/fid1c.py`.

Сборщик пишет `WORK/array_import/json/<имя>.json`: для «csv»-шагов первая строка — заголовки колонок, задача получает строку как массив «заголовок → значение». Новая колонка = новый заголовок + значение в той же позиции + `$data['Заголовок'] ?? …` в задаче.

## Правила доработки

- Новое поведение включай по наличию объекта или поля в выгрузке (`isset`, `file_exists`), чтобы не сломать другие конфигурации.
- `dd()` в сборщике останавливает весь процесс — заменяй на пропуск с понятным комментарием.
- Задачи импорта «молча» пропускают строку без пациента/врача, а некоторые падают на `null->id`: фильтруй такие строки в сборщике.
- После правки: `php -l`, `fid1c.py load` (тестовый прогон с нуля и сверка). Правки не коммить без просьбы пользователя.
