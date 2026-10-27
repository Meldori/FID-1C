"""Отчёты для пользователя: «Готовность выгрузки» (шаг 1) и «Итог» (шаг 3).

Отчёты собирает скрипт, а не модель: так они одинаковые в Claude, ChatGPT и без ИИ вовсе.
Модель дописывает только раздел «Комментарий» — объяснения, которых нет в цифрах.
"""
import datetime as dt

# Что пользователь потеряет в программе, если в выгрузке нет объекта
CONSEQUENCES = {
    'Филиал': 'филиал создастся без данных из 1С; часовой пояс задать вручную',
    'Медкарты': 'номера медкарт не перенесутся',
    'Сотрудники': 'врачей и сотрудников придётся завести вручную, записи и счета без врача не загрузятся',
    'Должности': 'должностей не будет; врачом считается тот, у кого есть записи или счета',
    'Номенклатура': 'услуг не будет — счета не загрузятся',
    'Цены': 'цен из прайса 1С не будет; цена услуги возьмётся как самая частая цена продаж',
    'Контакты (телефоны, email)': 'у пациентов не будет телефонов и email',
    'Паспорта': 'паспортов не будет (в Стоматологической клинике часть берётся из справок для налоговой)',
    'Графики врачей': 'расписание врачей построится по записям',
    'Оплаты': 'оплат не будет — все счета окажутся неоплаченными',
    'Пациенты': 'пациентов нет — переносить нечего',
    'Записи в календарь': 'календарь будет пустым',
    'Счета (оказание услуг)': 'счетов и истории лечения не будет',
}
# Расхождения сверки, которые нормальны и ничего не требуют
NORMAL = {'дубли в самой 1С (слиты в одну)': 'Дубли в самой 1С, в программе одна запись — это норма'}


def example(v):
    if isinstance(v, (list, tuple)):
        return ' / '.join('—' if x in (None, '') else str(x) for x in v)
    return str(v)


def fmt(n):
    return f'{n:,}'.replace(',', ' ')


def size(b):
    for unit, k in (('ГиБ', 2**30), ('МиБ', 2**20), ('КиБ', 2**10)):
        if b >= k:
            return f'{b / k:.2f} {unit}'
    return f'{b} байт'


def money(x):
    return f'{x:,.2f}'.replace(',', ' ')


def readiness(clinic, check, info, fid=None, cfg=None):
    """check — check_dump.check(), info — scan_1c.analyze(), fid — `fid_bridge info` или None."""
    cfg = cfg or {}
    level, reason = info['level'], info['reason']
    if not check['ok'] and level != 'НЕЛЬЗЯ':
        level, reason = 'НЕЛЬЗЯ', 'файл повреждён'
    lines = [f'## Готовность выгрузки {clinic}: {level}', '', f'Причина: {reason}.', '']

    if check['ok']:
        lines.append(f"Файл: целый, {size(check['size_bytes'])}.")
    else:
        parts = []
        if not check.get('is_1c_dump', True):
            parts.append('это не выгрузка «Выгрузка/загрузка данных XML»')
        if check['restarts_at']:
            first = check['restarts_at'][0]
            parts.append(f'начинается заново на отметке {size(first)} — дальше повторы, реальных данных только первые {size(first)}')
        if not check['closed_properly']:
            parts.append('не закрыт тегом </V8Exch:_1CV8DtUD> — выгрузка оборвана')
        lines.append('Файл: ПОВРЕЖДЁН — ' + '; '.join(parts) + '.')
    period = f"{info['period'][0]} — {info['period'][1]}" if info.get('period') else 'документов нет'
    lines += [f"Конфигурация: {info['config_label']}", f'Период данных: {period}', '',
              '| Что | Есть в 1С | Сколько | Помечено на удаление |', '| --- | --- | --- | --- |']
    for r in info['rows']:
        if r['count'] is None:
            lines.append(f"| {r['what']} | да, {r['object']} | — | |")
        elif r['count']:
            # в УНФ в Контрагентах вместе с пациентами лежат поставщики и лаборатории: точное число пациентов — после разбора
            note = ' (вместе с поставщиками)' if r['object'] == 'CatalogObject.Контрагенты' else ''
            lines.append(f"| {r['what']} | да, `{r['object']}`{note} | {fmt(r['count'])} | {fmt(r['deleted']) if r['deleted'] else ''} |")
        else:
            lines.append(f"| {r['what']} | **нет** | 0 | |")
    lines.append('')

    damaged = not check['ok']
    if damaged and info['missing']:
        # при обрыве объекты пропали не из-за 1С, а из-за файла: одна причина, а не десять последствий
        lost = [f"- всё, что шло в выгрузке после обрыва: в пригодной части нет {', '.join(info['missing'])}"]
    else:
        lost = [f"- {w}: {CONSEQUENCES.get(w, 'не будет в программе')}" for w in info['missing']]
    if info['config'] == 'stom' and not cfg.get('timezone'):
        lost.append('- Часовой пояс: в этой 1С его нет — задайте `timezone` в fid1c.json до загрузки (иначе записи сдвинутся)')
    if info.get('only_fias_registers'):
        lost.append('- Из регистров в файле только ФИАС: всё, что шло после него, вероятно, обрезано')
    lines += ['Чего не будет в программе:'] + (lost or ['- ничего, всё нужное есть']) + ['']

    todo = []
    if fid is None:
        todo.append('- не проверено: нет доступа к Future-IT-Dent (запустите `fid1c.py doctor`)')
    else:
        imp = fid.get('importer', {})
        if not imp.get('base'):
            todo.append('- в Future-IT-Dent нет импорта 1С (ImportFrom1CDBServices)')
        if info['config'] == 'stom' and not imp.get('requests'):
            todo.append('- импорт не умеет `Заявка` и `ОказаниеУслуг` (Стоматологическая клиника) — нужна доработка, см. references/config-stom.md')
        if not imp.get('dependents'):
            todo.append('- нет переноса детей-иждивенцев (`get_dependents_from_1c`)')
        if info['config'] == 'unknown':
            todo.append('- конфигурация не распознана: изучить по references/explore.md и доработать сборщики')
    if info.get('fias'):
        todo.append(f"- в файле {fmt(info['fias'])} записей ФИАС: не мешают, но замедляют разбор; в следующий раз исключить")
    lines += ['Что нужно доработать в импорте:'] + (todo or ['- ничего: импорт поддерживает эту конфигурацию']) + ['']

    tenant = cfg.get('test_tenant') or '<тестовый тенант>'
    if level == 'НЕЛЬЗЯ':
        offer = 'Предлагаю: сначала новая выгрузка из 1С — без регистров ФИАС, на NTFS-диск, архивом 7-Zip (подробно в references/pitfalls.md).'
        if damaged and not info['critical']:
            # в пригодной части есть пациенты, записи и счета — частичная загрузка хоть что-то даёт
            offer += (' Если клиника согласна на неполные данные — можно загрузить пригодную часть в тестовый тенант, '
                      'но только по вашему решению.')
        lines.append(offer)
    else:
        lines.append(f'Предлагаю: загрузить в тестовый тенант `{tenant}` (`fid1c.py load --tenant {tenant}`), '
                     'сверить с 1С и показать итог. В рабочую базу — только после этого и по вашему «да».')
    lines += ['', '### Комментарий', '', '_(особенности этой клиники — дописывает ассистент)_', '']
    return '\n'.join(lines)


def final(target, rec, state=None, ready=None, chairs=None):
    """rec — reconcile.as_json(); state — шаги загрузки из fid1c_state.json; ready — readiness.json."""
    m, n = rec['score']
    pct = int(1000 * m / n) / 10 if n else 100.0
    problems = [(e['name'], kind, v) for e in rec['entities'] for kind, v in e['problems'].items()]
    unexplained = [p for p in problems if p[1] not in NORMAL]
    failed_steps = {k: s for k, s in (state or {}).items() if s.get('failed') or s.get('status') not in ('done', 'skipped')}
    clean = m == n and not failed_steps
    if clean:
        phrase = 'Всё перенесено и совпадает с 1С построчно.'
    elif m == n:
        phrase = 'Сверка сошлась, но на загрузке были ошибки — посмотреть шаги ниже.'
    else:
        phrase = f'Перенесено не всё: не совпало {fmt(n - m)} из {fmt(n)}.'
    lines = [f'## Итог: {fmt(m)}/{fmt(n)} ({pct:.1f}%)', '', f'Куда загружено: {target}', '', phrase, '',
             '| Сущность | В 1С | Загружено | Совпало |', '| --- | --- | --- | --- |']
    for e in rec['entities']:
        mark = '' if e['matched'] == e['expected'] else ' ⚠'
        lines.append(f"| {e['name']} | {fmt(e['expected'])} | {fmt(e['loaded'])} | {fmt(e['matched'])}/{fmt(e['expected'])}{mark} |")
    t = rec['totals']
    lines += ['', f"Деньги: счета {money(t['invoices_sum_1c'])} ₽ в 1С / {money(t['invoices_sum_db'])} ₽ в базе; "
                  f"оплаты {money(t['payments_sum_1c'])} ₽ / {money(t['payments_sum_db'])} ₽.",
              f"Часовой пояс сверки: {t.get('tz', '?')}.", '']

    lines.append('### Расхождения')
    if not problems:
        lines.append('- нет')
    for name, kind, v in problems:
        count = v[0] if len(v) == 1 and isinstance(v[0], int) else len(v)
        examples = '' if len(v) == 1 and isinstance(v[0], int) else ' — например: ' + '; '.join(example(x) for x in v[:3])
        why = NORMAL.get(kind, 'Причина не установлена — разобрать')
        lines.append(f'- **{name}, {kind}:** {count}{examples}. {why}.')
    lines.append('')

    if state:
        lines += ['### Загрузка по шагам', '', '| Шаг | Строк | Загружено | Ошибок |', '| --- | --- | --- | --- |']
        for step, s in state.items():
            if s.get('status') == 'skipped':
                lines.append(f'| {step} | — | файла нет | |')
            elif s.get('status') != 'done':
                lines.append(f"| {step} | — | **{s.get('status', '?')}** | |")
            else:
                lines.append(f"| {step} | {fmt(s.get('rows', 0))} | {fmt(s.get('loaded', 0))} | {fmt(s['failed']) if s.get('failed') else ''} |")
        lines.append('')
        errs = [(k, e) for k, s in state.items() for e in s.get('errors', [])[:3]]
        if errs:
            lines += ['Ошибки загрузки (полностью — в WORK/errors_<шаг>.log):'] + [f'- {k}: {e}' for k, e in errs] + ['']
    if chairs:
        over = sum(c['overlaps'] for c in chairs.get('by_chair', {}).values())
        lines += [f"Кресла: {chairs.get('chairs')} шт., записей переложено {fmt(chairs.get('moved', 0))}, "
                  f"пересечений {over} (остаются только когда записей одновременно больше, чем кресел).", '']

    lines.append('### Чего нет из-за самой 1С')
    if ready is None:
        lines.append('- не проверялось (нет reports/readiness.json — запустите fid1c.py readiness)')
    else:
        lines += [f"- {w}: {CONSEQUENCES.get(w, 'нет в выгрузке')}" for w in ready.get('missing') or []] or ['- ничего']
    lines += ['', '### Не проверено', '- интерфейс программы в браузере (открыть карточку пациента, календарь, счёт)', '']
    if clean:
        verdict = 'да — можно переносить в рабочую базу (после дампа и по явному согласию)'
    elif unexplained or failed_steps:
        verdict = 'нет — есть необъяснённые расхождения или ошибки загрузки'
    else:
        verdict = 'да, с оговорками — все расхождения объяснены выше'
    lines += [f'Готово к переносу: {verdict}.', '', '### Комментарий', '',
              '_(объяснение каждого расхождения и что делать — дописывает ассистент)_', '',
              f"_Сверка: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}._", '']
    return '\n'.join(lines)
