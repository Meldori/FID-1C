<?php
// Выгружает из тенанта всё, что создал импорт, в <out_dir>/*.json для сверки (только чтение).
//   php export_tenant.php <tenant|db:имя_базы> <out_dir>
// db:имя_базы — дамп с сервера, поднятый в отдельную локальную базу: читается напрямую, без тенанта.
// Окружение: FID — путь к Future-IT-Dent.
$fid = rtrim(getenv('FID') ?: 'C:/OSPanel/home/Future-IT-Dent', '/');
require $fid.'/vendor/autoload.php';
$app = require $fid.'/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
ini_set('memory_limit', '4G');

use Illuminate\Support\Facades\DB;

if ($argc < 3) {
    fwrite(STDERR, "php export_tenant.php <tenant|db:имя_базы> <out_dir>\n");
    exit(2);
}
[$target, $out] = [$argv[1], rtrim($argv[2], '/')];
if (str_starts_with($target, 'db:')) {
    config(['database.connections.chk' => array_merge(config('database.connections.mysql'), ['database' => substr($target, 3)])]);
    $db = DB::connection('chk');
} else {
    tenancy()->initialize(App\Models\Tenant::findOrFail($target));
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
$save('categories_services', $db->table('categories_services')->get());
$save('references', $imp('card_references_objects')->get());
$save('diagnoses', $imp('diagnoses')->get());
$save('appointments', $db->table('appointments')->whereIn('patient_id', $patientIds)->get());
$save('invoice', $imp('invoice')->get());
$save('stp', $db->table('services_treatment_plan')->whereNotNull('import_id')->where('import_id', '!=', '')->get());
$save('money', $db->table('money_operations')->whereIn('patient_id', $patientIds)->get());
$save('timetables', $db->table('doctor_timetables')->get());
$save('chairs', $db->table('chairs')->get());
$save('branches', $db->table('branches')->get());
$save('family_user', $db->table('family_user')->get());
echo "exported to $out\n";
