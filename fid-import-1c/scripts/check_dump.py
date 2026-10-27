#!/usr/bin/env python3
"""Проверка XML-выгрузки 1С (1CV8DtUD) на целостность до любой загрузки.

Ищет типичное повреждение больших выгрузок: файл обрывается и начинается заново
(второй заголовок `<?xml` внутри файла, обычно ровно на отметке 4 ГиБ), и проверяет,
что файл закрыт тегом `</V8Exch:_1CV8DtUD>`.

    python check_dump.py <dump.xml> [--json out.json]

Код выхода: 0 — файл целый, 1 — повреждён, 2 — ошибка чтения.
Только стандартная библиотека Python 3.8+: работает и локально, и в песочнице чата.
"""
import argparse
import json
import os
import sys

HEADER = b'<?xml'
CLOSE = b'</V8Exch:_1CV8DtUD>'
ROOT = b'<V8Exch:_1CV8DtUD'
CHUNK = 64 * 1024 * 1024


def find_headers(path, size):
    """Смещения всех заголовков `<?xml` (с BOM или без) в файле."""
    found = []
    overlap = len(HEADER) + 3
    with open(path, 'rb') as f:
        pos = 0
        prev = b''
        while pos < size:
            block = f.read(CHUNK)
            if not block:
                break
            data = prev + block
            base = pos - len(prev)
            i = data.find(HEADER)
            while i != -1:
                off = base + i
                if data[max(i - 3, 0):i] == b'\xef\xbb\xbf':
                    off -= 3
                found.append(off)
                i = data.find(HEADER, i + 1)
            prev = data[-overlap:]
            pos += len(block)
    return sorted(set(found))


def same_bytes(path, a, b, length):
    """Совпадают ли `length` байт файла с позиций a и b (сравнение блоками)."""
    with open(path, 'rb') as f1, open(path, 'rb') as f2:
        f1.seek(a)
        f2.seek(b)
        left = length
        while left > 0:
            n = min(CHUNK, left)
            if f1.read(n) != f2.read(n):
                return False
            left -= n
    return True


def check(path):
    """Отчёт о целостности файла (dict). Бросает OSError, если файл не читается."""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = f.read(4096)
        f.seek(max(size - 4096, 0))
        tail = f.read()
    headers = find_headers(path, size)
    restarts = [h for h in headers if h > 0]
    closed = CLOSE in tail
    report = {
        'file': os.path.abspath(path),
        'size_bytes': size,
        'is_1c_dump': ROOT in head,
        'restarts_at': restarts,
        'closed_properly': closed,
        'usable_bytes': restarts[0] if restarts else size,
        'copies_identical': None,
        'ok': ROOT in head and not restarts and closed,
    }
    if restarts:
        first = restarts[0]
        report['copies_identical'] = same_bytes(path, 0, first, min(first, size - first, 256 * 1024 * 1024))
    return report


def describe(report):
    """Текст для человека: что с файлом и что делать."""
    size = f"{report['size_bytes']:,}".replace(',', ' ')
    lines = [f"Файл: {report['file']} ({size} байт)"]
    if report['ok']:
        lines.append('Целый: заголовок один, файл закрыт тегом </V8Exch:_1CV8DtUD>.')
        return lines
    if not report['is_1c_dump']:
        lines.append('Это не выгрузка 1С «Выгрузка/загрузка данных XML» (нет корневого тега V8Exch:_1CV8DtUD).')
    if report['restarts_at']:
        gib = ', '.join(f'{r:,} ({r / 2**30:.2f} ГиБ)'.replace(',', ' ') for r in report['restarts_at'])
        lines.append(f'ПОВРЕЖДЁН: файл начинается заново на смещениях {gib}.')
        if report['copies_identical']:
            lines.append('Куски совпадают с началом файла: реальных данных только до первой отметки, остальное — повторы.')
        usable = f"{report['usable_bytes']:,}".replace(',', ' ')
        lines.append(f'Пригодная часть: первые {usable} байт; всё, что шло в выгрузке дальше, потеряно.')
    if not report['closed_properly']:
        lines.append('Файл не закрыт тегом </V8Exch:_1CV8DtUD>: выгрузка оборвана.')
    lines.append('Нужна новая выгрузка из 1С (без регистров ФИАС АдресныеОбъекты/АдминистративнаяИерархия), '
                 'сохранённая на NTFS-диск и переданная архивом 7-Zip.')
    return lines


def main(argv=None):
    # консоль Windows по умолчанию не UTF-8 — без этого русский текст выводится кракозябрами
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='Проверка целостности XML-выгрузки 1С')
    ap.add_argument('dump')
    ap.add_argument('--json')
    args = ap.parse_args(argv)
    try:
        report = check(args.dump)
    except OSError as e:
        print(f'Ошибка чтения: {e}')
        return 2
    print('\n'.join(describe(report)))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
