#!/usr/bin/env python3
"""Обзор XML-выгрузки 1С (1CV8DtUD): какие объекты есть, сколько их, какая это конфигурация.

Файл режется на объекты верхнего уровня построчно (строка с двумя табуляциями
`\t\t<v8:Тип>` открывает объект, `\t\t</v8:…>` закрывает), каждый объект разбирается
отдельно. Так обходится ограничение XML-парсера Python на файлах больше 4 ГБ.

    python scan_1c.py <dump.xml> <out_dir> [--count-copies]

На повреждённом файле (второй заголовок <?xml) обзор останавливается на повторе, чтобы не считать копии.

Пишет в out_dir:
  scan.json    — количества, помеченные на удаление, период документов, по 2 примера каждого типа
  summary.md   — конфигурация, ключевые объекты, чего не хватает для импорта
Только стандартная библиотека Python 3.8+.
"""
import argparse
import collections
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

NS = re.compile(r'^\{[^}]*\}')
OPEN = re.compile(rb'^\t\t<v8:([^\s>/]+)')
FIELD = re.compile(rb'^\t\t\t<v8:(Date|DeletionMark|Posted)>([^<]*)<')
WRAP = (b'<r xmlns:v8="http://v8.1c.ru/8.1/data/enterprise/current-config" '
        b'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:core="http://v8.1c.ru/data">')

# Объекты, которые нужны импорту Future IT Dent, по смыслу
KEY_OBJECTS = {
    'Филиал': ['CatalogObject.Филиалы', 'CatalogObject.СтруктурныеЕдиницы'],
    'Пациенты': ['CatalogObject.Клиенты', 'CatalogObject.Контрагенты'],
    'Медкарты': ['CatalogObject.МедицинскиеКарты'],
    'Сотрудники': ['CatalogObject.Сотрудники'],
    'Должности': ['CatalogObject.Должности'],
    'Номенклатура': ['CatalogObject.Номенклатура'],
    'Цены': ['InformationRegisterRecordSet.ЦеныНоменклатуры'],
    'Контакты (телефоны, email)': ['InformationRegisterRecordSet.КонтактнаяИнформация'],
    'Паспорта': ['InformationRegisterRecordSet.ДокументыФизическихЛиц', 'InformationRegisterRecordSet.ПаспортныеДанные'],
    'Графики врачей': ['InformationRegisterRecordSet.ВремяРаботыСотрудниковПлан', 'InformationRegisterRecordSet.ГрафикиРаботы'],
    'Записи в календарь': ['DocumentObject.Событие', 'DocumentObject.Заявка'],
    'Счета (оказание услуг)': ['DocumentObject.Прием', 'DocumentObject.ОказаниеУслуг'],
    # в Стоматологической клинике оплаты — табличная часть Оплата внутри ОказаниеУслуг
    'Оплаты': ['DocumentObject.ПоступлениеДенежныхСредств', 'DocumentObject.ПриходныйКассовыйОрдер',
               'DocumentObject.ОплатаПлатежнойКартой', 'DocumentObject.ОказаниеУслуг'],
}
CRITICAL = ('Пациенты', 'Счета (оказание услуг)', 'Записи в календарь')
FIAS = ['InformationRegisterRecordSet.АдресныеОбъекты', 'InformationRegisterRecordSet.АдминистративнаяИерархия']
# В УНФ телефоны и email лежат в карточке контрагента, отдельный регистр не нужен
INLINE_CONTACTS = {'unf': ['Контакты (телефоны, email)']}


def todict(e):
    ch = list(e)
    if not ch:
        return e.text
    d = {}
    for c in ch:
        k = NS.sub('', c.tag)
        v = todict(c)
        if k in d:
            if not isinstance(d[k], list):
                d[k] = [d[k]]
            if len(d[k]) < 3:
                d[k].append(v)
        else:
            d[k] = v
    return d


def detect_configuration(counts):
    has = lambda t: counts.get(t, 0) > 0
    if has('CatalogObject.Клиенты') and has('DocumentObject.ОказаниеУслуг'):
        return 'stom', '1С:Стоматологическая клиника (Клиенты, Заявка, ОказаниеУслуг) — см. references/config-stom.md'
    if has('CatalogObject.Контрагенты') and has('CatalogObject.СтруктурныеЕдиницы'):
        return 'unf', '1С:Медицина на базе УНФ (Контрагенты, СтруктурныеЕдиницы, Событие, Прием) — см. references/config-unf.md'
    return 'unknown', 'Конфигурация не распознана — изучить вручную по references/explore.md'


def scan(path, stop_at_restart=True):
    """Один проход по файлу. Возвращает dict: counts, deleted, posted, dates, samples, order, restarted."""
    counts = collections.Counter()
    deleted = collections.Counter()
    posted = collections.Counter()
    dates = {}
    samples = collections.defaultdict(list)
    order = []
    tag = None
    buf = []
    restarted = False
    with open(path, 'rb') as f:
        for line in f:
            if b'<?xml' in line and order:
                restarted = True
                if stop_at_restart:
                    break
            if tag is None:
                m = OPEN.match(line)
                if not m:
                    continue
                tag = m.group(1).decode('utf-8')
                buf = [line]
                single = line.rstrip().endswith(b'/>') or (b'</v8:' + m.group(1) + b'>') in line
                if not single:
                    continue
            else:
                buf.append(line)
                fm = FIELD.match(line)
                if fm:
                    name, value = fm.group(1), fm.group(2).decode('utf-8', 'replace')
                    if name == b'DeletionMark' and value == 'true':
                        deleted[tag] += 1
                    elif name == b'Posted' and value == 'true':
                        posted[tag] += 1
                    elif name == b'Date' and tag.startswith('DocumentObject.') and not value.startswith('0001'):
                        day = value[:10]
                        lo, hi = dates.get(tag, (day, day))
                        dates[tag] = (min(lo, day), max(hi, day))
                if not line.startswith(b'\t\t</v8:'):
                    continue
            if tag not in counts:
                order.append(tag)
            counts[tag] += 1
            if len(samples[tag]) < 2:
                raw = b''.join(buf)
                if len(raw) < 300000:
                    try:
                        samples[tag].append(todict(ET.fromstring(WRAP + raw + b'</r>')[0]))
                    except ET.ParseError:
                        pass
            tag = None
            buf = []
    return {'counts': counts, 'deleted': deleted, 'posted': posted, 'dates': dates,
            'samples': samples, 'order': order, 'restarted': restarted}


def analyze(result):
    """Что из нужного импорту есть, чего нет, и вердикт готовности по составу выгрузки."""
    counts = result['counts']
    kind, label = detect_configuration(counts)
    rows, missing = [], []
    for what, types in KEY_OBJECTS.items():
        present = [(t, counts[t], result['deleted'].get(t, 0)) for t in types if counts.get(t)]
        if what in INLINE_CONTACTS.get(kind, []) and not present:
            rows.append({'what': what, 'object': 'карточка Контрагенты.КонтактнаяИнформация', 'count': None, 'deleted': 0})
            continue
        if present:
            for t, n, d in present:
                rows.append({'what': what, 'object': t, 'count': n, 'deleted': d})
        else:
            rows.append({'what': what, 'object': None, 'count': 0, 'deleted': 0})
            missing.append(what)
    critical = [w for w in missing if w in CRITICAL]
    if result['restarted'] or critical:
        level = 'НЕЛЬЗЯ'
        reason = 'файл повреждён' if result['restarted'] else 'нет: ' + ', '.join(critical)
    elif missing or kind == 'unknown':
        level = 'МОЖНО С ОГОВОРКАМИ'
        reason = 'в программе не будет: ' + ', '.join(missing) if missing else 'конфигурация не распознана, нужна ручная проверка'
    else:
        level, reason = 'МОЖНО', 'всё нужное для импорта есть'
    regs = [t for t in result['order'] if t.startswith(('InformationRegister', 'AccumulationRegister'))]
    days = [d for d in result['dates'].values()]
    period = (min(d[0] for d in days), max(d[1] for d in days)) if days else None
    return {'config': kind, 'config_label': label, 'rows': rows, 'missing': missing, 'critical': critical,
            'level': level, 'reason': reason, 'period': period,
            'fias': sum(counts.get(t, 0) for t in FIAS),
            'only_fias_registers': bool(regs) and set(regs) <= set(FIAS),
            'total_objects': sum(counts.values()), 'types': len(counts)}


def summary_lines(path, result, info):
    lines = ['# Обзор выгрузки 1С', '', f'Файл: `{path}`', f"Конфигурация: **{info['config_label']}**",
             f"Готовность: **{info['level']} — {info['reason']}**", '']
    if info['period']:
        lines += [f"Период документов: {info['period'][0]} — {info['period'][1]}", '']
    if result['restarted']:
        lines += ['**Внимание:** в файле повторный заголовок `<?xml` — выгрузка повреждена (`check_dump.py`); посчитано только до первого повтора.', '']
    lines += ['| Что нужно импорту | Объект 1С | Количество | Помечено на удаление |', '| --- | --- | --- | --- |']
    for r in info['rows']:
        count = 'в карточке' if r['count'] is None else r['count']
        lines.append(f"| {r['what']} | {'`' + r['object'] + '`' if r['object'] else 'нет'} | {count} | {r['deleted'] or ''} |")
    lines.append('')
    if info['fias']:
        lines.append(f"ФИАС в выгрузке: {info['fias']} записей — для импорта не нужен, при повторной выгрузке исключить.")
    if info['missing']:
        lines.append('Не хватает: ' + ', '.join(info['missing']) + '.')
    if info['only_fias_registers']:
        lines.append('Из регистров в файле только ФИАС: всё, что шло после него, вероятно, обрезано.')
    lines += ['', f"Всего объектов: {info['total_objects']}, типов: {info['types']}."]
    return lines


def write_outputs(path, out_dir, result, info):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'scan.json'), 'w', encoding='utf-8') as f:
        json.dump({'counts': result['counts'].most_common(), 'deleted': dict(result['deleted']),
                   'posted': dict(result['posted']), 'dates': result['dates'], 'order': result['order'],
                   'restarted': result['restarted'], 'analysis': info, 'samples': result['samples']},
                  f, ensure_ascii=False, default=str)
    text = '\n'.join(summary_lines(path, result, info)) + '\n'
    with open(os.path.join(out_dir, 'summary.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    return text


def main(argv=None):
    # консоль Windows по умолчанию не UTF-8 — без этого русский текст выводится кракозябрами
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='Обзор XML-выгрузки 1С')
    ap.add_argument('dump')
    ap.add_argument('out_dir')
    ap.add_argument('--count-copies', action='store_true',
                    help='не останавливаться на повторном заголовке <?xml (по умолчанию повторы не считаются)')
    args = ap.parse_args(argv)
    result = scan(args.dump, not args.count_copies)
    info = analyze(result)
    text = write_outputs(args.dump, args.out_dir, result, info)
    sys.stdout.write(text + f"\nКонфигурация: {info['config']}\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
