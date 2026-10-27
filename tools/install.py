#!/usr/bin/env python3
"""Установка навыка fid-import-1c для Claude и/или ChatGPT (Codex, десктоп-приложение).

    python tools/install.py --claude          ~/.claude/skills/fid-import-1c
    python tools/install.py --agents          ~/.agents/skills/fid-import-1c  (ChatGPT/Codex)
    python tools/install.py --all             оба
    python tools/install.py --all --link      ссылкой на папку репозитория: правки видны сразу (на Windows — junction)
    python tools/install.py --all --uninstall

Существующая установка заменяется только если это прежняя копия навыка (в ней есть SKILL.md с name: fid-import-1c).
"""
import argparse
import os
import shutil
import subprocess
import sys

NAME = 'fid-import-1c'
SRC = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', NAME))
# Что не нужно ассистенту при работе: тесты навыка и кэш Python
SKIP = shutil.ignore_patterns('evals', '__pycache__', '*.pyc')


def targets(a, home):
    out = []
    if a.claude or a.all:
        out.append(('Claude', os.path.join(home, '.claude', 'skills', NAME)))
    if a.agents or a.all:
        out.append(('ChatGPT/Codex', os.path.join(home, '.agents', 'skills', NAME)))
    return out


def is_ours(path):
    skill = os.path.join(path, 'SKILL.md')
    if not os.path.exists(skill):
        return False
    with open(skill, encoding='utf-8') as f:
        return f'name: {NAME}' in f.read(400)


def remove(path):
    """Ссылку или junction убрать, не трогая папку репозитория; копию — удалить целиком."""
    if os.path.islink(path):
        os.unlink(path)
    elif getattr(os.path, 'isjunction', lambda p: False)(path) or os.path.realpath(path) == os.path.realpath(SRC):
        os.rmdir(path)  # junction: удаляется сама ссылка, папка репозитория остаётся
    else:
        shutil.rmtree(path)


def link(src, dst):
    try:
        os.symlink(src, dst, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            raise
        # без прав разработчика Windows не даёт symlink — junction работает всегда
        subprocess.run(['cmd', '/c', 'mklink', '/J', dst, src], check=True, capture_output=True)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='Установка навыка fid-import-1c')
    ap.add_argument('--claude', action='store_true')
    ap.add_argument('--agents', action='store_true', help='ChatGPT (десктоп) и Codex')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--link', action='store_true')
    ap.add_argument('--uninstall', action='store_true')
    ap.add_argument('--home', default=os.path.expanduser('~'), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    todo = targets(a, a.home)
    if not todo:
        ap.error('укажите --claude, --agents или --all')
    for label, dst in todo:
        exists = os.path.lexists(dst)
        if exists and not is_ours(dst):
            print(f'✘ {label}: {dst} занят чем-то другим — не трогаю')
            continue
        if exists:
            remove(dst)
        if a.uninstall:
            print(f"✔ {label}: {'удалён' if exists else 'не был установлен'} ({dst})")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if a.link:
            link(SRC, dst)
        else:
            shutil.copytree(SRC, dst, ignore=SKIP)
        print(f"✔ {label}: {'ссылка' if a.link else 'копия'} → {dst}")
    if not a.uninstall:
        print('Перезапустите ассистента, чтобы он увидел навык. Проверка окружения: '
              f'python "{os.path.join(SRC, "scripts", "fid1c.py")}" doctor')
    return 0


if __name__ == '__main__':
    sys.exit(main())
