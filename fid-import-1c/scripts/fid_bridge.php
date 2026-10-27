<?php
// Мост между fid1c.py и Future IT Dent: одна команда — одно действие в программе, без HTTP и без tinker.
//   php fid_bridge.php <команда> [аргументы]
// Окружение: FID — путь к Future-IT-Dent, WORK — рабочая папка выгрузки (с V8Exch_Data/),
//            FID1C_PROTECTED — тенанты через запятую, которые нельзя удалять и пересоздавать.
// Последняя строка вывода — «@@FID1C {json}»: по ней fid1c.py читает результат.
//
// Команды:
//   info                         окружение: база, Redis, тенанты, доработан ли импорт
//   dbconf                       параметры подключения к MySQL (для mysqldump; пароль не печатается в лог)
//   counts <tenant>              сколько загруженных из 1С строк уже лежит в тенанте
//   create-tenant <id> [образец] тестовый тенант с модулями и правами образца, подписка до 2222, метка fid1c_test
//   drop-tenant <id> [--adopt]   удалить тестовый тенант (только с меткой fid1c_test, иначе нужен --adopt)
//   extend <tenant> <дата>       продлить подписку (tenants.date_end)
//   parse <dump.xml>             разобрать XML по типам в WORK/V8Exch_Data
//   build <get_метод>            собрать файл импорта WORK/array_import/json/*.json
//   sync <шаг> <tenant>          загрузить файл шага в тенант синхронно, ошибки — в WORK/errors_<шаг>.log
//   set-timezone <tenant> <tz>   часовой пояс филиалов из 1С (до загрузки записей)
//   chairs <tenant>              разложить записи по креслам без наложений и пересобрать расписание врачей
//   export <tenant|db:база> <out> выгрузить загруженное в JSON для сверки (только чтение)
//   requisites <tenant> [--from-1c] [--org <ref|ИНН>] [--dry-run]
//                                реквизиты предприятия: карточка (legal_entities) → настройки (app_settings),
//                                с --from-1c карточка сначала заполняется из справочника «Организации» выгрузки

$fid = rtrim(str_replace('\\', '/', getenv('FID') ?: 'C:/OSPanel/home/Future-IT-Dent'), '/');
if (!file_exists($fid.'/vendor/autoload.php')) {
    fwrite(STDERR, "Не найден Future-IT-Dent в $fid (задайте FID)\n");
    exit(3);
}
require $fid.'/vendor/autoload.php';
$app = require $fid.'/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
ini_set('memory_limit', getenv('FID1C_MEMORY') ?: '8G');
error_reporting(E_ALL & ~E_DEPRECATED);

use App\Models\Tenant;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;

// Локальный прогон: никаких писем, пушей и внешних запросов реальным людям
config([
    'mail.default' => 'log',
    'broadcasting.default' => 'log',
    'queue.default' => 'sync',
]);
Illuminate\Support\Facades\Http::preventStrayRequests();

function result(array $data, int $code = 0): never
{
    echo "\n@@FID1C ".json_encode($data, JSON_UNESCAPED_UNICODE)."\n";
    exit($code);
}

function fail(string $message, int $code = 1): never
{
    fwrite(STDERR, $message."\n");
    result(['ok' => false, 'error' => $message], $code);
}

function work(): string
{
    $work = getenv('WORK') ?: fail('Задайте WORK — рабочую папку выгрузки (с V8Exch_Data/)', 2);
    return rtrim(str_replace('\\', '/', $work), '/').'/';
}

function protected_tenant(string $id): bool
{
    $list = array_filter(array_map('trim', explode(',', getenv('FID1C_PROTECTED') ?: '')));
    return in_array($id, $list, true);
}

function tenant_db(string $id): string
{
    return config('tenancy.database.prefix').$id.config('tenancy.database.suffix');
}

function open_tenant(string $id): Tenant
{
    $tenant = Tenant::find($id) ?: fail("Тенанта $id нет");
    tenancy()->initialize($tenant);
    // Часовой пояс и филиал кэшируются в Redis на 3 суток: сбрасываем только ключи этого тенанта
    foreach (['time_zone_', 'branch_id_'] as $key) {
        try {
            Cache::store('redis')->forget($key.$id.'_0');
        } catch (Throwable $e) {
        }
    }
    return $tenant;
}

// Шаги загрузки в порядке ImportFrom1CInDBServices::import(): шаг => [задача, формат файла, сборщик]
const STEPS = [
    'branch' => ['App\Jobs\Import\I1C\Calendar\Branch', 'csv', 'get_branch_from_1c'],
    'price' => ['App\Jobs\Import\I1C\Price\Price', 'csv', 'get_price_from_1c'],
    'price_price' => ['App\Jobs\Import\I1C\Price\PricePrice', 'csv', 'get_price_price_from_1c'],
    'diagnoses' => ['App\Jobs\Import\I1C\Diagnose\Diagnoses', 'csv', 'get_diagnoses_from_1c'],
    'profession' => ['App\Jobs\Import\I1C\Employee\Profession', 'csv', 'get_profession_from_1c'],
    'users' => ['App\Jobs\Import\I1C\Patient\Patients', 'csv', 'get_users_from_1c'],
    'employees' => ['App\Jobs\Import\I1C\Employee\Employees', 'csv', 'get_employees_from_1c'],
    'patient_cart' => ['App\Jobs\Import\I1C\Patient\PatientCart1C', 'csv', 'get_patient_cart_from_1c'],
    'patient_passport' => ['App\Jobs\Import\I1C\Patient\Passport', 'csv', 'get_patient_passport_from_1c'],
    'dependents' => ['App\Jobs\Import\I1C\Patient\Dependent1C', 'csv', 'get_dependents_from_1c'],
    'timetables' => ['App\Jobs\Import\I1C\Calendar\Timetables', 'csv', 'get_timetables_from_1c'],
    'appointment' => ['App\Jobs\Import\I1C\Calendar\Appointment1C', 'csv', 'get_appointment_from_1c'],
    'invoice_new' => ['App\Jobs\Import\I1C\Invoice\InvoiceNew1C', 'json', 'get_invoice_new'],
    'payment' => ['App\Jobs\Import\I1C\Payment\Payment1C', 'csv', 'get_payment_from_1c'],
    'payment_cart' => ['App\Jobs\Import\I1C\Payment\PaymentCart', 'csv', 'get_payment_cart_from_1c'],
];

// Ключи app_settings с реквизитами предприятия ↔ колонки карточки legal_entities. Ключи с паролями и токенами не трогаем.
const REQUISITES = [
    'company_name' => 'company_name', 'kpp' => 'kpp', 'inn' => 'inn', 'ogrn' => 'ogrn', 'ur_address' => 'ur_address',
    'rchet' => 'rchet', 'kor' => 'kor', 'bik' => 'bik', 'okpo' => 'okpo', 'okved' => 'okved', 'phone' => 'phone',
    'email' => 'email', 'gen_dir' => 'gen_dir',
];

function one_c_list(string $file): array
{
    if (!file_exists($file)) return [];
    $d = json_decode(file_get_contents($file), true);
    if (!is_array($d)) return [];
    $d = array_values($d)[0] ?? [];
    return isset($d['v8_Ref']) ? [$d] : (is_array($d) ? $d : []);
}

/** Реквизиты организации из 1С: имя, ИНН, КПП, ОГРН, ОКПО и первые адрес, телефон, email из контактной информации. */
function org_requisites(array $o): array
{
    $text = fn ($v) => is_string($v) ? trim($v) : '';
    $out = [
        'company_name' => $text($o['v8_ПолноеНаименование'] ?? '') ?: $text($o['v8_Description'] ?? ''),
        'inn' => $text($o['v8_ИНН'] ?? ''), 'kpp' => $text($o['v8_КПП'] ?? ''), 'ogrn' => $text($o['v8_ОГРН'] ?? ''),
        'okpo' => $text($o['v8_КодПоОКПО'] ?? ''),
    ];
    // в выгрузке 1С кавычки названия бывают одинарными: 'ДЕНТАЛ АСК' → «ДЕНТАЛ АСК» не угадываем, берём как есть
    $contacts = $o['v8_КонтактнаяИнформация'] ?? [];
    $contacts = isset($contacts['v8_Тип']) ? [$contacts] : (is_array($contacts) ? $contacts : []);
    foreach ($contacts as $c) {
        $type = $text($c['v8_Тип'] ?? '');
        $view = $text($c['v8_Представление'] ?? '');
        if ($type === 'Телефон' && empty($out['phone'])) $out['phone'] = $text($c['v8_НомерТелефона'] ?? '') ?: $view;
        if ($type === 'АдресЭлектроннойПочты' && empty($out['email'])) $out['email'] = $text($c['v8_АдресЭП'] ?? '') ?: $view;
        if ($type === 'Адрес' && empty($out['ur_address'])) $out['ur_address'] = $view;
    }
    return array_filter($out, fn ($v) => $v !== '');
}

$cmd = $argv[1] ?? 'help';
$args = array_slice($argv, 2);
$start = microtime(true);

switch ($cmd) {
    case 'info':
        $info = ['ok' => true, 'fid' => $fid, 'php' => PHP_VERSION, 'db' => false, 'redis' => false, 'tenants' => []];
        try {
            DB::select('select 1');
            $info['db'] = true;
            foreach (Tenant::query()->with('domains')->get() as $t) {
                $info['tenants'][] = [
                    'id' => (string) $t->id,
                    'domains' => $t->domains->pluck('domain')->all(),
                    'date_end' => $t->date_end ?? null,
                    'test' => (bool) ($t->fid1c_test ?? false),
                ];
            }
        } catch (Throwable $e) {
            $info['db_error'] = $e->getMessage();
        }
        try {
            Cache::store('redis')->get('fid1c_ping');
            $info['redis'] = true;
        } catch (Throwable $e) {
            $info['redis_error'] = $e->getMessage();
        }
        $svc = 'App\Services\Import\ImportFrom1CDBServices';
        $info['importer'] = [
            'base' => class_exists($svc),
            // методы, которые добавляют доработки импорта для обеих конфигураций 1С
            'dependents' => method_exists($svc, 'get_dependents_from_1c'),
            'requests' => method_exists($svc, 'get_appointment_from_requests'),
            'sold_goods' => method_exists($svc, 'get_sold_goods'),
        ];
        $info['steps'] = array_keys(STEPS);
        $info['tenant_db_prefix'] = config('tenancy.database.prefix');
        result($info);

    case 'dbconf':
        $c = config('database.connections.'.config('database.default'));
        result(['ok' => true, 'host' => $c['host'], 'port' => (string) $c['port'], 'user' => $c['username'],
            'password' => $c['password'], 'central' => $c['database'], 'prefix' => config('tenancy.database.prefix'),
            'suffix' => config('tenancy.database.suffix')]);

    case 'counts':
        $id = $args[0] ?? fail('counts <tenant>', 2);
        open_tenant($id);
        $imp = fn ($t) => DB::table($t)->whereNotNull('import_id')->where('import_id', '!=', '')->count();
        result(['ok' => true, 'tenant' => $id, 'db' => tenant_db($id), 'counts' => [
            'patients' => $imp('patients'), 'doctors' => $imp('doctors'), 'employees' => $imp('employees'),
            'services' => $imp('services'), 'invoice' => $imp('invoice'),
            'appointments' => DB::table('appointments')->count(), 'money_operations' => DB::table('money_operations')->count(),
            'branches' => DB::table('branches')->count(), 'chairs' => DB::table('chairs')->where('active', 1)->count(),
        ]]);

    case 'create-tenant':
        $id = $args[0] ?? fail('create-tenant <id> [образец]', 2);
        $source = $args[1] ?? '1';
        if (!preg_match('/^[a-z0-9_-]{1,40}$/i', $id)) fail("Недопустимый id тенанта: $id", 2);
        if (Tenant::find($id)) fail("Тенант $id уже есть", 4);
        $db = tenant_db($id);
        DB::statement("CREATE DATABASE IF NOT EXISTS `$db` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");
        $modules = App\Models\Tenant\ModulesTenants::where('tenant', '=', $source)->pluck('module_id')->all();
        tenancy()->initialize(Tenant::find($source) ?: fail("Нет тенанта-образца $source"));
        $rights = App\Models\Permission\Permissions::where('active', 1)->pluck('slug')->all();
        tenancy()->end();
        App\Jobs\Tenant\CreateTenant::dispatchSync([
            'create' => ['id' => $id],
            'domain' => $id.'.localhost',
            'modules' => $modules,
            'right' => $rights,
            'addmissiion' => [],
        ]);
        $tenant = Tenant::find($id) ?: fail('Тенант не создался');
        // без подписки программа пишет «Срок действия вашей программы истёк»
        $tenant->date_end = '2222-02-02';
        $tenant->fid1c_test = true;
        $tenant->save();
        echo "Создан тестовый тенант $id ($db), модулей ".count($modules).', прав '.count($rights)."\n";
        result(['ok' => true, 'tenant' => $id, 'db' => $db, 'domain' => $id.'.localhost', 'modules' => count($modules)]);

    case 'drop-tenant':
        $id = $args[0] ?? fail('drop-tenant <id> [--adopt]', 2);
        if (protected_tenant($id)) fail("Тенант $id в списке защищённых — удалять его нельзя", 5);
        $tenant = Tenant::find($id);
        if ($tenant && !($tenant->fid1c_test ?? false) && !in_array('--adopt', $args, true)) {
            fail("Тенант $id создан не fid1c (нет метки fid1c_test). Удаление только с --adopt и после проверки, что это тестовый тенант", 5);
        }
        if ($tenant) $tenant->delete();
        DB::statement('DROP DATABASE IF EXISTS `'.tenant_db($id).'`');
        echo "Тестовый тенант $id удалён\n";
        result(['ok' => true, 'tenant' => $id]);

    case 'extend':
        [$id, $date] = [$args[0] ?? fail('extend <tenant> <YYYY-MM-DD>', 2), $args[1] ?? '2222-02-02'];
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) fail("Дата в формате ГГГГ-ММ-ДД: $date", 2);
        $tenant = Tenant::find($id) ?: fail("Тенанта $id нет");
        $old = $tenant->date_end ?? null;
        $tenant->date_end = $date;
        $tenant->save();
        result(['ok' => true, 'tenant' => $id, 'date_end_before' => $old, 'date_end' => $date]);

    case 'parse':
        $dump = $args[0] ?? fail('parse <dump.xml>', 2);
        if (!is_file($dump)) fail("Нет файла $dump", 2);
        $work = work();
        @mkdir($work, 0777, true);
        $out = App\Services\Import\ImportFrom1CDBServices::get_from_1c($dump, $work);
        $files = glob($work.'V8Exch_Data/*.json') ?: [];
        echo "Разобрано типов: ".count($files)."\n";
        result(['ok' => count($files) > 0, 'types' => count($files), 'seconds' => round(microtime(true) - $start, 1), 'message' => is_string($out) ? mb_substr($out, 0, 500) : null], count($files) ? 0 : 1);

    case 'build':
        $method = $args[0] ?? fail('build <get_метод>', 2);
        if (!str_starts_with($method, 'get_') || !method_exists('App\Services\Import\ImportFrom1CDBServices', $method)) {
            fail("Нет сборщика $method в ImportFrom1CDBServices", 2);
        }
        $rows = App\Services\Import\ImportFrom1CDBServices::$method(work());
        $n = is_array($rows) ? max(count($rows) - 1, 0) : null;
        echo "$method: ".($n ?? 'ok')." строк\n";
        result(['ok' => true, 'method' => $method, 'rows' => $n, 'seconds' => round(microtime(true) - $start, 1)]);

    case 'sync':
        [$name, $id] = [$args[0] ?? fail('sync <шаг> <tenant>', 2), $args[1] ?? fail('sync <шаг> <tenant>', 2)];
        if (!isset(STEPS[$name])) fail("Нет шага $name; шаги: ".implode(', ', array_keys(STEPS)), 2);
        [$class, $type] = STEPS[$name];
        $patch = work();
        $file = $patch.'array_import/json/'.$name.'.json';
        if (!file_exists($file)) {
            echo "$name: файла нет, пропуск\n";
            result(['ok' => true, 'step' => $name, 'skipped' => true, 'rows' => 0, 'loaded' => 0, 'failed' => 0]);
        }
        open_tenant($id);
        $data = json_decode(file_get_contents($file), true);
        if (!is_array($data)) fail("$file не читается как JSON");
        $ok = $fail = 0;
        $errors = [];
        for ($i = $type === 'csv' ? 1 : 0; $i < count($data); $i++) {
            $row = $data[$i];
            if ($type === 'csv') {
                $row = [];
                foreach ($data[0] as $key => $value) {
                    if (isset($data[$i][$key])) $row[$value] = $data[$i][$key];
                }
            }
            try {
                (new $class($row))->handle();
                $ok++;
            } catch (Throwable $e) {
                $fail++;
                $msg = $e->getMessage().' @ '.basename($e->getFile()).':'.$e->getLine();
                $errors[$msg][] = $i;
            }
        }
        $log = '';
        foreach ($errors as $msg => $rows) $log .= count($rows).'x '.$msg.' | строки: '.implode(',', array_slice($rows, 0, 10))."\n";
        file_put_contents($patch.'errors_'.$name.'.log', $log);
        echo "$name: загружено $ok, ошибок $fail\n".mb_substr($log, 0, 2000);
        result(['ok' => true, 'step' => $name, 'rows' => $ok + $fail, 'loaded' => $ok, 'failed' => $fail,
            'errors' => array_slice(array_map(fn ($m, $r) => count($r).'x '.$m, array_keys($errors), $errors), 0, 10),
            'seconds' => round(microtime(true) - $start, 1)]);

    case 'set-timezone':
        [$id, $tz] = [$args[0] ?? fail('set-timezone <tenant> <tz>', 2), $args[1] ?? fail('set-timezone <tenant> <tz>', 2)];
        if (!in_array($tz, timezone_identifiers_list(), true)) fail("Неизвестный часовой пояс $tz", 2);
        open_tenant($id);
        $n = DB::table('branches')->whereNotNull('import_id')->where('import_id', '!=', '')->update(['time_zone' => $tz]);
        open_tenant($id); // сброс кэша пояса после смены
        result(['ok' => true, 'tenant' => $id, 'time_zone' => $tz, 'branches' => $n]);

    case 'chairs':
        $id = $args[0] ?? fail('chairs <tenant> [сколько кресел]', 2);
        $want = isset($args[1]) ? (int) $args[1] : 0;
        open_tenant($id);
        // У записи нет филиала, только кресло: берём кресла филиалов из 1С, иначе записи уедут в чужой филиал
        $branches = DB::table('branches')->whereNotNull('import_id')->where('import_id', '!=', '')->orderBy('id')->pluck('id')->all();
        if (!$branches) $branches = DB::table('branches')->orderBy('id')->limit(1)->pluck('id')->all();
        $created = $disabled = 0;
        if ($want > 0) {
            // ровно $want активных кресел в филиале из 1С: лишние выключаются, недостающие создаются
            $have = DB::table('chairs')->where('branch_id', $branches[0])->orderBy('id')->get(['id', 'active']);
            foreach ($have as $i => $c) {
                $on = $i < $want ? 1 : 0;
                if ($c->active != $on) {
                    DB::table('chairs')->where('id', $c->id)->update(['active' => $on, 'updated_at' => now()]);
                    $disabled += $on ? 0 : 1;
                }
            }
            for ($i = count($have); $i < $want; $i++) {
                DB::table('chairs')->insert(['name' => 'Кресло '.($i + 1), 'branch_id' => $branches[0], 'active' => 1, 'import_id' => '', 'created_at' => now(), 'updated_at' => now()]);
                $created++;
            }
        }
        $chairs = DB::table('chairs')->where('active', 1)->whereIn('branch_id', $branches)->orderBy('id')->pluck('id')->all();
        if (!$chairs) fail('В филиале из 1С нет активных кресел — задайте "chairs" в fid1c.json');
        // трогаем только записи пациентов из 1С: в рабочем тенанте могут быть записи, сделанные в программе
        $imported = DB::table('patients')->whereNotNull('import_id')->where('import_id', '!=', '')->pluck('id')->all();
        $usual = [];
        foreach (DB::table('appointments')->whereIn('patient_id', $imported)->where('active', 1)->groupBy('doctor_id', 'chair_id')->selectRaw('doctor_id, chair_id, count(*) c')->orderByDesc('c')->get() as $r) {
            $usual[$r->doctor_id] ??= $r->chair_id;
        }
        // 1) активные записи по времени: кресло, где врач уже сидит в этот день, иначе его привычное, иначе любое свободное;
        // 2) отменённые — в кресло врача в этот день (они не занимают кресло);
        // 3) расписание врача = непрерывные блоки его записей в одном кресле (разрыв до 60 минут склеивается)
        $branchChairs = DB::table('chairs')->whereIn('branch_id', $branches)->pluck('id')->all();
        $stats = DB::transaction(function () use ($chairs, $usual, $imported, $branchChairs) {
            $apps = DB::table('appointments')->whereIn('patient_id', $imported)->orderBy('time_begin')->orderBy('id')->get(['id', 'doctor_id', 'chair_id', 'date', 'time_begin', 'time_end', 'active']);
            $busy = $dayChair = $assigned = [];
            $forced = 0;
            foreach ($apps as $a) {
                if (!$a->active) continue;
                $key = $a->doctor_id.'|'.$a->date;
                $prefs = array_values(array_unique(array_merge(
                    isset($dayChair[$key]) ? [$dayChair[$key]] : [],
                    isset($usual[$a->doctor_id]) && in_array($usual[$a->doctor_id], $chairs) ? [$usual[$a->doctor_id]] : [],
                    $chairs
                )));
                $pick = null;
                foreach ($prefs as $cid) {
                    $free = true;
                    foreach ($busy[$cid] ?? [] as [$b, $e]) {
                        if ($b < $a->time_end && $e > $a->time_begin) { $free = false; break; }
                    }
                    if ($free) { $pick = $cid; break; }
                }
                if ($pick === null) { $pick = $prefs[0]; $forced++; }
                $busy[$pick][] = [$a->time_begin, $a->time_end];
                $dayChair[$key] ??= $pick;
                $assigned[$a->id] = $pick;
            }
            foreach ($apps as $a) {
                if ($a->active) continue;
                $u = $usual[$a->doctor_id] ?? null;
                $assigned[$a->id] = $dayChair[$a->doctor_id.'|'.$a->date] ?? (in_array($u, $chairs) ? $u : $chairs[0]);
            }
            $moved = 0;
            foreach ($apps as $a) {
                if ($a->chair_id != $assigned[$a->id]) {
                    DB::table('appointments')->where('id', $a->id)->update(['chair_id' => $assigned[$a->id]]);
                    $moved++;
                }
            }
            // расписание пересобирается только в креслах филиала из 1С
            DB::table('doctor_timetables')->whereIn('chair_id', $branchChairs)->delete();
            $blocks = [];
            foreach ($apps as $a) {
                if (!$a->active) continue;
                $k = $a->doctor_id.'|'.$assigned[$a->id].'|'.$a->date;
                $last = $blocks[$k][count($blocks[$k] ?? []) - 1] ?? null;
                if ($last && strtotime($a->time_begin) - strtotime($last['time_end']) <= 3600) {
                    $blocks[$k][count($blocks[$k]) - 1]['time_end'] = max($last['time_end'], $a->time_end);
                } else {
                    $blocks[$k][] = ['doctor_id' => $a->doctor_id, 'chair_id' => $assigned[$a->id], 'date' => $a->date, 'time_begin' => $a->time_begin, 'time_end' => $a->time_end];
                }
            }
            $rows = [];
            foreach ($blocks as $list) foreach ($list as $b) $rows[] = $b + ['user_id' => 0, 'import_id' => '', 'created_at' => now(), 'updated_at' => now()];
            foreach (array_chunk($rows, 500) as $chunk) DB::table('doctor_timetables')->insert($chunk);
            return ['moved' => $moved, 'forced' => $forced, 'timetable_blocks' => count($rows)];
        });
        $overlaps = [];
        $by = [];
        foreach (DB::table('appointments')->whereIn('patient_id', $imported)->where('active', 1)->orderBy('time_begin')->get(['chair_id', 'time_begin', 'time_end']) as $a) $by[$a->chair_id][] = $a;
        foreach ($by as $cid => $l) {
            $n = 0;
            for ($i = 1; $i < count($l); $i++) if ($l[$i]->time_begin < $l[$i - 1]->time_end) $n++;
            $overlaps[(string) $cid] = ['active' => count($l), 'overlaps' => $n];
        }
        echo "Кресел: ".count($chairs)." (создано $created, выключено $disabled); записей перенесено: {$stats['moved']}; "
            ."без свободного кресла: {$stats['forced']}; блоков расписания: {$stats['timetable_blocks']}\n";
        result(['ok' => true, 'chairs' => count($chairs), 'created' => $created, 'disabled' => $disabled] + $stats + ['by_chair' => $overlaps]);

    case 'requisites':
        $id = $args[0] ?? fail('requisites <tenant> [--from-1c] [--org <ref|ИНН>] [--dry-run]', 2);
        $dry = in_array('--dry-run', $args, true);
        $orgArg = ($i = array_search('--org', $args, true)) !== false ? ($args[$i + 1] ?? null) : null;
        open_tenant($id);
        $card = DB::table('legal_entities')->orderBy('id')->first();
        $picked = null;
        if (in_array('--from-1c', $args, true)) {
            $orgs = array_values(array_filter(one_c_list(work().'V8Exch_Data/v8_CatalogObject.Организации.json'),
                fn ($o) => ($o['v8_DeletionMark'] ?? 'false') !== 'true'));
            if (!$orgs) fail('В выгрузке нет справочника «Организации» — реквизиты введите вручную', 6);
            if ($orgArg) {
                $matched = array_values(array_filter($orgs, fn ($o) => ($o['v8_Ref'] ?? '') === $orgArg || ($o['v8_ИНН'] ?? '') === $orgArg));
                $picked = $matched[0] ?? fail("Организации $orgArg в выгрузке нет", 2);
            } else {
                // головная организация-юрлицо; ИП и филиалы — только если юрлица нет
                usort($orgs, fn ($a, $b) => [($b['v8_ЮрФизЛицо'] ?? '') === 'ЮрЛицо', ($b['v8_ЭтоГоловнаяОрганизация'] ?? '') === 'true']
                    <=> [($a['v8_ЮрФизЛицо'] ?? '') === 'ЮрЛицо', ($a['v8_ЭтоГоловнаяОрганизация'] ?? '') === 'true']);
                $picked = $orgs[0];
            }
            $fill = org_requisites($picked);
            if ($card) {
                if (!$dry) DB::table('legal_entities')->where('id', $card->id)->update($fill + ['updated_at' => now()]);
            } elseif (!$dry) {
                DB::table('legal_entities')->insert($fill + ['entity_type' => 'company', 'created_at' => now(), 'updated_at' => now()]);
            }
            $card = $dry ? (object) array_merge((array) ($card ?: []), $fill) : DB::table('legal_entities')->orderBy('id')->first();
        }
        if (!$card) fail('Карточка предприятия пуста: используйте --from-1c или заполните её в программе', 6);
        $changes = [];
        $current = DB::table('app_settings')->whereIn('name', array_keys(REQUISITES))->pluck('value', 'name')->all();
        foreach (REQUISITES as $key => $col) {
            $new = (string) ($card->$col ?? '');
            $old = (string) ($current[$key] ?? '');
            if ($new === $old) continue;
            $changes[] = ['key' => $key, 'old' => $old, 'new' => $new];
            if (!$dry) {
                DB::table('app_settings')->updateOrInsert(['name' => $key], ['value' => $new, 'updated_at' => now()]);
            }
        }
        result(['ok' => true, 'tenant' => $id, 'dry_run' => $dry, 'organization' => $picked ? ($picked['v8_ПолноеНаименование'] ?? $picked['v8_Description'] ?? null) : null,
            'changes' => $changes, 'card_name' => $card->company_name ?? null]);

    case 'export':
        [$target, $out] = [$args[0] ?? fail('export <tenant|db:база> <out>', 2), rtrim($args[1] ?? fail('export <tenant|db:база> <out>', 2), '/\\')];
        if (str_starts_with($target, 'db:')) {
            config(['database.connections.fid1c_chk' => array_merge(config('database.connections.mysql'), ['database' => substr($target, 3)])]);
            $db = DB::connection('fid1c_chk');
        } else {
            open_tenant($target);
            $db = DB::connection();
        }
        @mkdir($out, 0777, true);
        $save = fn ($n, $rows) => file_put_contents("$out/$n.json", json_encode($rows, JSON_UNESCAPED_UNICODE));
        $imp = fn ($t) => $db->table($t)->whereNotNull('import_id')->where('import_id', '!=', '');
        $patientIds = $imp('patients')->pluck('id');
        $save('users', $imp('users')->get(['id', 'import_id', 'email', 'created_at']));
        $save('personal_users', $imp('personal_users')->get());
        $save('patients', $imp('patients')->get(['id', 'import_id', 'user_id', 'created_at']));
        $save('doctors', $imp('doctors')->get());
        $save('employees', $imp('employees')->get());
        $save('services', $imp('services')->get());
        $save('appointments', $db->table('appointments')->whereIn('patient_id', $patientIds)->get());
        $save('invoice', $imp('invoice')->get());
        $save('money', $db->table('money_operations')->whereIn('patient_id', $patientIds)->get());
        $save('chairs', $db->table('chairs')->get());
        $save('branches', $db->table('branches')->get());
        $save('legal_entities', $db->table('legal_entities')->get());
        // в настройках только реквизиты предприятия: рядом лежат пароли и токены интеграций, их не выгружаем
        $save('app_settings', $db->table('app_settings')->whereIn('name', array_keys(REQUISITES))->get(['name', 'value']));
        $save('family_user', $db->table('family_user')->get());
        echo "Выгружено в $out\n";
        result(['ok' => true, 'out' => $out, 'patients' => count($patientIds)]);

    default:
        fwrite(STDERR, "Команды: info, dbconf, counts, create-tenant, drop-tenant, extend, parse, build, sync, set-timezone, chairs, export\n");
        result(['ok' => false, 'error' => "Неизвестная команда $cmd"], 2);
}
