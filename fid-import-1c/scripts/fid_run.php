<?php
// Один шаг импорта 1С вне HTTP: php fid_run.php <get_xxx|sync:<имя>> [tenant]
// Окружение: FID — путь к Future-IT-Dent, WORK — рабочая папка выгрузки (с V8Exch_Data/)
// get_*    — ImportFrom1CDBServices (сборка файла array_import/json/*.json)
// Import_* — ImportFrom1CInDBServices (постановка задач в очередь, нужен tenant)

$fid = rtrim(getenv('FID') ?: 'C:/OSPanel/home/Future-IT-Dent', '/');
require $fid.'/vendor/autoload.php';
$app = require $fid.'/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
ini_set('memory_limit', '8G');

// Локальный прогон: никаких писем, пушей и внешних запросов реальным людям
config([
    'mail.default' => 'log',
    'broadcasting.default' => 'log',
    'queue.default' => 'sync',
]);
Illuminate\Support\Facades\Http::preventStrayRequests();

$method = $argv[1];
$tenant = $argv[2] ?? null;
if (!getenv('WORK')) {
    fwrite(STDERR, "Задайте WORK — рабочую папку выгрузки (с V8Exch_Data/)\n");
    exit(2);
}
$patch = rtrim(getenv('WORK'), '/').'/';

if ($tenant) {
    tenancy()->initialize(App\Models\Tenant::findOrFail($tenant));
    // Часовой пояс филиала кэшируется на 3 суток: сбрасываем только ключи этого тенанта
    foreach (['time_zone_', 'branch_id_'] as $key) {
        Illuminate\Support\Facades\Cache::store('redis')->forget($key.$tenant.'_0');
    }
}

// Тот же порядок файлов и задач, что в ImportFrom1CInDBServices::import()
$jobs = [
    'branch' => [App\Jobs\Import\I1C\Calendar\Branch::class, 'csv'],
    'price' => [App\Jobs\Import\I1C\Price\Price::class, 'csv'],
    'price_price' => [App\Jobs\Import\I1C\Price\PricePrice::class, 'csv'],
    'diagnoses' => [App\Jobs\Import\I1C\Diagnose\Diagnoses::class, 'csv'],
    'profession' => [App\Jobs\Import\I1C\Employee\Profession::class, 'csv'],
    'users' => [App\Jobs\Import\I1C\Patient\Patients::class, 'csv'],
    'employees' => [App\Jobs\Import\I1C\Employee\Employees::class, 'csv'],
    'patient_cart' => [App\Jobs\Import\I1C\Patient\PatientCart1C::class, 'csv'],
    'user_info' => [App\Jobs\Import\I1C\Patient\UserInfo::class, 'csv'],
    'dependents' => [App\Jobs\Import\I1C\Patient\Dependent1C::class, 'csv'],
    'patient_passport' => [App\Jobs\Import\I1C\Patient\Passport::class, 'csv'],
    'timetables' => [App\Jobs\Import\I1C\Calendar\Timetables::class, 'csv'],
    'appointment' => [App\Jobs\Import\I1C\Calendar\Appointment1C::class, 'csv'],
    'invoice_new' => [App\Jobs\Import\I1C\Invoice\InvoiceNew1C::class, 'json'],
    'payment' => [App\Jobs\Import\I1C\Payment\Payment1C::class, 'csv'],
    'payment_cart' => [App\Jobs\Import\I1C\Payment\PaymentCart::class, 'csv'],
];

$start = microtime(true);
if (str_starts_with($method, 'sync:')) {
    // sync:<name> — выполняет задачи сразу, ошибки собирает в work/errors_<name>.log
    $name = substr($method, 5);
    [$class, $type] = $jobs[$name];
    $file = $patch.'array_import/json/'.$name.'.json';
    if (!file_exists($file)) {
        echo "$name: файла нет, пропуск\n";
        exit;
    }
    $data = json_decode(file_get_contents($file), true);
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
    foreach ($errors as $msg => $rows) $log .= count($rows).'x '.$msg.' | rows: '.implode(',', array_slice($rows, 0, 10))."\n";
    file_put_contents($patch.'errors_'.$name.'.log', $log);
    echo "$name: ok=$ok fail=$fail (".round(microtime(true) - $start, 1)."s)\n".mb_substr($log, 0, 3000);
    exit;
}
if (str_starts_with($method, 'get_')) {
    $result = App\Services\Import\ImportFrom1CDBServices::$method($patch);
    echo $method.': '.(is_array($result) ? max(count($result) - 1, 0).' rows' : 'ok');
} else {
    App\Services\Import\ImportFrom1CInDBServices::$method($patch);
    echo $method.': dispatched';
}
echo ' ('.round(microtime(true) - $start, 1)."s)\n";
