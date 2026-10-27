"""Автотесты навыка fid-import-1c: скрипты, отчёты, правила безопасности, сборка и установка.

    python -m unittest discover -s tests -v

Нужен только Python 3.8+. Тесты с PHP пропускаются, если PHP не найден.
Входные данные — синтетические выгрузки из fid-import-1c/evals/*/files (tools/make_fixtures.py).
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
SKILL = os.path.join(ROOT, 'fid-import-1c')
SCRIPTS = os.path.join(SKILL, 'scripts')
EVALS = os.path.join(SKILL, 'evals')
sys.path[:0] = [SCRIPTS, os.path.join(ROOT, 'tools')]

import build  # noqa: E402
import check_dump  # noqa: E402
import fid1c  # noqa: E402
import install  # noqa: E402
import reconcile  # noqa: E402
import reports  # noqa: E402
import scan_1c  # noqa: E402

STOM = os.path.join(EVALS, 'readiness-stom-missing-contacts', 'files', 'dump.xml')
BROKEN = os.path.join(EVALS, 'readiness-corrupted-dump', 'files', 'dump.xml')
FINAL = os.path.join(EVALS, 'final-report-discrepancy', 'files')


def read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as f:
        return f.read()


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


def run_cli(*argv):
    """fid1c.main в этом же процессе: (код выхода, вывод)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fid1c.main(list(argv))
    return code, out.getvalue()


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fid1c-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class CheckDumpTest(Temp):
    def test_whole_file_is_ok(self):
        r = check_dump.check(STOM)
        self.assertTrue(r['ok'])
        self.assertTrue(r['is_1c_dump'])
        self.assertEqual(r['restarts_at'], [])

    def test_restart_and_unclosed_file_is_damaged(self):
        r = check_dump.check(BROKEN)
        self.assertFalse(r['ok'])
        self.assertTrue(r['restarts_at'], 'повтор заголовка не найден')
        self.assertFalse(r['closed_properly'])
        self.assertEqual(r['usable_bytes'], r['restarts_at'][0])
        self.assertIn('ПОВРЕЖДЁН', '\n'.join(check_dump.describe(r)))

    def test_header_on_chunk_border(self):
        # заголовок попадает на границу блока чтения — должен найтись ровно один раз
        path = os.path.join(self.tmp, 'border.xml')
        old = check_dump.CHUNK
        check_dump.CHUNK = 16
        try:
            with open(path, 'wb') as f:
                f.write(b'<?xml v?><V8Exch:_1CV8DtUD>' + b'x' * 10 + b'<?xml v?>' + b'</V8Exch:_1CV8DtUD>')
            r = check_dump.check(path)
        finally:
            check_dump.CHUNK = old
        self.assertEqual(r["restarts_at"], [37])

    def test_not_a_1c_file(self):
        path = os.path.join(self.tmp, 'other.xml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0"?><root/>')
        r = check_dump.check(path)
        self.assertFalse(r['ok'])
        self.assertFalse(r['is_1c_dump'])


class ScanTest(unittest.TestCase):
    def test_stom_configuration_and_counts(self):
        res = scan_1c.scan(STOM)
        info = scan_1c.analyze(res)
        self.assertEqual(info['config'], 'stom')
        self.assertEqual(res['counts']['CatalogObject.Клиенты'], 4)
        self.assertEqual(res['counts']['DocumentObject.Заявка'], 3)
        self.assertEqual(res['counts']['DocumentObject.ОказаниеУслуг'], 3)
        self.assertEqual(res['posted']['DocumentObject.ОказаниеУслуг'], 3)
        self.assertEqual(info['level'], 'МОЖНО С ОГОВОРКАМИ')
        for what in ('Цены', 'Контакты (телефоны, email)', 'Паспорта', 'Графики врачей'):
            self.assertIn(what, info['missing'])
        self.assertEqual(info['period'], ('2026-07-01', '2026-07-04'))

    def test_damaged_file_stops_at_restart(self):
        res = scan_1c.scan(BROKEN)
        self.assertTrue(res['restarted'])
        self.assertEqual(scan_1c.analyze(res)['level'], 'НЕЛЬЗЯ')
        copies = scan_1c.scan(BROKEN, stop_at_restart=False)
        self.assertGreater(sum(copies['counts'].values()), sum(res['counts'].values()))

    def test_unf_contacts_live_in_card(self):
        counts = {'CatalogObject.Контрагенты': 5, 'CatalogObject.СтруктурныеЕдиницы': 1, 'DocumentObject.Событие': 1,
                  'DocumentObject.Прием': 1, 'DocumentObject.ПоступлениеДенежныхСредств': 1}
        res = {'counts': counts, 'deleted': {}, 'dates': {}, 'order': list(counts), 'restarted': False}
        info = scan_1c.analyze(res)
        self.assertEqual(info['config'], 'unf')
        self.assertNotIn('Контакты (телефоны, email)', info['missing'])
        self.assertNotIn('НЕЛЬЗЯ', info['level'])

    def test_missing_patients_is_critical(self):
        res = {'counts': {'DocumentObject.Заявка': 1}, 'deleted': {}, 'dates': {}, 'order': [], 'restarted': False}
        info = scan_1c.analyze(res)
        self.assertEqual(info['level'], 'НЕЛЬЗЯ')
        self.assertIn('Пациенты', info['critical'])


class ReconcileTest(Temp):
    def test_planted_discrepancies_are_found(self):
        _, data = reconcile.run(os.path.join(FINAL, 'work'), os.path.join(FINAL, 'db'), os.path.join(self.tmp, 'r.md'))
        self.assertEqual(data['score'], [12, 14])
        ents = {e['name']: e for e in data['entities']}
        self.assertEqual((ents['Записи в календаре']['matched'], ents['Записи в календаре']['expected']), (2, 3))
        self.assertEqual((ents['Счета']['matched'], ents['Счета']['expected']), (2, 3))
        self.assertIn('сумма', ents['Счета']['problems'])
        self.assertEqual(ents['Пациенты']['matched'], 3, 'поставщик не должен считаться пациентом')
        self.assertEqual(data['totals']['invoices_sum_1c'], 6000)
        self.assertEqual(data['totals']['invoices_sum_db'], 5800)

    def test_fixed_offset_and_named_zone_agree_for_today(self):
        import datetime as dt
        fixed, _ = reconcile.local_clock(None, 5)
        named, _ = reconcile.local_clock('Asia/Yekaterinburg', None)
        t = dt.datetime(2026, 5, 10, 5, 0)
        self.assertEqual(fixed(t), dt.datetime(2026, 5, 10, 10, 0))
        self.assertEqual(named(t), dt.datetime(2026, 5, 10, 10, 0))

    def test_missing_work_folder_is_clear_error(self):
        with self.assertRaises(SystemExit) as e:
            reconcile.reconcile(self.tmp, self.tmp)
        self.assertIn('V8Exch_Data', str(e.exception))


class ReportsTest(unittest.TestCase):
    def test_readiness_template(self):
        info = scan_1c.analyze(scan_1c.scan(STOM))
        text = reports.readiness('Тест', check_dump.check(STOM), info, None, {'test_tenant': 'fid1ctest'})
        self.assertTrue(text.startswith('## Готовность выгрузки Тест: МОЖНО С ОГОВОРКАМИ'))
        self.assertIn('Часовой пояс', text)
        self.assertIn('fid1ctest', text)
        self.assertIn('не проверено', text)

    def test_damaged_file_forces_no(self):
        info = scan_1c.analyze(scan_1c.scan(BROKEN))
        text = reports.readiness('Тест', check_dump.check(BROKEN), info)
        self.assertIn(': НЕЛЬЗЯ', text.splitlines()[0])
        self.assertIn('новая выгрузка', text)

    def test_damaged_without_documents_does_not_offer_partial_load(self):
        info = scan_1c.analyze(scan_1c.scan(BROKEN))
        text = reports.readiness('Тест', check_dump.check(BROKEN), info)
        self.assertNotIn('загрузить пригодную часть', text, 'в пригодной части нет записей и счетов — грузить нечего')
        self.assertIn('после обрыва', text)

    def test_final_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, data = reconcile.run(os.path.join(FINAL, 'work'), os.path.join(FINAL, 'db'), os.path.join(tmp, 'r.md'))
        text = reports.final('тенант t', data, {'users': {'status': 'done', 'rows': 3, 'loaded': 3, 'failed': 0}}, {'missing': []})
        self.assertIn('## Итог: 12/14 (85.7%)', text)
        self.assertIn('Готово к переносу: нет', text)
        self.assertIn('Т-002', text)

    def test_percent_rounds_down(self):
        data = {'score': [2586, 2587], 'totals': {'invoices_sum_1c': 0, 'invoices_sum_db': 0, 'payments_sum_1c': 0,
                                                  'payments_sum_db': 0}, 'entities': []}
        self.assertIn('(99.9%)', reports.final('x', data))


class CliTest(Temp):
    def clinic(self, dump=STOM, **extra):
        d = os.path.join(self.tmp, 'clinic')
        code, _ = run_cli('init', dump, '--clinic', 'Тест', '--dir', d, '--test-tenant', 'fid1ctest')
        self.assertEqual(code, 0)
        if extra:
            c = fid1c.Clinic(d)
            c.cfg.update(extra)
            c.save()
        return d

    def test_readiness_without_fid(self):
        d = self.clinic()
        code, out = run_cli('readiness', '-C', d, '--no-fid')
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(d, 'reports', 'readiness.md')))
        self.assertEqual(json.loads(read(d, 'reports', 'readiness.json'))['level'], 'МОЖНО С ОГОВОРКАМИ')
        code, out = run_cli('-C', d, 'status')
        self.assertIn('МОЖНО С ОГОВОРКАМИ', out)

    def test_readiness_damaged_exit_1(self):
        d = self.clinic(BROKEN)
        code, out = run_cli('readiness', '-C', d, '--no-fid')
        self.assertEqual(code, 1)
        self.assertIn('НЕЛЬЗЯ', out)

    def test_init_extracts_zip(self):
        z = os.path.join(self.tmp, 'Выгрузка.zip')
        with zipfile.ZipFile(z, 'w') as f:
            f.write(STOM, 'Выгрузка/dump.xml')
            f.writestr('readme.txt', 'не xml')
        d = self.clinic(z)
        c = fid1c.Clinic(d)
        self.assertTrue(os.path.exists(c.dump))
        self.assertTrue(c.dump.startswith(d))
        self.assertFalse(os.path.isabs(c.cfg['dump']), 'внутри папки клиники путь должен быть относительным')

    def test_init_sets_safe_test_tenant(self):
        d = os.path.join(self.tmp, 'c')
        run_cli('init', STOM, '--dir', d)
        c = fid1c.Clinic(d)
        self.assertEqual(c.cfg['test_tenant'], 'fid1ctest')
        self.assertNotIn(c.cfg['test_tenant'], c.cfg['protected_tenants'])

    def test_zip_slip_refused(self):
        z = os.path.join(self.tmp, 'evil.zip')
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('../../evil.xml', '<x/>')
        code, out = run_cli('init', z, '--dir', os.path.join(self.tmp, 'c'))
        self.assertEqual(code, 4)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'evil.xml')))

    def test_protected_tenant_refused(self):
        d = self.clinic()
        for t in ('lip', '1'):
            code, out = run_cli('load', '-C', d, '--tenant', t)
            self.assertEqual(code, 4, out)
            self.assertIn('рабочий', out)

    def test_production_needs_yes(self):
        d = self.clinic()
        code, out = run_cli('load', '-C', d, '--tenant', 'lip', '--production')
        self.assertEqual(code, 4)
        self.assertIn('--yes', out)

    def test_unknown_step(self):
        d = self.clinic()
        code, out = run_cli('load', '-C', d, '--steps', 'users,nonsense')
        self.assertEqual(code, 2)

    def test_damaged_dump_is_not_parsed(self):
        d = self.clinic(BROKEN)
        c = fid1c.Clinic(d)
        with self.assertRaises(fid1c.Stop) as e:
            fid1c.ensure_parsed(c, br=None)
        self.assertEqual(e.exception.code, 4)

    def test_verify_from_export(self):
        code, out = run_cli('verify', '-C', self.tmp, '--work', os.path.join(FINAL, 'work'), '--export', os.path.join(FINAL, 'db'))
        self.assertEqual(code, 1)
        self.assertIn('12/14', out)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'reports', 'final_export.md')))

    def test_requires_config(self):
        code, out = run_cli('readiness', '-C', self.tmp)
        self.assertEqual(code, 2)
        self.assertIn('init', out)


class SafetyTest(Temp):
    def test_backup_checks(self):
        c = fid1c.Clinic(self.tmp)
        with self.assertRaises(fid1c.Stop):
            fid1c.check_backup(c, 'lip', None)
        bad = os.path.join(self.tmp, 'bad.sql')
        with open(bad, 'w') as f:
            f.write('-- обрыв')
        with self.assertRaises(fid1c.Stop) as e:
            fid1c.check_backup(c, 'lip', bad)
        self.assertIn('не завершён', str(e.exception))
        good = os.path.join(self.tmp, 'good.sql')
        with open(good, 'w') as f:
            f.write('...\n-- Dump completed on 2026-10-27\n')
        fid1c.check_backup(c, 'lip', good)
        old = time.time() - 5 * 3600
        os.utime(good, (old, old))
        with self.assertRaises(fid1c.Stop) as e:
            fid1c.check_backup(c, 'lip', good)
        self.assertIn('старше', str(e.exception))

    def test_lock_blocks_second_run_and_clears_stale(self):
        name = f'unittest-{os.getpid()}'
        with fid1c.Lock(name):
            with self.assertRaises(fid1c.Stop) as e:
                with fid1c.Lock(name):
                    pass
            self.assertEqual(e.exception.code, 5)
        lock = fid1c.Lock(name)
        with open(lock.file, 'w') as f:
            json.dump({'pid': 999999999, 'since': 'давно'}, f)  # процесса нет — замок брошенный
        with fid1c.Lock(name):
            pass
        self.assertFalse(os.path.exists(lock.file))


class PackagingTest(Temp):
    def test_skill_md_is_valid_for_both_platforms(self):
        self.assertEqual(build.validate(SKILL), [])
        fm, _ = build.frontmatter(os.path.join(SKILL, 'SKILL.md'))
        self.assertLessEqual(len(fm['description']), 1024)

    def test_openai_yaml(self):
        text = read(SKILL, 'agents', 'openai.yaml')
        for key in ('interface:', 'display_name:', 'short_description:', 'policy:', 'allow_implicit_invocation:'):
            self.assertIn(key, text)

    def test_skill_does_not_name_one_assistant_tools(self):
        body = read(SKILL, 'SKILL.md')
        for word in ('Bash tool', 'TodoWrite', 'Read tool', 'Claude Code', 'tinker'):
            self.assertNotIn(word, body)

    def test_archives(self):
        for path in build.build(self.tmp):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            self.assertIn('fid-import-1c/SKILL.md', names)
            self.assertIn('fid-import-1c/agents/openai.yaml', names)
            self.assertIn('fid-import-1c/scripts/fid1c.py', names)
            self.assertFalse([n for n in names if '/evals/' in n or n.endswith('.pyc')])

    def test_install_and_uninstall(self):
        code = quiet(install.main, ['--all', '--home', self.tmp])
        self.assertEqual(code, 0)
        for sub in ('.claude', '.agents'):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, sub, 'skills', 'fid-import-1c', 'SKILL.md')))
            self.assertFalse(os.path.exists(os.path.join(self.tmp, sub, 'skills', 'fid-import-1c', 'evals')))
        quiet(install.main, ['--all', '--home', self.tmp])  # повторная установка заменяет копию
        quiet(install.main, ['--all', '--uninstall', '--home', self.tmp])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, '.claude', 'skills', 'fid-import-1c')))

    def test_install_link_keeps_repository(self):
        quiet(install.main, ['--claude', '--link', '--home', self.tmp])
        dst = os.path.join(self.tmp, '.claude', 'skills', 'fid-import-1c')
        self.assertTrue(os.path.exists(os.path.join(dst, 'SKILL.md')))
        quiet(install.main, ['--claude', '--uninstall', '--home', self.tmp])
        self.assertFalse(os.path.lexists(dst))
        self.assertTrue(os.path.exists(os.path.join(SKILL, 'SKILL.md')), 'удаление ссылки не должно трогать репозиторий')

    def test_install_does_not_touch_foreign_folder(self):
        dst = os.path.join(self.tmp, '.claude', 'skills', 'fid-import-1c')
        os.makedirs(dst)
        open(os.path.join(dst, 'mine.txt'), 'w').close()
        quiet(install.main, ['--claude', '--home', self.tmp])
        self.assertTrue(os.path.exists(os.path.join(dst, 'mine.txt')))


@unittest.skipUnless(shutil.which('php'), 'PHP не найден')
class PhpTest(unittest.TestCase):
    def test_bridge_syntax(self):
        r = subprocess.run(['php', '-l', os.path.join(SCRIPTS, 'fid_bridge.php')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
