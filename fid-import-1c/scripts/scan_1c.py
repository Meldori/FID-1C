#!/usr/bin/env python3
"""Обзор XML-выгрузки 1С (1CV8DtUD): какие объекты есть, сколько их, какая это конфигурация.

Файл режется на объекты верхнего уровня построчно (строка с двумя табуляциями
`\t\t<v8:Тип>` открывает объект, `\t\t</v8:…>` закрывает), каждый объект разбирается
отдельно. Так обходится ограничение XML-парсера Python на файлах больше 4 ГБ.

    python scan_1c.py <dump.xml> <out_dir> [--count-copies]

На повреждённом файле (второй заголовок <?xml) обзор останавливается на повторе, чтобы не считать копии.

Пишет в out_dir:
  scan.json    — {"counts": [[тип, количество], ...], "samples": {тип: [2 примера]}}
  summary.md   — конфигурация, ключевые объекты, чего не хватает для импорта
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
FIAS = ['InformationRegisterRecordSet.АдресныеОбъекты', 'InformationRegisterRecordSet.АдминистративнаяИерархия']


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


def scan(path, stop_at_restart):
    counts = collections.Counter()
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
    return counts, samples, order, restarted


def main():
    # консоль Windows по умолчанию не UTF-8 — без этого русский текст выводится кракозябрами
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('out_dir')
    ap.add_argument('--count-copies', action='store_true',
                    help='не останавливаться на повторном заголовке <?xml (по умолчанию повторы не считаются)')
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    counts, samples, order, restarted = scan(args.dump, not args.count_copies)

    with open(os.path.join(args.out_dir, 'scan.json'), 'w', encoding='utf-8') as f:
        json.dump({'counts': counts.most_common(), 'order': order, 'samples': samples}, f, ensure_ascii=False, default=str)

    kind, label = detect_configuration(counts)
    lines = ['# Обзор выгрузки 1С', '', f'Файл: `{args.dump}`', f'Конфигурация: **{label}**', '']
    if restarted:
        lines += ['**Внимание:** в файле повторный заголовок `<?xml` — выгрузка повреждена (`check_dump.py`); посчитано только до первого повтора.', '']
    lines += ['| Что нужно импорту | Объект 1С | Количество |', '| --- | --- | --- |']
    missing = []
    for what, types in KEY_OBJECTS.items():
        present = [(t, counts[t]) for t in types if counts.get(t)]
        if present:
            for t, n in present:
                lines.append(f'| {what} | `{t}` | {n} |')
        else:
            lines.append(f'| {what} | нет | 0 |')
            missing.append(what)
    lines.append('')
    # Вердикт готовности: без пациентов, счетов или при повреждённом файле грузить нельзя;
    # без контактов, цен, паспортов или графиков — можно, но клиника получит неполные данные
    critical = [w for w in missing if w in ('Пациенты', 'Счета (оказание услуг)', 'Записи в календарь')]
    if restarted or critical:
        reason = 'файл повреждён' if restarted else 'нет: ' + ', '.join(critical)
        verdict = f'НЕЛЬЗЯ грузить — {reason}'
    elif missing:
        verdict = 'МОЖНО С ОГОВОРКАМИ — в программе не будет: ' + ', '.join(missing)
    else:
        verdict = 'МОЖНО грузить — всё нужное для импорта есть'
    lines.insert(4, f'Готовность: **{verdict}**')
    lines.insert(5, '')
    fias = sum(counts.get(t, 0) for t in FIAS)
    if fias:
        lines.append(f'ФИАС в выгрузке: {fias} записей — для импорта не нужен, при повторной выгрузке исключить.')
    if missing:
        lines.append('Не хватает: ' + ', '.join(missing) + '.')
    regs = [t for t in order if t.startswith(('InformationRegister', 'AccumulationRegister'))]
    if regs and set(regs) <= set(FIAS):
        lines.append('Из регистров в файле только ФИАС: всё, что шло после него, вероятно, обрезано.')
    lines += ['', f'Всего объектов: {sum(counts.values())}, типов: {len(counts)}.']
    with open(os.path.join(args.out_dir, 'summary.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    sys.stdout.buffer.write(('\n'.join(lines) + f'\n\nКонфигурация: {kind}\n').encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
