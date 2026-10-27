#!/usr/bin/env python3
"""Сборка навыка в архивы для загрузки через интерфейс.

    python tools/build.py

dist/fid-import-1c.zip    — Claude.ai (Настройки → Навыки) и ChatGPT (Skills → загрузить)
dist/fid-import-1c.skill  — тот же архив с расширением, которое Claude Code и Claude.ai открывают кнопкой «Save skill»

В архиве папка fid-import-1c/ без тестов (evals) и кэша Python. Перед сборкой проверяется SKILL.md.
"""
import os
import re
import sys
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
NAME = 'fid-import-1c'
SKIP_DIRS = {'evals', '__pycache__'}


def frontmatter(path):
    """Простой разбор frontmatter без PyYAML: name и description (в кавычках или без)."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    m = re.match(r'^---\n(.*?)\n---\n', text, re.S)
    if not m:
        raise ValueError('SKILL.md: нет frontmatter между строками ---')
    fields = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(':')
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] == '"':
            v = v[1:-1]
        fields[k.strip()] = v
    return fields, text[m.end():]


def validate(skill_dir):
    """Ограничения формата Agent Skills, общие для Claude и ChatGPT/Codex."""
    fm, body = frontmatter(os.path.join(skill_dir, 'SKILL.md'))
    errors = []
    name, desc = fm.get('name', ''), fm.get('description', '')
    if name != os.path.basename(skill_dir):
        errors.append(f'name «{name}» должен совпадать с именем папки')
    if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', name) or len(name) > 64:
        errors.append('name: латиница в нижнем регистре, цифры и дефисы, до 64 символов')
    if not desc or len(desc) > 1024:
        errors.append(f'description: от 1 до 1024 символов (сейчас {len(desc)})')
    if '<' in desc or '>' in desc:
        errors.append('description: без угловых скобок')
    if len(body.splitlines()) > 500:
        errors.append('SKILL.md длиннее 500 строк — вынести подробности в references/')
    for ref in set(re.findall(r'`((?:references|scripts|agents)/[^`*<> ]+)`', body)):
        if not os.path.exists(os.path.join(skill_dir, ref)):
            errors.append(f'SKILL.md ссылается на несуществующий {ref}')
    return errors


def build(out_dir=None):
    skill_dir = os.path.join(ROOT, NAME)
    errors = validate(skill_dir)
    if errors:
        raise SystemExit('Навык не собран:\n- ' + '\n- '.join(errors))
    out_dir = out_dir or os.path.join(ROOT, 'dist')
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for ext in ('zip', 'skill'):
        path = os.path.join(out_dir, f'{NAME}.{ext}')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            for base, dirs, files in os.walk(skill_dir):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for fn in sorted(files):
                    if fn.endswith('.pyc'):
                        continue
                    full = os.path.join(base, fn)
                    z.write(full, os.path.join(NAME, os.path.relpath(full, skill_dir)).replace(os.sep, '/'))
        paths.append(path)
    return paths


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    for p in build():
        print('собран', p)
