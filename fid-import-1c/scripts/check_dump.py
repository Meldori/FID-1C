#!/usr/bin/env python3
"""Проверка XML-выгрузки 1С (1CV8DtUD) на целостность до любой загрузки.

Ищет типичное повреждение больших выгрузок: файл обрывается и начинается заново
(второй заголовок `<?xml` внутри файла, обычно ровно на отметке 4 ГиБ), и проверяет,
что файл закрыт тегом `</V8Exch:_1CV8DtUD>`.

    python check_dump.py <dump.xml> [--json out.json]

Код выхода: 0 — файл целый, 1 — повреждён, 2 — ошибка чтения.
"""
import argparse
import json
import os
import sys

HEADER = b'<?xml'
CLOSE = b'</V8Exch:_1CV8DtUD>'
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
                if not found or found[-1] != off:
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


def main():
    # консоль Windows по умолчанию не UTF-8 — без этого русский текст выводится кракозябрами
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--json')
    args = ap.parse_args()
    try:
        size = os.path.getsize(args.dump)
        headers = find_headers(args.dump, size)
        with open(args.dump, 'rb') as f:
            f.seek(max(size - 4096, 0))
            tail = f.read()
    except OSError as e:
        print(f'Ошибка чтения: {e}')
        return 2

    restarts = [h for h in headers if h > 0]
    closed = CLOSE in tail
    report = {
        'file': args.dump,
        'size_bytes': size,
        'restarts_at': restarts,
        'closed_properly': closed,
        'usable_bytes': restarts[0] if restarts else size,
        'copies_identical': None,
        'ok': not restarts and closed,
    }
    if restarts:
        first = restarts[0]
        report['copies_identical'] = same_bytes(args.dump, 0, first, min(first, size - first, 256 * 1024 * 1024))

    print(f"Файл: {args.dump} ({size:,} байт)".replace(',', ' '))
    if report['ok']:
        print('Целый: заголовок один, файл закрыт тегом </V8Exch:_1CV8DtUD>.')
    else:
        if restarts:
            gib = ', '.join(f'{r:,} ({r / 2**30:.2f} ГиБ)'.replace(',', ' ') for r in restarts)
            print(f'ПОВРЕЖДЁН: файл начинается заново на смещениях {gib}.')
            if report['copies_identical']:
                print('Куски совпадают с началом файла: реальных данных только до первой отметки, остальное — повторы.')
            usable = f"{report['usable_bytes']:,}".replace(',', ' ')
            print(f'Пригодная часть: первые {usable} байт; всё, что шло в выгрузке дальше, потеряно.')
        if not closed:
            print('Файл не закрыт тегом </V8Exch:_1CV8DtUD>: выгрузка оборвана.')
        print('Нужна новая выгрузка из 1С (без регистров ФИАС АдресныеОбъекты/АдминистративнаяИерархия), сохранённая на NTFS-диск.')
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
