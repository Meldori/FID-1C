<?php
// Создаёт локальный тестовый тенант с модулями и правами как у тенанта-образца.
//   TENANT_ID=<id> [SOURCE_TENANT=1] php artisan tinker <путь>/create_tenant.php   (из папки Future-IT-Dent)
// Домен — <id>.localhost, подписка до 2222-02-02 (без неё программа пишет «Срок действия истёк»).
use App\Jobs\Tenant\CreateTenant;
use App\Models\Tenant;
use App\Models\Tenant\ModulesTenants;
use Illuminate\Support\Facades\DB;

$tid = getenv('TENANT_ID') ?: exit("Задайте TENANT_ID\n");
$sourceId = getenv('SOURCE_TENANT') ?: '1';
if (Tenant::find($tid)) {
    echo "tenant $tid already exists\n";
    return;
}

$db = config('tenancy.database.prefix').$tid.config('tenancy.database.suffix');
DB::statement("CREATE DATABASE IF NOT EXISTS `$db` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");

$modules = ModulesTenants::where('tenant', '=', $sourceId)->pluck('module_id')->all();
tenancy()->initialize(Tenant::findOrFail($sourceId));
$right = \App\Models\Permission\Permissions::where('active', 1)->pluck('slug')->all();
tenancy()->end();

CreateTenant::dispatchSync([
    'create' => ['id' => $tid],
    'domain' => $tid.'.localhost',
    'modules' => $modules,
    'right' => $right,
    'addmissiion' => [],
]);

$tenant = Tenant::find($tid);
$tenant->date_end = '2222-02-02';
$tenant->save();

echo 'created: '.($tenant ? 'yes' : 'no').", db=$db, modules=".count($modules).', rights='.count($right)."\n";
