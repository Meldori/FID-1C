#!/usr/bin/env python3
"""fid1c — перенос клиники из 1С в Future IT Dent одной командой на каждый шаг.

Работает одинаково из Claude, ChatGPT/Codex и вручную из консоли. Только стандартная библиотека Python 3.8+;
для загрузки нужны PHP и локальный Future-IT-Dent (MySQL, Redis).

    python fid1c.py init <выгрузка.zip|.xml> --clinic "Название" [--dir папка] [--test-tenant id]
    python fid1c.py doctor                      проверить окружение
    python fid1c.py readiness                   шаг 1: отчёт о готовности (reports/readiness.md)
    python fid1c.py parse                       разобрать XML по типам (WORK/V8Exch_Data)
    python fid1c.py load --tenant <id>          шаг 2: тестовый тенант с нуля → загрузка → кресла → сверка → итог
    python fid1c.py verify --tenant <id> | --db <база>   шаг 3: сверка и итог (reports/final_<id>.md)
    python fid1c.py requisites --tenant <id> [--from-1c] [--org <ИНН>] [--dry-run]   реквизиты предприятия
    python fid1c.py chairs --tenant <id> [--count 4]   разложить записи по креслам без наложений
    python fid1c.py backup --tenant <id>        дамп базы тенанта (перед переносом в рабочий)
    python fid1c.py extend --tenant <id> [--date 2222-02-02]   продлить подписку
    python fid1c.py status                      что уже сделано по этой клинике

Папка клиники (по умолчанию текущая) хранит fid1c.json — настройки, fid1c_state.json — что сделано,
reports/ — отчёты, logs/ — журналы, backups/ — дампы, work/ — разобранная выгрузка.

Коды выхода: 0 — успешно (для verify — всё совпало), 1 — есть проблемы/расхождения, 2 — ошибка в аргументах
или настройках, 3 — окружение не готово, 4 — отказ по правилам безопасности, 5 — занято другим прогоном.
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_dump  # noqa: E402
import reconcile as reconcile_mod  # noqa: E402
import reports  # noqa: E402
import scan_1c  # noqa: E402

CONFIG = 'fid1c.json'
STATE = 'fid1c_state.json'
BRIDGE = os.path.join(HERE, 'fid_bridge.php')
DEFAULT_FID = 'C:/OSPanel/home/Future-IT-Dent' if os.name == 'nt' else os.path.expanduser('~/Future-IT-Dent')
# Шаги загрузки в порядке ImportFrom1CInDBServices::import() и их сборщики
STEPS = [
    ('branch', 'get_branch_from_1c'), ('price', 'get_price_from_1c'), ('price_price', 'get_price_price_from_1c'),
    ('diagnoses', 'get_diagnoses_from_1c'), ('profession', 'get_profession_from_1c'), ('users', 'get_users_from_1c'),
    ('employees', 'get_employees_from_1c'), ('patient_cart', 'get_patient_cart_from_1c'),
    ('patient_passport', 'get_patient_passport_from_1c'), ('dependents', 'get_dependents_from_1c'),
    ('timetables', 'get_timetables_from_1c'), ('appointment', 'get_appointment_from_1c'),
    ('invoice_new', 'get_invoice_new'), ('payment', 'get_payment_from_1c'), ('payment_cart', 'get_payment_cart_from_1c'),
]
NOISE = ('DEPRECATED', 'Deprecated:', 'Psy Shell', 'PHP manual', 'Ctrl+D')
BACKUP_MAX_AGE_HOURS = 3


class Stop(Exception):
    """Остановка с понятным сообщением и кодом выхода."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- настройки и состояние

def defaults():
    return {
        'clinic': None, 'dump': None, 'work': 'work', 'fid': os.environ.get('FID', DEFAULT_FID),
        'php': os.environ.get('FID1C_PHP', 'php'), 'mysqldump': os.environ.get('FID1C_MYSQLDUMP'),
        'test_tenant': None, 'source_tenant': '1', 'protected_tenants': ['1', 'lip'],
        'timezone': None, 'chairs': None, 'organization': None, 'memory': '8G',
    }


class Clinic:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        path = os.path.join(self.root, CONFIG)
        self.cfg = defaults()
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                self.cfg.update(json.load(f))
        self.exists = os.path.exists(path)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def resolve(self, key):
        v = self.cfg.get(key)
        return None if v is None else (v if os.path.isabs(v) else self.path(v))

    @property
    def work(self):
        return self.resolve('work')

    @property
    def dump(self):
        return self.resolve('dump')

    def save(self):
        with open(self.path(CONFIG), 'w', encoding='utf-8') as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def state(self):
        p = self.path(STATE)
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_state(self, st):
        tmp = self.path(STATE + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, self.path(STATE))

    def require(self):
        if not self.exists:
            raise Stop(f'В {self.root} нет {CONFIG}. Начните с: python fid1c.py init <выгрузка> --clinic "Название"', 2)


def now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def say(text=''):
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


# ---------------------------------------------------------------- блокировка: один прогон на тенант

def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lock:
    """Файл-замок в системной временной папке: два прогона в один тенант задваивают счета и оплаты."""

    def __init__(self, name):
        self.file = os.path.join(tempfile.gettempdir(), f'fid1c-{name}.lock')

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w') as f:
                    json.dump({'pid': os.getpid(), 'since': now(), 'argv': sys.argv}, f)
                return self
            except FileExistsError:
                try:
                    with open(self.file, encoding='utf-8') as f:
                        info = json.load(f)
                except (OSError, ValueError):
                    info = {}
                if pid_alive(int(info.get('pid', 0))):
                    raise Stop(f"Уже идёт другой прогон (pid {info.get('pid')}, с {info.get('since')}): "
                               f"{' '.join(info.get('argv', []))}. Дождитесь его или остановите.", 5)
                os.remove(self.file)  # замок остался от упавшего прогона
        raise Stop('Не удалось взять замок прогона', 5)

    def __exit__(self, *exc):
        try:
            os.remove(self.file)
        except OSError:
            pass


# ---------------------------------------------------------------- вызов PHP

class Bridge:
    def __init__(self, clinic, log_name=None):
        self.c = clinic
        self.log = None
        if log_name:
            os.makedirs(clinic.path('logs'), exist_ok=True)
            self.log = clinic.path('logs', f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{log_name}.log")

    def env(self):
        env = dict(os.environ)
        env.update({'FID': self.c.cfg['fid'], 'WORK': self.c.work.replace('\\', '/') + '/',
                    'FID1C_PROTECTED': ','.join(map(str, self.c.cfg.get('protected_tenants') or [])),
                    'FID1C_MEMORY': self.c.cfg.get('memory') or '8G'})
        return env

    def call(self, *args, quiet=False, secret=False):
        """Запуск команды fid_bridge.php; вывод — на экран и в журнал, результат — из строки @@FID1C."""
        cmd = [self.c.cfg['php'], '-d', 'memory_limit=' + (self.c.cfg.get('memory') or '8G'), BRIDGE] + [str(a) for a in args]
        try:
            proc = subprocess.Popen(cmd, cwd=self.c.cfg['fid'] if os.path.isdir(self.c.cfg['fid']) else None,
                                    env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            raise Stop(f"Не найден PHP ({self.c.cfg['php']}). Укажите путь в fid1c.json → \"php\".", 3)
        result, tail = None, []
        log = open(self.log, 'a', encoding='utf-8') if self.log and not secret else None
        try:
            if log:
                log.write(f"\n### {now()} {' '.join(map(str, args))}\n")
            for raw in proc.stdout:
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')
                if line.startswith('@@FID1C '):
                    try:
                        result = json.loads(line[8:])
                    except ValueError:
                        pass
                    if log and not secret:
                        log.write(line + '\n')
                    continue
                if not line.strip() or any(n in line for n in NOISE):
                    continue
                tail = (tail + [line])[-20:]
                if log:
                    log.write(line + '\n')
                if not quiet:
                    say('   ' + line[:400])
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            raise
        finally:
            if log:
                log.close()
        if result is None:
            raise Stop(f"Команда «{' '.join(map(str, args))}» завершилась без результата (код {proc.returncode}).\n"
                       + '\n'.join(tail[-8:]) + (f'\nЖурнал: {self.log}' if self.log else ''), 3)
        return result


def bridge_ok(res, what):
    if not res.get('ok'):
        code = 4 if 'защищ' in str(res.get('error', '')) or 'fid1c_test' in str(res.get('error', '')) else 1
        raise Stop(f"{what}: {res.get('error', 'ошибка')}", code)
    return res


# ---------------------------------------------------------------- команды

def cmd_init(a):
    root = os.path.abspath(a.dir or os.getcwd())
    os.makedirs(root, exist_ok=True)
    c = Clinic(root)
    src = os.path.abspath(a.source)
    if not os.path.exists(src):
        raise Stop(f'Нет файла {src}', 2)
    low = src.lower()
    if low.endswith('.zip'):
        dump = extract_zip(src, c.path('dump'))
    elif low.endswith(('.rar', '.7z')):
        dump = extract_7z(src, c.path('dump'))
    elif low.endswith('.xml'):
        dump = src
    else:
        raise Stop('Ожидается .xml, .zip, .rar или .7z с выгрузкой 1С', 2)
    c.cfg['clinic'] = a.clinic or c.cfg.get('clinic') or os.path.splitext(os.path.basename(src))[0]
    # внутри папки клиники путь относительный (папку можно переносить), снаружи — абсолютный
    c.cfg['dump'] = os.path.relpath(dump, root) if os.path.abspath(dump).startswith(root + os.sep) else dump
    for key in ('test_tenant', 'timezone', 'chairs', 'fid', 'php'):
        if getattr(a, key, None):
            c.cfg[key] = getattr(a, key)
    # тестовый тенант по умолчанию: свой, не рабочий; отчёт и load сразу знают, куда грузить
    c.cfg['test_tenant'] = c.cfg.get('test_tenant') or 'fid1ctest'
    c.save()
    for d in ('reports', 'logs', 'backups'):
        os.makedirs(c.path(d), exist_ok=True)
    say(f'Папка клиники: {root}\nВыгрузка: {dump}\nНастройки: {c.path(CONFIG)}')
    say('Дальше: python fid1c.py doctor, затем python fid1c.py readiness')
    return 0


def safe_members(names, dest):
    dest = os.path.abspath(dest)
    for n in names:
        target = os.path.abspath(os.path.join(dest, n))
        if not (target == dest or target.startswith(dest + os.sep)):
            raise Stop(f'Архив содержит путь за пределами папки: {n}', 4)


def pick_xml(folder):
    xmls = sorted(glob.glob(os.path.join(folder, '**', '*.xml'), recursive=True), key=os.path.getsize, reverse=True)
    if not xmls:
        raise Stop(f'В архиве нет .xml ({folder})', 2)
    return xmls[0]


def extract_zip(src, dest):
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if n.lower().endswith('.xml')]
        safe_members(names, dest)
        need = sum(z.getinfo(n).file_size for n in names)
        free = shutil.disk_usage(dest).free
        if need > free:
            raise Stop(f'Не хватает места: нужно {need / 2**30:.1f} ГиБ, свободно {free / 2**30:.1f} ГиБ', 3)
        for n in names:
            if not os.path.exists(os.path.join(dest, n)) or os.path.getsize(os.path.join(dest, n)) != z.getinfo(n).file_size:
                say(f'Распаковываю {n} ({z.getinfo(n).file_size / 2**30:.2f} ГиБ)…')
                z.extract(n, dest)
    return pick_xml(dest)


def extract_7z(src, dest):
    exe = shutil.which('7z') or shutil.which('7za') or next(iter(glob.glob('C:/Program Files/7-Zip/7z.exe')), None)
    if not exe:
        raise Stop('Для .rar/.7z нужен 7-Zip (7z в PATH). Или распакуйте архив сами и передайте .xml', 3)
    os.makedirs(dest, exist_ok=True)
    listing = subprocess.run([exe, 'l', '-slt', src], capture_output=True, text=True, encoding='utf-8', errors='replace').stdout
    safe_members([ln[7:] for ln in listing.splitlines() if ln.startswith('Path = ')][1:], dest)
    r = subprocess.run([exe, 'x', '-y', '-o' + dest, src, '*.xml', '-r'])
    if r.returncode != 0:
        raise Stop('7-Zip не смог распаковать архив (возможно, он повреждён)', 1)
    return pick_xml(dest)


def find_mysqldump(cfg):
    for cand in [cfg.get('mysqldump'), shutil.which('mysqldump')] + sorted(glob.glob('C:/OSPanel/modules/MySQL-*/bin/mysqldump.exe'), reverse=True):
        if cand and os.path.exists(cand):
            return cand
    return None


def cmd_doctor(a):
    c = Clinic(a.dir or os.getcwd())
    rows, bad = [], 0

    def row(ok, what, detail, fix=''):
        nonlocal bad
        bad += 0 if ok else 1
        rows.append(('✔' if ok else '✘', what, detail, '' if ok else fix))

    row(sys.version_info >= (3, 8), 'Python', sys.version.split()[0], 'нужен Python 3.8+')
    row(c.exists, 'Папка клиники', c.root if c.exists else 'нет fid1c.json', 'python fid1c.py init <выгрузка> --clinic "…"')
    if c.exists:
        dump = c.dump
        row(bool(dump and os.path.exists(dump)), 'Выгрузка', dump or '—', 'проверьте "dump" в fid1c.json')
        if dump and os.path.exists(dump):
            free = shutil.disk_usage(c.root).free
            need = os.path.getsize(dump) * 2
            row(free > need, 'Место на диске', f'свободно {free / 2**30:.1f} ГиБ, нужно ~{need / 2**30:.1f}', 'освободите место')
    fid = c.cfg['fid']
    row(os.path.exists(os.path.join(fid, 'artisan')), 'Future-IT-Dent', fid, 'укажите "fid" в fid1c.json')
    php = shutil.which(c.cfg['php']) or (c.cfg['php'] if os.path.exists(c.cfg['php']) else None)
    row(bool(php), 'PHP', php or c.cfg['php'], 'укажите "php" в fid1c.json')
    info = None
    if php and os.path.exists(os.path.join(fid, 'artisan')):
        try:
            info = Bridge(c).call('info', quiet=True)
        except Stop as e:
            row(False, 'Запуск Future-IT-Dent', str(e).splitlines()[0], 'см. сообщение')
    if info:
        row(info.get('db'), 'MySQL', 'подключение есть' if info.get('db') else info.get('db_error', ''), 'запустите MySQL (OSPanel)')
        row(info.get('redis'), 'Redis', 'подключение есть' if info.get('redis') else info.get('redis_error', ''), 'запустите Redis (OSPanel)')
        imp = info.get('importer', {})
        row(imp.get('base') and imp.get('requests') and imp.get('dependents'), 'Импорт 1С в FID',
            ', '.join(k for k, v in imp.items() if v) or 'нет',
            'нужна ветка Future-IT-Dent с доработанным импортом (Заявка, иждивенцы)')
        ids = {t['id']: t for t in info.get('tenants', [])}
        prot = [t for t in c.cfg.get('protected_tenants') or []]
        row(True, 'Защищённые тенанты', ', '.join(prot) or 'не заданы')
        tt = c.cfg.get('test_tenant')
        if tt:
            t = ids.get(str(tt))
            detail = 'будет создан при загрузке' if not t else ('тестовый (создан fid1c)' if t['test'] else 'есть, создан НЕ fid1c — пересоздание только с --adopt')
            row(str(tt) not in map(str, prot), 'Тестовый тенант', f'{tt}: {detail}', 'тестовый тенант не может быть защищённым')
    md = find_mysqldump(c.cfg)
    row(bool(md), 'mysqldump (для дампа перед рабочей базой)', md or 'не найден', 'укажите "mysqldump" в fid1c.json')

    w = max(len(r[1]) for r in rows)
    for mark, what, detail, fix in rows:
        say(f"{mark} {what.ljust(w)}  {detail}" + (f'\n  {" " * w}  → {fix}' if fix else ''))
    say('' if not bad else f'\nПроблем: {bad}. Без них загрузка не пойдёт; проверка выгрузки (readiness) работает и так.')
    return 0 if not bad else 3


def cmd_readiness(a):
    c = Clinic(a.dir or os.getcwd())
    c.require()
    dump = c.dump
    if not dump or not os.path.exists(dump):
        raise Stop(f'Нет файла выгрузки {dump}', 2)
    say(f'1/2 Проверяю целостность {dump}…')
    chk = check_dump.check(dump)
    for line in check_dump.describe(chk):
        say('   ' + line)
    say('2/2 Обзор объектов (2–3 минуты на гигабайт)…')
    t0 = time.time()
    res = scan_1c.scan(dump, stop_at_restart=True)
    info = scan_1c.analyze(res)
    os.makedirs(c.path('reports'), exist_ok=True)
    scan_1c.write_outputs(dump, c.path('reports', 'scan'), res, info)
    fid = None
    if not a.no_fid:
        try:
            fid = Bridge(c).call('info', quiet=True)
        except Stop:
            fid = None
    text = reports.readiness(c.cfg.get('clinic') or 'клиники', chk, info, fid, c.cfg)
    with open(c.path('reports', 'readiness.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    with open(c.path('reports', 'readiness.json'), 'w', encoding='utf-8') as f:
        json.dump({'check': chk, 'analysis': info, 'level': text.splitlines()[0].rsplit(': ', 1)[-1],
                   'missing': info['missing'], 'config': info['config'], 'at': now()}, f, ensure_ascii=False, indent=1, default=str)
    st = c.state()
    st['readiness'] = {'at': now(), 'level': text.splitlines()[0].rsplit(': ', 1)[-1], 'config': info['config']}
    c.save_state(st)
    say(f'   готово за {time.time() - t0:.0f} с\n')
    say(text)
    say(f"Отчёт: {c.path('reports', 'readiness.md')}")
    return 1 if 'НЕЛЬЗЯ' in text.splitlines()[0] else 0


def ensure_parsed(c, br, force=False):
    data = os.path.join(c.work, 'V8Exch_Data')
    if not force and glob.glob(os.path.join(data, '*.json')):
        return
    dump = c.dump
    if not dump or not os.path.exists(dump):
        raise Stop(f'Нет файла выгрузки {dump}', 2)
    chk = check_dump.check(dump)
    if not chk['ok'] and not c.cfg.get('allow_damaged'):
        raise Stop('Выгрузка повреждена (см. readiness). Чтобы грузить пригодную часть, вырежьте её в отдельный файл и укажите его в '
                   '"dump", либо поставьте "allow_damaged": true в fid1c.json — только по решению пользователя.', 4)
    os.makedirs(c.work, exist_ok=True)
    say(f'Разбираю XML по типам в {data} (2–3 минуты на гигабайт)…')
    bridge_ok(br.call('parse', dump), 'Разбор выгрузки')
    st = c.state()
    st['parsed'] = {'at': now(), 'dump': dump, 'size': os.path.getsize(dump)}
    c.save_state(st)


def cmd_parse(a):
    c = Clinic(a.dir or os.getcwd())
    c.require()
    with Lock('parse-' + hashlib.md5(c.root.encode('utf-8')).hexdigest()[:12]):
        ensure_parsed(c, Bridge(c, 'parse'), force=True)
    say('Готово.')
    return 0


def tenant_info(c, br, tenant):
    info = br.call('info', quiet=True)
    if not info.get('db'):
        raise Stop('Нет подключения к MySQL: ' + str(info.get('db_error')), 3)
    return info, {t['id']: t for t in info.get('tenants', [])}.get(str(tenant))


def check_backup(c, tenant, backup):
    if not backup:
        raise Stop('Для рабочего тенанта нужен свежий дамп: python fid1c.py backup --tenant ' + tenant, 4)
    if not os.path.exists(backup):
        raise Stop(f'Нет файла дампа {backup}', 4)
    age = (time.time() - os.path.getmtime(backup)) / 3600
    if age > BACKUP_MAX_AGE_HOURS:
        raise Stop(f'Дамп {backup} старше {BACKUP_MAX_AGE_HOURS} ч ({age:.1f} ч). Сделайте новый: fid1c.py backup --tenant {tenant}', 4)
    with open(backup, 'rb') as f:
        f.seek(max(os.path.getsize(backup) - 512, 0))
        if b'Dump completed' not in f.read():
            raise Stop(f'Дамп {backup} не завершён (нет строки «Dump completed»)', 4)


def cmd_load(a):
    c = Clinic(a.dir or os.getcwd())
    c.require()
    tenant = str(a.tenant or c.cfg.get('test_tenant') or '')
    if not tenant:
        raise Stop('Укажите --tenant или "test_tenant" в fid1c.json', 2)
    protected = [str(t) for t in c.cfg.get('protected_tenants') or []]
    production = a.production
    if tenant in protected and not production:
        raise Stop(f'Тенант {tenant} рабочий (protected_tenants). Тестовая загрузка пересоздаёт тенант с нуля — для него запрещена. '
                   'Перенос в рабочий: --production --backup <дамп> --yes, только по согласию пользователя.', 4)
    if production and not a.yes:
        raise Stop('Загрузка в рабочий тенант требует --yes (явное согласие пользователя в этом разговоре).', 4)
    steps = [s for s in STEPS if not a.steps or s[0] in a.steps.split(',')]
    if a.steps and len(steps) != len(a.steps.split(',')):
        raise Stop('Неизвестный шаг. Шаги: ' + ', '.join(s for s, _ in STEPS), 2)

    with Lock('tenant-' + tenant):
        br = Bridge(c, f'load-{tenant}')
        info, t = tenant_info(c, br, tenant)
        st = c.state()
        run = st.setdefault('tenants', {}).setdefault(tenant, {})

        if production:
            if not t:
                raise Stop(f'Тенанта {tenant} нет', 2)
            check_backup(c, tenant, a.backup)
            counts = bridge_ok(br.call('counts', tenant, quiet=True), 'Проверка тенанта')['counts']
            busy = {k: v for k, v in counts.items() if k in ('patients', 'invoice', 'services') and v}
            if busy and not a.resume:
                raise Stop(f'В тенанте {tenant} уже есть загруженные из 1С данные {busy}. Поверх заливать нельзя — задвоятся '
                           'счета и оплаты. Откатите тенант из дампа «до импорта» или выберите другой.', 4)
        elif a.resume:
            if not t:
                raise Stop(f'Тенанта {tenant} нет — продолжать нечего, запустите load без --resume', 2)
        else:
            if t and not t.get('test') and not a.adopt:
                raise Stop(f'Тенант {tenant} уже есть и создан не fid1c. Пересоздание удалит его базу. Если это точно тестовый тенант — '
                           'добавьте --adopt; иначе выберите другой id.', 4)
            say(f'== Пересоздаю тестовый тенант {tenant}')
            if t:
                bridge_ok(br.call('drop-tenant', tenant, *(['--adopt'] if a.adopt else [])), 'Удаление тенанта')
            bridge_ok(br.call('create-tenant', tenant, c.cfg.get('source_tenant') or '1'), 'Создание тенанта')
            run.clear()
            run['created_at'] = now()
        run['production'] = production
        c.save_state(st)

        ensure_parsed(c, br)
        if not a.no_build:
            say('== Собираю файлы импорта')
            for name, method in steps:
                try:
                    res = bridge_ok(br.call('build', method, quiet=True), f'Сборка {name}')
                except Stop as e:
                    # сборщик FID завершается через dd(), если в выгрузке нет нужного объекта (например, регистра графиков):
                    # это «данных нет», а не поломка — шаг пропускается, старый файл шага удаляется
                    if 'не существует' not in str(e):
                        raise
                    stale = os.path.join(c.work, 'array_import', 'json', name + '.json')
                    if os.path.exists(stale):
                        os.remove(stale)
                    say(f'   {name}: в выгрузке нет нужных данных — пропуск')
                    continue
                say(f"   {name}: {res.get('rows') if res.get('rows') is not None else 'готово'}")
        say(f'== Загружаю в {tenant}')
        done = run.setdefault('steps', {})
        for name, _ in steps:
            prev = done.get(name, {})
            if a.resume and prev.get('status') in ('done', 'skipped'):
                say(f'   {name}: уже загружен, пропуск')
                continue
            if a.resume and prev.get('status') == 'running':
                raise Stop(f'Шаг {name} был прерван посередине: часть строк загружена. Продолжать нельзя — задвоится. '
                           'Для теста — load без --resume (тенант пересоздастся), для рабочего — откат из дампа.', 4)
            done[name] = {'status': 'running', 'at': now()}
            c.save_state(st)
            res = br.call('sync', name, tenant)
            if not res.get('ok'):
                done[name] = {'status': 'crashed', 'at': now(), 'error': res.get('error')}
                c.save_state(st)
                raise Stop(f"Шаг {name} упал: {res.get('error')}", 1)
            done[name] = {'status': 'skipped' if res.get('skipped') else 'done', 'at': now(), 'rows': res.get('rows', 0),
                          'loaded': res.get('loaded', 0), 'failed': res.get('failed', 0), 'errors': res.get('errors', [])}
            c.save_state(st)
            if name == 'branch' and c.cfg.get('timezone'):
                bridge_ok(br.call('set-timezone', tenant, c.cfg['timezone'], quiet=True), 'Часовой пояс')
                say(f"   часовой пояс филиала: {c.cfg['timezone']}")
        if production:
            say(f'== Реквизиты предприятия не менял (рабочий тенант). Посмотреть, что изменится: '
                f'python fid1c.py requisites --tenant {tenant} --from-1c --dry-run')
        elif not a.no_requisites:
            say('== Реквизиты предприятия из справочника «Организации»')
            fill_requisites(c, br, tenant)
        if not a.no_chairs:
            say('== Раскладываю записи по креслам')
            res = br.call('chairs', tenant, *([c.cfg['chairs']] if c.cfg.get('chairs') else []))
            if res.get('ok'):
                run['chairs'] = res
            else:
                say('   ' + str(res.get('error')))
        c.save_state(st)
        if production:
            ten = [x for x in info.get('tenants', []) if x['id'] == tenant][0]
            if ten.get('date_end') and str(ten['date_end'])[:10] < dt.date.today().isoformat():
                say(f"!! Подписка тенанта {tenant} закончилась {ten['date_end']}: python fid1c.py extend --tenant {tenant}")
        return verify(c, br, tenant, None)


def verify(c, br, tenant, dbname, export=None):
    target = f'db:{dbname}' if dbname else tenant
    label = dbname or tenant or 'export'
    if export:
        # готовая выгрузка базы (например, присланная в облачный чат): без PHP и Future-IT-Dent
        out = export
        say(f'== Сверка с 1С (выгрузка {export})')
    else:
        say(f'== Сверка с 1С ({target})')
        out = os.path.join(c.work, f'audit_{label}')
        bridge_ok(br.call('export', target, out, quiet=True), 'Выгрузка для сверки')
    if not os.path.isdir(os.path.join(c.work, 'V8Exch_Data')):
        raise Stop(f'Нет {os.path.join(c.work, "V8Exch_Data")}: сначала fid1c.py parse (или укажите --work)', 2)
    os.makedirs(c.path('reports'), exist_ok=True)
    rec_md = c.path('reports', f'reconcile_{label}.md')
    _, rec = reconcile_mod.run(c.work, out, rec_md, c.path('reports', f'reconcile_{label}.json'), tz_name=c.cfg.get('timezone'))
    st = c.state()
    run = st.get('tenants', {}).get(tenant, {}) if not dbname else {}
    ready = None
    if os.path.exists(c.path('reports', 'readiness.json')):
        with open(c.path('reports', 'readiness.json'), encoding='utf-8') as f:
            ready = json.load(f)
    where = f'выгрузка {export}' if export else f'база {dbname}' if dbname else f"тенант {tenant} ({tenant}.localhost)" + (' — РАБОЧИЙ' if run.get('production') else ' — тестовый')
    text = reports.final(where, rec, run.get('steps'), ready, run.get('chairs'))
    path = c.path('reports', f'final_{label}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    if tenant and not dbname and not export:
        run['verified'] = {'at': now(), 'score': rec['score']}
        st.setdefault('tenants', {})[tenant] = run
        c.save_state(st)
    say('')
    say(text)
    say(f'Итог: {path}\nПодробно: {rec_md}')
    m, n = rec['score']
    return 0 if m == n else 1


def cmd_verify(a):
    c = Clinic(a.dir or os.getcwd())
    if a.work:
        c.cfg['work'] = os.path.abspath(a.work)
    elif not a.export:
        c.require()
    if not (a.tenant or a.db or a.export):
        raise Stop('Укажите --tenant <id>, --db <база> или --export <папка выгрузки базы>', 2)
    if a.export and not os.path.isdir(a.export):
        raise Stop(f'Нет папки {a.export}', 2)
    br = None if a.export else Bridge(c, f'verify-{a.tenant or a.db}')
    return verify(c, br, str(a.tenant or ''), a.db, os.path.abspath(a.export) if a.export else None)


def cmd_backup(a):
    c = Clinic(a.dir or os.getcwd())
    c.require()
    exe = find_mysqldump(c.cfg)
    if not exe:
        raise Stop('Не найден mysqldump — укажите "mysqldump" в fid1c.json', 3)
    br = Bridge(c)
    conf = bridge_ok(br.call('dbconf', quiet=True, secret=True), 'Параметры базы')
    db = a.database or f"{conf['prefix']}{a.tenant}{conf['suffix']}"
    os.makedirs(c.path('backups'), exist_ok=True)
    out = c.path('backups', f"{db}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.sql")
    env = dict(os.environ, MYSQL_PWD=conf.get('password') or '')
    say(f'Делаю дамп {db} → {out}')
    with open(out, 'wb') as f:
        r = subprocess.run([exe, '-h', conf['host'], '-P', conf['port'], '-u', conf['user'], '--single-transaction', '--routines',
                            '--default-character-set=utf8mb4', db], stdout=f, stderr=subprocess.PIPE, env=env)
    with open(out, 'rb') as f:
        f.seek(max(os.path.getsize(out) - 512, 0))
        complete = b'Dump completed' in f.read()
    if r.returncode != 0 or not complete:
        raise Stop(f"Дамп не удался: {r.stderr.decode('utf-8', 'replace')[:500]}", 1)
    say(f'Готово: {out} ({os.path.getsize(out) / 2**20:.1f} МиБ). Откат: mysql {db} < "{out}"')
    return 0


def fill_requisites(c, br, tenant, dry=False, from_1c=True):
    """Карточка предприятия из 1С + синхронизация настроек программы (app_settings печатаются в счетах)."""
    args = ['requisites', tenant] + (['--from-1c'] if from_1c else []) + (['--dry-run'] if dry else [])
    if c.cfg.get('organization'):
        args += ['--org', c.cfg['organization']]
    res = br.call(*args, quiet=True)
    if not res.get('ok'):
        say('   ' + str(res.get('error')))
        return res
    say(f"   организация из 1С: {res.get('organization') or '—'}; изменено реквизитов в настройках: {len(res['changes'])}"
        + (' (пробный прогон, ничего не записано)' if dry else ''))
    for ch in res['changes']:
        say(f"   {ch['key']}: «{ch['old'] or '—'}» → «{ch['new'] or '—'}»")
    return res


def cmd_requisites(a):
    c = Clinic(a.dir or os.getcwd())
    c.require()
    tenant = str(a.tenant)
    if tenant in [str(t) for t in c.cfg.get('protected_tenants') or []] and not a.dry_run and not a.yes:
        raise Stop(f'Тенант {tenant} рабочий: запись реквизитов только с --yes (по согласию пользователя). '
                   'Сначала посмотрите изменения: --dry-run', 4)
    if a.org:
        c.cfg['organization'] = a.org
    with Lock('tenant-' + tenant):
        res = fill_requisites(c, Bridge(c, f'requisites-{tenant}'), tenant, a.dry_run, a.from_1c)
    return 0 if res.get('ok') else 1


def cmd_chairs(a):
    c = Clinic(a.dir or os.getcwd())
    tenant = str(a.tenant)
    if tenant in [str(t) for t in c.cfg.get('protected_tenants') or []] and not a.yes:
        raise Stop(f'Тенант {tenant} рабочий: перекладка записей по креслам только с --yes (по согласию пользователя)', 4)
    count = a.count or c.cfg.get('chairs')
    with Lock('tenant-' + tenant):
        res = bridge_ok(Bridge(c, f'chairs-{tenant}').call('chairs', tenant, *([count] if count else [])), 'Кресла')
    st = c.state()
    if c.exists:
        st.setdefault('tenants', {}).setdefault(tenant, {})['chairs'] = res
        c.save_state(st)
    for cid, v in res.get('by_chair', {}).items():
        say(f"   кресло {cid}: активных записей {v['active']}, пересечений {v['overlaps']}")
    return 0


def cmd_extend(a):
    c = Clinic(a.dir or os.getcwd())
    res = bridge_ok(Bridge(c).call('extend', a.tenant, a.date, quiet=True), 'Продление')
    say(f"Подписка тенанта {a.tenant}: {res.get('date_end_before')} → {res['date_end']}")
    return 0


def cmd_status(a):
    c = Clinic(a.dir or os.getcwd())
    c.require()
    st = c.state()
    say(f"Клиника: {c.cfg.get('clinic')}\nВыгрузка: {c.dump}\nТестовый тенант: {c.cfg.get('test_tenant') or '—'}")
    r = st.get('readiness')
    say(f"Готовность: {r['level']} ({r['config']}, {r['at']})" if r else 'Готовность: не проверялась → fid1c.py readiness')
    p = st.get('parsed')
    say(f"Разбор XML: {p['at']}" if p else 'Разбор XML: не делался')
    for tid, run in st.get('tenants', {}).items():
        steps = run.get('steps', {})
        done = sum(1 for s in steps.values() if s.get('status') in ('done', 'skipped'))
        bad = [k for k, s in steps.items() if s.get('status') not in ('done', 'skipped')]
        v = run.get('verified')
        say(f"Тенант {tid}{' (РАБОЧИЙ)' if run.get('production') else ''}: шагов {done}/{len(STEPS)}"
            + (f", прервано: {', '.join(bad)}" if bad else '')
            + (f", сверка {v['score'][0]}/{v['score'][1]} ({v['at']})" if v else ', сверки не было'))
    return 0


# ---------------------------------------------------------------- разбор аргументов

def parser():
    # -C/--dir понимается и до, и после команды: «fid1c.py -C папка load» и «fid1c.py load -C папка»
    ap = argparse.ArgumentParser(prog='fid1c', description='Перенос клиники из 1С в Future IT Dent')
    ap.add_argument('-C', '--dir', dest='dir_top', help='папка клиники (по умолчанию текущая)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    add = sub.add_parser

    def add_parser(*x, **k):
        p = add(*x, **k)
        p.add_argument('-C', '--dir', help='папка клиники (по умолчанию текущая)')
        return p

    sub.add_parser = add_parser
    p = sub.add_parser('init', help='создать папку клиники и fid1c.json')
    p.add_argument('source', help='выгрузка: .xml, .zip, .rar, .7z')
    p.add_argument('--clinic')
    p.add_argument('--test-tenant')
    p.add_argument('--timezone', help='часовой пояс филиала, например Europe/Samara')
    p.add_argument('--chairs', type=int, help='сколько кресел в клинике (по умолчанию — как в тенанте)')
    p.add_argument('--fid')
    p.add_argument('--php')
    sub.add_parser('doctor', help='проверить окружение')
    p = sub.add_parser('readiness', help='шаг 1: отчёт о готовности выгрузки')
    p.add_argument('--no-fid', action='store_true', help='не обращаться к Future-IT-Dent (например, в облачном чате)')
    sub.add_parser('parse', help='разобрать XML по типам')
    p = sub.add_parser('load', help='шаг 2: загрузка (тестовый тенант пересоздаётся) и сверка')
    p.add_argument('--tenant')
    p.add_argument('--steps', help='только эти шаги через запятую')
    p.add_argument('--resume', action='store_true', help='продолжить после остановки между шагами')
    p.add_argument('--adopt', action='store_true', help='разрешить пересоздать тенант, созданный не fid1c')
    p.add_argument('--no-build', action='store_true', help='не пересобирать файлы импорта')
    p.add_argument('--no-chairs', action='store_true', help='не раскладывать записи по креслам')
    p.add_argument('--no-requisites', action='store_true', help='не заполнять реквизиты предприятия из 1С')
    p.add_argument('--production', action='store_true', help='рабочий тенант: без пересоздания, нужен --backup и --yes')
    p.add_argument('--backup', help='свежий дамп рабочего тенанта (fid1c.py backup)')
    p.add_argument('--yes', action='store_true', help='пользователь явно согласился')
    p = sub.add_parser('verify', help='шаг 3: сверка и итоговый отчёт')
    p.add_argument('--tenant')
    p.add_argument('--db', help='отдельная база (например, дамп с сервера)')
    p.add_argument('--export', help='готовая выгрузка базы (папка с patients.json, invoice.json …) — без Future-IT-Dent')
    p.add_argument('--work', help='папка с V8Exch_Data, если не из fid1c.json')
    p = sub.add_parser('backup', help='дамп базы тенанта')
    p.add_argument('--tenant', required=True)
    p.add_argument('--database', help='имя базы, если не tenant<id>')
    p = sub.add_parser('requisites', help='реквизиты предприятия: карточка → настройки программы (из 1С — с --from-1c)')
    p.add_argument('--tenant', required=True)
    p.add_argument('--from-1c', action='store_true', help='сначала заполнить карточку из справочника «Организации»')
    p.add_argument('--org', help='ИНН или ссылка организации, если в 1С их несколько (по умолчанию головное юрлицо)')
    p.add_argument('--dry-run', action='store_true', help='только показать, что изменится')
    p.add_argument('--yes', action='store_true', help='для рабочего тенанта: пользователь согласился')
    p = sub.add_parser('chairs', help='разложить записи по креслам (и задать число кресел)')
    p.add_argument('--tenant', required=True)
    p.add_argument('--count', type=int, help='сколько кресел в филиале из 1С')
    p.add_argument('--yes', action='store_true', help='для рабочего тенанта: пользователь согласился')
    p = sub.add_parser('extend', help='продлить подписку тенанта')
    p.add_argument('--tenant', required=True)
    p.add_argument('--date', default='2222-02-02')
    sub.add_parser('status', help='что сделано по клинике')
    return ap


COMMANDS = {'init': cmd_init, 'doctor': cmd_doctor, 'readiness': cmd_readiness, 'parse': cmd_parse, 'load': cmd_load,
            'verify': cmd_verify, 'requisites': cmd_requisites, 'chairs': cmd_chairs, 'backup': cmd_backup, 'extend': cmd_extend, 'status': cmd_status}


def main(argv=None):
    # консоль Windows по умолчанию не UTF-8 — без этого русский текст выводится кракозябрами
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    a = parser().parse_args(argv)
    a.dir = a.dir or a.dir_top
    try:
        return COMMANDS[a.cmd](a)
    except Stop as e:
        say(f'\nСТОП: {e}')
        return e.code
    except KeyboardInterrupt:
        say('\nПрервано. Состояние сохранено в fid1c_state.json; шаг, который шёл, помечен как прерванный.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
