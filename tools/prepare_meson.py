#!/usr/bin/env python3
"""Apply the reviewed mochiOS linker policy only to the exact pinned Meson source."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    """Hash a host-tool source file without executing it."""
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def prepare():
    """Idempotently port the pinned host Meson; reject different versions or modified source."""
    import mesonbuild
    from mesonbuild.coredata import version
    lock = json.loads((ROOT/'tooling/meson-port.json').read_text())
    if version != lock['version']: raise ValueError('The mochiOS Meson patch requires the pinned version')
    site = Path(mesonbuild.__file__).resolve().parent.parent
    state = {name:digest(site/name) for name in lock['files']}
    if all(state[name] == item['after'] for name, item in lock['files'].items()):
        return
    if any(state[name] != item['before'] for name, item in lock['files'].items()):
        raise ValueError('Refusing to patch modified host Meson sources')
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(ROOT/'tooling/meson-mochios.patch')], cwd=site, check=True)
    if any(digest(site/name) != item['after'] for name, item in lock['files'].items()):
        raise ValueError('Meson patch post-image verification failed')
    importlib.invalidate_caches()


if __name__ == '__main__':
    prepare()
