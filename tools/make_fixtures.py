#!/usr/bin/env python3
"""Генерирует синтетические входные файлы для тестов навыка (выдуманные люди, без персональных данных).

    python tools/make_fixtures.py

Пишет в fid-import-1c/evals/<case>/files/.
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fid-import-1c', 'evals')
Z = '00000000-0000-0000-0000-000000000000'
HEAD = ('﻿<?xml version="1.0" encoding="UTF-8"?>\r\n'
        '<V8Exch:_1CV8DtUD xmlns:V8Exch="http://www.1c.ru/V8/1CV8DtUD/" xmlns:core="http://v8.1c.ru/data" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/enterprise/current-config" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\r\n\t<V8Exch:Data>\r\n')
TAIL = '\t</V8Exch:Data>\r\n</V8Exch:_1CV8DtUD>\r\n'


def ref(n):
    return f'{n:08x}-1111-2222-3333-444455556666'


def obj(kind, fields):
    """Объект верхнего уровня в формате выгрузки: поля и табличные части (список словарей)."""
    out = [f'\t\t<v8:{kind}>\r\n']
    for k, v in fields.items():
        if isinstance(v, list):
            for row in v:
                out.append(f'\t\t\t<v8:{k}>\r\n')
                out += [f'\t\t\t\t<v8:{a}>{b}</v8:{a}>\r\n' for a, b in row.items()]
                out.append(f'\t\t\t</v8:{k}>\r\n')
        elif v is None:
            out.append(f'\t\t\t<v8:{k}/>\r\n')
        else:
            out.append(f'\t\t\t<v8:{k}>{v}</v8:{k}>\r\n')
    out.append(f'\t\t</v8:{kind}>\r\n')
    return ''.join(out)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


# ---------- 1. Стоматологическая клиника: всё есть, кроме регистров контактов, цен, паспортов и графиков
people = [('Котова', 'Мария', 'Игоревна', 'Женский', '1987-04-12'), ('Лебедев', 'Павел', 'Сергеевич', 'Мужской', '1979-11-03'),
          ('Орлова', 'Анна', 'Петровна', 'Женский', '2015-06-21'), ('Соколов', 'Иван', None, 'Мужской', '0001-01-01')]
stom = [obj('CatalogObject.Филиалы', {'Ref': ref(1), 'DeletionMark': 'false', 'Code': '00001', 'Description': 'Клиника «Улыбка-Тест»'})]
stom.append(obj('CatalogObject.Организации', {'Ref': ref(2), 'DeletionMark': 'false', 'Description': 'ООО «Тест-Дент»', 'ИНН': '0000000000'}))
for i, (fam, nam, otc, pol, dr) in enumerate(people):
    stom.append(obj('CatalogObject.Клиенты', {'IsFolder': 'false', 'Ref': ref(100 + i), 'DeletionMark': 'false', 'Parent': Z,
                                               'Description': f'{fam} {nam}', 'Фамилия': fam, 'Имя': nam, 'Отчество': otc,
                                               'Пол': pol, 'ДатаРождения': f'{dr}T00:00:00', 'ДатаСоздания': '2024-01-10T10:00:00'}))
    stom.append(obj('CatalogObject.МедицинскиеКарты', {'Ref': ref(200 + i), 'DeletionMark': 'false', 'Клиент': ref(100 + i), 'НомерКарты': str(i + 1)}))
stom.append(obj('CatalogObject.Должности', {'Ref': ref(300), 'DeletionMark': 'false', 'Description': 'Врач-стоматолог-терапевт'}))
stom.append(obj('CatalogObject.Сотрудники', {'IsFolder': 'false', 'Ref': ref(301), 'DeletionMark': 'false', 'Description': 'Белов Олег',
                                              'Фамилия': 'Белов', 'Имя': 'Олег', 'Должность': ref(300), 'УчаствуетВКалендарномПланировании': 'true'}))
stom.append(obj('CatalogObject.Номенклатура', {'IsFolder': 'false', 'Ref': ref(400), 'DeletionMark': 'false', 'Description': 'Осмотр',
                                                'НаименованиеПолное': 'Осмотр', 'ВидНоменклатуры': 'Услуга', 'Code': '001'}))
stom.append(obj('CatalogObject.ВидыСостоянийЗаявок', {'Ref': ref(500), 'DeletionMark': 'false', 'Description': 'Выполнена', 'PredefinedDataName': 'Выполнена'}))
for i in range(3):
    stom.append(obj('DocumentObject.Заявка', {'Ref': ref(600 + i), 'DeletionMark': 'false', 'Date': '2026-07-0{}T09:00:00'.format(i + 1),
                                               'Posted': 'false', 'Клиент': ref(100 + i), 'Сотрудник': ref(301),
                                               'ДатаНачала': f'2026-07-0{i + 2}T10:00:00', 'ДатаОкончания': f'2026-07-0{i + 2}T11:00:00',
                                               'Филиал': ref(1), 'Состояние': ref(500)}))
    stom.append(obj('DocumentObject.ОказаниеУслуг', {'Ref': ref(700 + i), 'DeletionMark': 'false', 'Date': f'2026-07-0{i + 2}T11:05:00',
                                                      'Posted': 'true', 'Клиент': ref(100 + i), 'Сотрудник': ref(301), 'Филиал': ref(1),
                                                      'СуммаДокумента': '1500', 'ВидОперации': 'ПродажаКлиенту',
                                                      'Работы': [{'Номенклатура': ref(400), 'Количество': '1', 'Цена': '1500', 'Сумма': '1500'}],
                                                      'Оплата': [{'ВидОплаты': 'Безналичные', 'Сумма': '1500'}]}))
write(os.path.join(ROOT, 'readiness-stom-missing-contacts', 'files', 'dump.xml'), HEAD + ''.join(stom) + TAIL)

# ---------- 2. Повреждённый файл: после части объектов файл начинается заново (как испорченная выгрузка > 4 ГБ)
first = HEAD + ''.join(stom[:6])
cut = first[:len(first) - 40]  # обрыв посреди объекта
write(os.path.join(ROOT, 'readiness-corrupted-dump', 'files', 'dump.xml'), cut + first + first[:len(first) // 2])

# ---------- 3. Итоговая сверка (УНФ): рабочая папка после get_from_1c + выгрузка тенанта с подложенными расхождениями
work = os.path.join(ROOT, 'final-report-discrepancy', 'files', 'work', 'V8Exch_Data')
dbd = os.path.join(ROOT, 'final-report-discrepancy', 'files', 'db')


def v8(rows):
    return [{f'v8_{k}': v for k, v in r.items()} for r in rows]


def jw(dir_, name, data):
    os.makedirs(dir_, exist_ok=True)
    with open(os.path.join(dir_, name + '.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


clients = [('Зайцева', 'Ольга', 'Николаевна', '1990-02-02', '79001112233'), ('Морозов', 'Глеб', 'Ильич', '1985-08-15', '79004445566'),
           ('Титова', 'Вера', None, '2001-12-30', '79007778899')]
kontr = [{'IsFolder': 'false', 'Ref': ref(100 + i), 'DeletionMark': 'false', 'Parent': Z, 'Description': f'{a} {b}', 'Фамилия': a, 'Имя': b,
          'Отчество': c, 'ТипКонтрагента': 'Клиенты', 'ДатаРожденияКлиента': f'{d}T00_00_00',
          'КонтактнаяИнформация': [{'v8_Тип': 'Телефон', 'v8_НомерТелефона': ph}]} for i, (a, b, c, d, ph) in enumerate(clients)]
kontr.append({'IsFolder': 'false', 'Ref': ref(199), 'DeletionMark': 'false', 'Parent': Z, 'Description': 'ООО Поставщик', 'ТипКонтрагента': 'Поставщик'})
jw(work, 'v8_CatalogObject.Контрагенты', {'v8_CatalogObject.Контрагенты': v8(kontr)})
jw(work, 'v8_CatalogObject.СтруктурныеЕдиницы', {'v8_CatalogObject.СтруктурныеЕдиницы': v8([{'IsFolder': 'false', 'Ref': ref(1), 'Description': 'Филиал Тест', 'ЧасовойПояс': 'Asia/Yekaterinburg'}])})
jw(work, 'v8_CatalogObject.Сотрудники', {'v8_CatalogObject.Сотрудники': v8([{'IsFolder': 'false', 'Ref': ref(301), 'DeletionMark': 'false', 'Description': 'Белов Олег', 'Фамилия': 'Белов', 'Имя': 'Олег'}])})
jw(work, 'v8_CatalogObject.Номенклатура', {'v8_CatalogObject.Номенклатура': v8([{'IsFolder': 'false', 'Ref': ref(400), 'DeletionMark': 'false', 'Description': 'Осмотр', 'НаименованиеПолное': 'Осмотр', 'ЭтоУслуга': 'true'}])})
events = [{'Ref': ref(600 + i), 'Posted': 'true', 'DeletionMark': 'false', 'Контрагент': ref(100 + i), 'Статус': 'ПриемОкончен',
           'Услуги': {'v8_Сотрудник': ref(301), 'v8_ДатаНачала': f'2026-05-1{i}T10_00_00', 'v8_ДатаОкончания': f'2026-05-1{i}T11_00_00'}} for i in range(3)]
jw(work, 'v8_DocumentObject.Событие', {'v8_DocumentObject.Событие': v8(events)})
priem = [{'Ref': ref(700 + i), 'Number': f'Т-00{i + 1}', 'Posted': 'true', 'DeletionMark': 'false', 'Date': f'2026-05-1{i}T11_00_00',
          'Контрагент': ref(100 + i), 'Сотрудник': ref(301), 'СуммаДокумента': '2000',
          'Лечение': {'v8_Номенклатура': ref(400), 'v8_Цена': '2000', 'v8_Количество': '1', 'v8_Сумма': '2000'}} for i in range(3)]
jw(work, 'v8_DocumentObject.Прием', {'v8_DocumentObject.Прием': v8(priem)})
pay = [{'Ref': ref(800 + i), 'Posted': 'true', 'Контрагент': ref(100 + i), 'ВидОперации': 'ОплатаОтКлиента', 'ТипДенежныхСредств': 'Наличные',
        'СуммаДокумента': '2000', 'РасшифровкаПлатежа': {'v8_Сумма': '2000', 'v8_ДокументОснования': ref(700 + i)}} for i in range(3)]
jw(work, 'v8_DocumentObject.ПоступлениеДенежныхСредств', {'v8_DocumentObject.ПоступлениеДенежныхСредств': v8(pay)})

# база: всё загружено, кроме того, что подложено специально
jw(dbd, 'branches', [{'id': 1, 'name': 'Филиал Тест', 'time_zone': 'Asia/Yekaterinburg', 'import_id': ref(1)}])
jw(dbd, 'patients', [{'id': 10 + i, 'import_id': ref(100 + i), 'user_id': 20 + i, 'created_at': None} for i in range(3)])
jw(dbd, 'users', [{'id': 20 + i, 'import_id': ref(100 + i), 'email': f'user{20 + i}@user.ru', 'created_at': None} for i in range(3)])
pu = [{'import_id': ref(100 + i), 'user_id': 20 + i, 'last_name': a, 'first_name': b, 'second_name': c, 'birthdate': d, 'phone': ph}
      for i, (a, b, c, d, ph) in enumerate(clients)]
pu.append({'import_id': ref(301), 'user_id': 30, 'last_name': 'Белов', 'first_name': 'Олег', 'second_name': None, 'birthdate': '1970-01-01', 'phone': ''})
jw(dbd, 'personal_users', pu)
jw(dbd, 'doctors', [{'id': 5, 'import_id': ref(301), 'user_id': 30, 'active': 1}])
jw(dbd, 'employees', [])
jw(dbd, 'services', [{'id': 1, 'import_id': ref(400), 'name': 'Осмотр', 'price': '2000'}])
# подложено: запись третьего пациента не загружена
jw(dbd, 'appointments', [{'patient_id': 10 + i, 'doctor_id': 5, 'time_begin': f'2026-05-1{i} 05:00:00', 'time_end': f'2026-05-1{i} 06:00:00',
                          'visit_id': 7, 'active': 1, 'chair_id': 1} for i in range(2)])
# подложено: сумма второго счёта 1 800 вместо 2 000
jw(dbd, 'invoice', [{'id': 1 + i, 'import_id': ref(700 + i), 'patient_id': 10 + i, 'price': '1800.00' if i == 1 else '2000.00'} for i in range(3)])
jw(dbd, 'money', [{'patient_id': 10 + i, 'money': '2000.00', 'money_operation_type': 'invoice_paid', 'payment_type': 1, 'invoice_id': 1 + i} for i in range(3)])
print('fixtures written to', os.path.abspath(ROOT))
