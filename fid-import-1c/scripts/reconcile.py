#!/usr/bin/env python3
"""Итоговая сверка импорта 1С → Future IT Dent: «совпало N из M» по каждой сущности и общий счёт.

    python reconcile.py --work <папка с V8Exch_Data> --db <папка экспорта> --out <report.md>
                        [--tz Europe/Samara | --tz-hours 4] [--json report.json]

--work  рабочая папка выгрузки (та, что передавалась get_from_1c; внутри V8Exch_Data/)
--db    папка, куда `fid_bridge.php export` выгрузил загруженное
--tz    часовой пояс филиала; по умолчанию берётся из branches.time_zone. Если в Python есть база
        поясов (zoneinfo + tzdata), учитываются исторические смены поясов, иначе — постоянный сдвиг
--tz-hours  постоянный сдвиг от UTC вместо пояса

Что считается «совпало»:
  пациенты     — есть в базе, ФИО (без учёта регистра), дата рождения, телефон и email, если есть в 1С
  сотрудники   — есть врачом или сотрудником, ФИО
  услуги       — есть, название
  записи       — пациент, врач, начало, конец, статус визита (дубли в самой 1С считаются одной записью)
  счета        — есть, сумма = СуммаДокумента, пациент
  оплаты       — каждая оплата (пациент + сумма) нашлась среди денежных операций
Код выхода: 0 — всё совпало, 1 — есть расхождения.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys

ZERO = '00000000-0000-0000-0000-000000000000'
TZ = {'Europe/Kaliningrad': 2, 'Europe/Moscow': 3, 'Europe/Samara': 4, 'Europe/Ulyanovsk': 4, 'Europe/Saratov': 4,
      'Europe/Astrakhan': 4, 'Asia/Yekaterinburg': 5, 'Asia/Omsk': 6, 'Asia/Novosibirsk': 7, 'Asia/Barnaul': 7,
      'Asia/Tomsk': 7, 'Asia/Novokuznetsk': 7, 'Asia/Krasnoyarsk': 7, 'Asia/Irkutsk': 8, 'Asia/Chita': 9,
      'Asia/Yakutsk': 9, 'Asia/Vladivostok': 10, 'Asia/Sakhalin': 11, 'Asia/Magadan': 11, 'Asia/Kamchatka': 12}
# состояние в 1С → visit_id в Future IT Dent (как в Appointment1C.php)
VISIT = {'ПриемНачат': 1, 'ПациентВКресле': 1, 'ПациентОжидает': 2, 'Запланировано': 5, 'Запланирована': 5,
         'Подтверждена': 6, 'ПриемОкончен': 7, 'Выполнена': 7, 'Прием': 7,
         'ПриемОтменен': 4, 'ПриемНеСостоялся': 4, 'НеПодтверждена': 4, 'Отменена': 4}


def local_clock(tz_name, tz_hours):
    """Функция UTC → местное время филиала и её описание для отчёта."""
    if tz_hours is None and tz_name:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(tz_name)
            utc = dt.timezone.utc
            return (lambda t: t.replace(tzinfo=utc).astimezone(zone).replace(tzinfo=None)), f'{tz_name} (с историей поясов)'
        except Exception:  # нет zoneinfo/tzdata (часто на Windows) — постоянный сдвиг
            pass
    hours = tz_hours if tz_hours is not None else TZ.get(tz_name, 3)
    shift = dt.timedelta(hours=hours)
    return (lambda t: t + shift), f'{tz_name or "без пояса"}, UTC+{hours}'


def lst(v):
    return [] if v is None else (v if isinstance(v, list) else [v])


def s(v):
    return '' if v is None or isinstance(v, (dict, list)) else str(v).strip()


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def t1c(v):
    return dt.datetime.strptime(s(v).replace('_', ':')[:19], '%Y-%m-%dT%H:%M:%S')


def digits(v):
    return re.sub(r'\D', '', s(v))[-10:]


class Source:
    def __init__(self, work):
        self.dir = os.path.join(work, 'V8Exch_Data')

    def has(self, name):
        return os.path.exists(os.path.join(self.dir, f'v8_{name}.json'))

    def load(self, name):
        path = os.path.join(self.dir, f'v8_{name}.json')
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding='utf-8') as fh:
                d = json.load(fh)
        except ValueError:
            return None  # файл битый (например, HTML в полях) — вызывающий решает, что делать
        d = d.get('v8_' + name, d) if isinstance(d, dict) else d
        return d if isinstance(d, list) else [d]


def db(dir_, name):
    path = os.path.join(dir_, name + '.json')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


class Entity:
    def __init__(self, name):
        self.name, self.expected, self.loaded, self.matched = name, 0, 0, 0
        self.problems = collections.defaultdict(list)

    def bad(self, kind, example):
        self.problems[kind].append(example)


def reconcile(work, dbdir, tz_hours=None, tz_name=None):
    src = Source(work)
    if not os.path.isdir(src.dir):
        raise SystemExit(f'Нет папки {src.dir}: сначала разберите выгрузку (fid1c.py parse)')
    stom = src.has('CatalogObject.Клиенты') and src.has('DocumentObject.ОказаниеУслуг')
    branches = db(dbdir, 'branches')
    if not tz_name:
        zones = [b.get('time_zone') for b in branches if b.get('import_id')] or [b.get('time_zone') for b in branches]
        tz_name = zones[0] if zones else None
    to_local, tz_label = local_clock(tz_name, tz_hours)
    ents = []

    # ---------- пациенты
    pu = {r['import_id']: r for r in db(dbdir, 'personal_users')}
    users = {r['import_id']: r for r in db(dbdir, 'users')}
    pat = {r['import_id']: r for r in db(dbdir, 'patients')}
    clients = src.load('CatalogObject.Клиенты' if stom else 'CatalogObject.Контрагенты') or []
    deleted = {k['v8_Ref'] for k in clients if k.get('v8_DeletionMark') == 'true'}
    e = Entity('Пациенты')
    for k in clients:
        if k.get('v8_IsFolder') == 'true' or k.get('v8_DeletionMark') == 'true':
            continue
        if not stom and k.get('v8_ТипКонтрагента') not in (None, 'Клиенты'):
            continue
        e.expected += 1
        ref = k['v8_Ref']
        r = pu.get(ref)
        if not r or ref not in pat:
            e.bad('нет в базе', s(k.get('v8_Description')))
            continue
        e.loaded += 1
        ok = True
        name = ' '.join([s(k.get('v8_Фамилия')) or s(k.get('v8_Description')), s(k.get('v8_Имя')), s(k.get('v8_Отчество'))]).lower().split()
        if name != ' '.join([s(r.get('last_name')), s(r.get('first_name')), s(r.get('second_name'))]).lower().split():
            ok = False
            e.bad('ФИО', (s(k.get('v8_Description')), r.get('last_name'), r.get('first_name')))
        bd = s(k.get('v8_ДатаРожденияКлиента') or k.get('v8_ДатаРождения'))[:10]
        bd = '1970-01-01' if not bd or bd.startswith('0001') else bd
        if s(r.get('birthdate'))[:10] != bd:
            ok = False
            e.bad('дата рождения', (s(k.get('v8_Description')), bd, r.get('birthdate')))
        contacts = lst(k.get('v8_КонтактнаяИнформация'))
        phones = [digits(c.get('v8_НомерТелефона')) for c in contacts if c.get('v8_Тип') == 'Телефон' and digits(c.get('v8_НомерТелефона'))]
        if phones and digits(r.get('phone')) not in phones:
            ok = False
            e.bad('телефон', (s(k.get('v8_Description')), phones, r.get('phone')))
        mails = [s(c.get('v8_АдресЭП') or c.get('v8_Представление')).lower() for c in contacts if c.get('v8_Тип') == 'АдресЭлектроннойПочты']
        if mails and s((users.get(ref) or {}).get('email')).lower() not in mails:
            ok = False
            e.bad('email', (s(k.get('v8_Description')), mails, (users.get(ref) or {}).get('email')))
        e.matched += ok
    ents.append(e)

    # ---------- сотрудники
    docs = {r['import_id'] for r in db(dbdir, 'doctors')}
    emps = {r['import_id'] for r in db(dbdir, 'employees')}
    e = Entity('Сотрудники и врачи')
    for x in src.load('CatalogObject.Сотрудники') or []:
        if x.get('v8_IsFolder') == 'true':
            continue
        e.expected += 1
        ref = x['v8_Ref']
        if ref not in docs and ref not in emps:
            e.bad('нет в базе', s(x.get('v8_Description')))
            continue
        e.loaded += 1
        r = pu.get(ref, {})
        if s(x.get('v8_Фамилия')).lower() == s(r.get('last_name')).lower() and s(x.get('v8_Имя')).lower() == s(r.get('first_name')).lower():
            e.matched += 1
        else:
            e.bad('ФИО', (s(x.get('v8_Description')), r.get('last_name'), r.get('first_name')))
    ents.append(e)

    # ---------- услуги
    svc = {r['import_id']: r for r in db(dbdir, 'services')}
    e = Entity('Услуги')
    for x in src.load('CatalogObject.Номенклатура') or []:
        if x.get('v8_IsFolder') == 'true':
            continue
        is_service = x.get('v8_ЭтоУслуга') == 'true' if 'v8_ЭтоУслуга' in x else x.get('v8_ВидНоменклатуры', 'Услуга') == 'Услуга'
        if not is_service:
            continue
        e.expected += 1
        r = svc.get(x['v8_Ref'])
        if not r:
            e.bad('нет в базе', s(x.get('v8_Description')))
            continue
        e.loaded += 1
        names = {s(x.get('v8_НаименованиеПолное')), s(x.get('v8_Description'))}
        if s(r.get('name')) in names:
            e.matched += 1
        else:
            e.bad('название', (s(x.get('v8_Description')), r.get('name')))
    ents.append(e)

    # ---------- записи
    pat_id = {k: v['id'] for k, v in pat.items()}
    doc_id = {r['import_id']: r['id'] for r in db(dbdir, 'doctors')}
    expected = collections.Counter()
    if src.has('DocumentObject.Заявка'):
        states = {x['v8_Ref']: s(x.get('v8_PredefinedDataName')) or s(x.get('v8_Description')) for x in src.load('CatalogObject.ВидыСостоянийЗаявок') or []}
        for z in src.load('DocumentObject.Заявка') or []:
            if z.get('v8_Клиент') in (None, ZERO) or z['v8_Клиент'] in deleted:
                continue
            v = VISIT.get(states.get(z.get('v8_Состояние')), 5)
            expected[(z['v8_Клиент'], z.get('v8_Сотрудник'), t1c(z['v8_ДатаНачала']), t1c(z['v8_ДатаОкончания']), v)] += 1
    else:
        for x in src.load('DocumentObject.Событие') or []:
            if x.get('v8_Posted') != 'true' or x.get('v8_Контрагент') in (None, ZERO) or x['v8_Контрагент'] in deleted:
                continue
            for u in lst(x.get('v8_Услуги'))[:1]:
                if not u.get('v8_ДатаНачала'):
                    continue
                v = VISIT.get(s(x.get('v8_Статус')), 5)
                expected[(x['v8_Контрагент'], u.get('v8_Сотрудник'), t1c(u['v8_ДатаНачала']), t1c(u['v8_ДатаОкончания']), v)] += 1
    inv_pat = {v: k for k, v in pat_id.items()}
    inv_doc = {v: k for k, v in doc_id.items()}
    got = collections.Counter()
    for a in db(dbdir, 'appointments'):
        b = to_local(dt.datetime.strptime(a['time_begin'][:19], '%Y-%m-%d %H:%M:%S'))
        en = to_local(dt.datetime.strptime(a['time_end'][:19], '%Y-%m-%d %H:%M:%S'))
        got[(inv_pat.get(a['patient_id']), inv_doc.get(a['doctor_id']), b, en, a['visit_id'])] += 1
    e = Entity('Записи в календаре')
    e.expected = len(expected)
    e.loaded = sum(got.values())
    e.matched = sum(1 for k in expected if got.get(k))
    for k in list(expected)[:]:
        if not got.get(k):
            e.bad('нет или не совпала', (k[2].isoformat(sep=' '), k[4]))
    dup = sum(v - 1 for v in expected.values() if v > 1)
    if dup:
        e.bad('дубли в самой 1С (слиты в одну)', dup)
    extra = sum((got - collections.Counter({k: 1 for k in expected})).values())
    if extra:
        e.bad('лишние в базе', extra)
    ents.append(e)

    # ---------- счета
    inv = {r['import_id']: r for r in db(dbdir, 'invoice')}
    docs_src = src.load('DocumentObject.ОказаниеУслуг') if stom else src.load('DocumentObject.Прием')
    e = Entity('Счета')
    sales = []
    for x in docs_src or []:
        client = x.get('v8_Клиент') or x.get('v8_Контрагент')
        if x.get('v8_Posted') != 'true' or client in (None, ZERO) or client in deleted or x.get('v8_ВидОперации') == 'ВозвратКлиенту':
            continue
        sales.append(x)
        e.expected += 1
        r = inv.get(x['v8_Ref'])
        if not r:
            e.bad('нет в базе', s(x.get('v8_Number')))
            continue
        e.loaded += 1
        if abs(f(r.get('price')) - f(x.get('v8_СуммаДокумента'))) > 0.01:
            e.bad('сумма', (s(x.get('v8_Number')), x.get('v8_СуммаДокумента'), r.get('price')))
        elif r.get('patient_id') != pat_id.get(client):
            e.bad('пациент', s(x.get('v8_Number')))
        else:
            e.matched += 1
    ents.append(e)
    sum_src = sum(f(x.get('v8_СуммаДокумента')) for x in sales)
    sum_db = sum(f(r.get('price')) for r in inv.values() if r.get('patient_id') in inv_pat)

    # ---------- оплаты
    exp_pay = collections.Counter()
    if stom:
        for x in sales:
            for o in lst(x.get('v8_Оплата')):
                exp_pay[(x['v8_Клиент'], round(f(o.get('v8_Сумма')), 2))] += 1
        for name in ('DocumentObject.ПриходныйКассовыйОрдер', 'DocumentObject.ОплатаПлатежнойКартой'):
            for x in src.load(name) or []:
                if x.get('v8_Posted') == 'true' and x.get('v8_DeletionMark') != 'true' and x.get('v8_Клиент') not in deleted:
                    exp_pay[(x['v8_Клиент'], round(f(x.get('v8_СуммаДокумента')), 2))] += 1
    else:
        for x in src.load('DocumentObject.ПоступлениеДенежныхСредств') or []:
            if x.get('v8_Posted') != 'true' or x.get('v8_ВидОперации') == 'ПоступлениеРозничнойВыручкиИзОперационнойКассы':
                continue
            if x.get('v8_Контрагент') in deleted or x.get('v8_Контрагент') not in pat_id:
                continue
            for r in lst(x.get('v8_РасшифровкаПлатежа')):
                exp_pay[(x['v8_Контрагент'], round(f(r.get('v8_Сумма')), 2))] += 1
    got_pay = collections.Counter()
    for m in db(dbdir, 'money'):
        if m.get('money_operation_type') in ('invoice_paid', 'prepayment'):
            got_pay[(inv_pat.get(m['patient_id']), round(f(m.get('money')), 2))] += 1
    e = Entity('Оплаты')
    e.expected = sum(exp_pay.values())
    e.loaded = sum(got_pay.values())
    e.matched = sum((exp_pay & got_pay).values())
    miss = exp_pay - got_pay
    for k, n in list(miss.items())[:20]:
        e.bad('нет в базе', (k[1], n))
    if sum((got_pay - exp_pay).values()):
        e.bad('лишние в базе', sum((got_pay - exp_pay).values()))
    ents.append(e)
    pay_src = sum(k[1] * n for k, n in exp_pay.items())
    pay_db = sum(k[1] * n for k, n in got_pay.items())
    return ents, {'config': 'stom' if stom else 'unf', 'tz': tz_label,
                  'invoices_sum_1c': round(sum_src, 2), 'invoices_sum_db': round(sum_db, 2),
                  'payments_sum_1c': round(pay_src, 2), 'payments_sum_db': round(pay_db, 2)}


def render(ents, totals):
    m = sum(e.matched for e in ents)
    n = sum(e.expected for e in ents)
    # вниз до десятых: 2586/2587 — это 99.9%, а не «100.0%»
    pct = int(1000 * m / n) / 10 if n else 100
    verdict = 'Всё перенесено и совпадает с 1С' if m == n else 'Есть расхождения — разобрать перед переносом в рабочую базу'
    out = [f'# Итог сверки: {m}/{n} ({pct:.1f}%)', '', f'**{verdict}.**', '',
           '| Сущность | В 1С | Загружено | Совпало | Итог |', '| --- | --- | --- | --- | --- |']
    for e in ents:
        out.append(f'| {e.name} | {e.expected} | {e.loaded} | {e.matched} | {e.matched}/{e.expected} |')
    out += ['', '| Деньги | 1С | База |', '| --- | --- | --- |',
            f"| Сумма счетов (₽) | {totals['invoices_sum_1c']:,.2f} | {totals['invoices_sum_db']:,.2f} |".replace(',', ' '),
            f"| Сумма оплат (₽) | {totals['payments_sum_1c']:,.2f} | {totals['payments_sum_db']:,.2f} |".replace(',', ' '),
            '', f"Конфигурация: {totals['config']}, часовой пояс филиала: {totals['tz']}.", '']
    probs = [(e, k, v) for e in ents for k, v in e.problems.items()]
    if probs:
        out.append('## Расхождения')
        out.append('')
        for e, k, v in probs:
            count = v[0] if len(v) == 1 and isinstance(v[0], int) else len(v)
            examples = '' if len(v) == 1 and isinstance(v[0], int) else ' — например: ' + '; '.join(map(str, v[:3]))
            out.append(f'- **{e.name}, {k}:** {count}{examples}')
    return '\n'.join(out) + '\n', m, n


def as_json(ents, totals):
    m = sum(e.matched for e in ents)
    n = sum(e.expected for e in ents)
    return {'score': [m, n], 'totals': totals,
            'entities': [{'name': e.name, 'expected': e.expected, 'loaded': e.loaded, 'matched': e.matched,
                          'problems': {k: v[:20] for k, v in e.problems.items()}} for e in ents]}


def run(work, dbdir, out, json_out=None, tz_hours=None, tz_name=None):
    """Сверка с записью отчётов. Возвращает (текст, данные)."""
    ents, totals = reconcile(work, dbdir, tz_hours, tz_name)
    text, m, n = render(ents, totals)
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(text)
    data = as_json(ents, totals)
    if json_out:
        with open(json_out, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
    return text, data


def main(argv=None):
    # консоль Windows по умолчанию не UTF-8 — без этого русский текст выводится кракозябрами
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='Сверка загруженного в Future IT Dent с выгрузкой 1С')
    ap.add_argument('--work', required=True)
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--json')
    ap.add_argument('--tz')
    ap.add_argument('--tz-hours', type=int)
    a = ap.parse_args(argv)
    text, data = run(a.work, a.db, a.out, a.json, a.tz_hours, a.tz)
    sys.stdout.write(text)
    m, n = data['score']
    return 0 if m == n else 1


if __name__ == '__main__':
    sys.exit(main())
