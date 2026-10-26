#!/usr/bin/env bash
# Полный прогон импорта 1С в ТЕСТОВЫЙ тенант (пересоздаётся с нуля) и итоговая сверка.
#
#   WORK=<рабочая папка с V8Exch_Data> TENANT=<id тестового тенанта> bash pipeline.sh
#
# Окружение:
#   FID     — путь к Future-IT-Dent (по умолчанию C:/OSPanel/home/Future-IT-Dent)
#   WORK    — рабочая папка, куда get_from_1c разложил выгрузку
#   TENANT  — id тестового тенанта; рабочий (например, 1/lip) скрипт трогать отказывается
#   STEPS   — какие шаги загружать (по умолчанию все, в порядке ImportFrom1CInDBServices::import)
#
# Один прогон за раз: два параллельных прогона задваивают счета и оплаты.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FID="${FID:-C:/OSPanel/home/Future-IT-Dent}"
: "${WORK:?Задайте WORK}"
: "${TENANT:?Задайте TENANT}"
case "$TENANT" in
  1|lip) echo "Отказ: pipeline.sh пересоздаёт тенант с нуля, рабочий тенант '$TENANT' он не трогает."; exit 2 ;;
esac
export FID WORK
STEPS="${STEPS:-branch price price_price diagnoses profession users employees patient_cart patient_passport dependents timetables appointment invoice_new payment payment_cart}"
clean() { grep -v DEPRECATED | grep -v '^$' | grep -v 'Psy Shell\|PHP manual\|Ctrl+D' || true; }

running=$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='php.exe'\" | Where-Object { \$_.CommandLine -like '*fid_run.php*' }).Count" 2>/dev/null | tr -d '\r')
if [ "${running:-0}" != "0" ]; then
  echo "Отказ: уже идёт другой прогон (процессов fid_run.php: $running)."; exit 2
fi

echo "== 1. Пересоздаю тестовый тенант $TENANT"
(cd "$FID" && php artisan tinker --execute="if (\$t = App\Models\Tenant::find('$TENANT')) { \$t->delete(); } DB::statement('DROP DATABASE IF EXISTS \`'.config('tenancy.database.prefix').'$TENANT'.config('tenancy.database.suffix').'\`'); echo 'dropped';") 2>&1 | clean
(cd "$FID" && TENANT_ID="$TENANT" php -d memory_limit=2G artisan tinker "$HERE/create_tenant.php") 2>&1 | clean

echo "== 2. Собираю файлы импорта"
for m in get_branch_from_1c get_price_from_1c get_price_price_from_1c get_diagnoses_from_1c get_profession_from_1c \
         get_users_from_1c get_employees_from_1c get_patient_cart_from_1c get_patient_passport_from_1c get_dependents_from_1c \
         get_timetables_from_1c get_appointment_from_1c get_invoice_new get_payment_from_1c get_payment_cart_from_1c; do
  php -d memory_limit=8G "$HERE/fid_run.php" "$m" 2>&1 | clean | tail -1
done

echo "== 3. Загружаю в $TENANT"
for n in $STEPS; do
  php -d memory_limit=8G "$HERE/fid_run.php" "sync:$n" "$TENANT" 2>&1 | clean | tail -6 | cut -c1-400
done

echo "== 4. Раскладываю записи по креслам"
php "$HERE/chairs_by_appointments.php" "$TENANT" 2>&1 | clean | tail -10

echo "== 5. Сверка с 1С"
php "$HERE/export_tenant.php" "$TENANT" "$WORK/audit_$TENANT" 2>&1 | clean | tail -1
python -I "$HERE/reconcile.py" --work "$WORK" --db "$WORK/audit_$TENANT" --out "$WORK/reconcile_$TENANT.md" --json "$WORK/reconcile_$TENANT.json"
