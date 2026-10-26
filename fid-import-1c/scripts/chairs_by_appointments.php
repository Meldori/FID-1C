<?php
// Раскладка записей по креслам по самим записям + пересборка расписания врачей: php chairs_by_appointments.php <tenant>
// 1) активные записи по времени: кресло, где врач уже сидит в этот день, иначе его привычное, иначе любое свободное;
// 2) отменённые — в кресло врача в этот день (они не занимают кресло);
// 3) расписание врача = непрерывные блоки его записей в одном кресле (разрыв до 60 минут склеивается).
$fid = rtrim(getenv('FID') ?: 'C:/OSPanel/home/Future-IT-Dent', '/');
require $fid.'/vendor/autoload.php';
$app = require $fid.'/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

use Illuminate\Support\Facades\DB;

tenancy()->initialize(App\Models\Tenant::findOrFail($argv[1]));
$chairs = DB::table('chairs')->where('active', 1)->orderBy('id')->pluck('id')->all();
$usual = [];
foreach (DB::table('appointments')->where('active', 1)->groupBy('doctor_id', 'chair_id')->selectRaw('doctor_id, chair_id, count(*) c')->orderByDesc('c')->get() as $r) {
    $usual[$r->doctor_id] ??= $r->chair_id;
}

DB::transaction(function () use ($chairs, $usual) {
    $apps = DB::table('appointments')->orderBy('time_begin')->orderBy('id')->get(['id', 'doctor_id', 'chair_id', 'date', 'time_begin', 'time_end', 'active']);
    $busy = [];      // chair => [[begin, end]]
    $dayChair = [];  // doctor|date => chair
    $assigned = [];
    $forced = 0;
    foreach ($apps as $a) {
        if (!$a->active) continue;
        $key = $a->doctor_id.'|'.$a->date;
        $prefs = array_values(array_unique(array_merge(
            isset($dayChair[$key]) ? [$dayChair[$key]] : [],
            isset($usual[$a->doctor_id]) ? [$usual[$a->doctor_id]] : [],
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
        $key = $a->doctor_id.'|'.$a->date;
        $assigned[$a->id] = $dayChair[$key] ?? ($usual[$a->doctor_id] ?? $chairs[0]);
    }
    $moved = 0;
    foreach ($apps as $a) {
        if ($a->chair_id != $assigned[$a->id]) {
            DB::table('appointments')->where('id', $a->id)->update(['chair_id' => $assigned[$a->id]]);
            $moved++;
        }
    }

    // расписание из активных записей
    DB::table('doctor_timetables')->delete();
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
    echo "записей перенесено: $moved; без свободного кресла: $forced; блоков расписания: ".count($rows)."\n";
});

$by = [];
foreach (DB::table('appointments')->where('active', 1)->orderBy('time_begin')->get(['chair_id', 'time_begin', 'time_end']) as $a) $by[$a->chair_id][] = $a;
ksort($by);
foreach ($by as $cid => $l) {
    $n = 0;
    for ($i = 1; $i < count($l); $i++) if ($l[$i]->time_begin < $l[$i - 1]->time_end) $n++;
    echo "кресло $cid: ".count($l)." активных, пересечений $n\n";
}
