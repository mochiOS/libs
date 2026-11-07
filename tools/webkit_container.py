#!/usr/bin/env python3
"""Offline WebKit build within the pinned, credential-free libs container."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import gzip
import tarfile
import sys
import container_build
import webkit


def apply_port_patches(engine, source, names, env, logs):
    """Apply reviewed native patches once, refusing changed inputs in a reused tree."""
    if not isinstance(names,list) or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names):
        raise ValueError('Invalid native patch list')
    patches={}
    for name in names:
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.suffix!='.patch':
            raise ValueError('Invalid native patch path')
        path=(engine/relative).resolve()
        if not path.is_relative_to(engine.resolve()) or not path.is_file():
            raise ValueError('Native patch escaped engine inputs')
        patches[name]=webkit.digest(path)
    state=logs/'native-patches.json'
    if state.exists():
        if json.loads(state.read_text())!=patches: raise ValueError('Native patches changed; choose a fresh build directory')
        return
    for index,name in enumerate(names):
        webkit.execute(['patch','--batch','--fuzz=0','-p1','-d',source,'-i',engine/name],env,logs/f'patch-{index}.log')
    webkit.write_json(state,patches)


def main():
    root=Path('/pipeline');work=Path('/work');engine=root/'engines/webkit'
    plan=json.loads((work/'plan.json').read_text())
    if not plan['ready']: raise ValueError('Blocked WebKit plan')
    for name,digest in plan['inputs'].items():
        if webkit.digest(root/name)!=digest: raise ValueError('Engine input changed: '+name)
    sdk=Path('/sdk');metadata=json.loads((sdk/'sdk.json').read_text())
    toolchain=sdk/metadata['cmake_toolchain']
    common=['--target','mochios','--toolchain',str(toolchain),'--deps-prefix','/deps',
            '--cache','/source','--source-root','/work/source','--build-root','/work/engine','--jobs','2']
    # doctor and source extraction enforce resource bounds and actual target links.
    subprocess.run([sys.executable,root/'tools/webkit.py','doctor',*common],check=True)
    subprocess.run([sys.executable,root/'tools/webkit.py','prepare',*common],check=True)
    port=json.loads((engine/'port.json').read_text())
    apply_port_patches(engine,work/'source'/plan['source']['directory'],port['patches'],
                       webkit.environment('mochios',Path('/deps')),work/'patch-logs')
    for step in ['configure','install']:
        subprocess.run([sys.executable,root/'tools/webkit.py',step,*common],check=True)
    receipt=json.loads((work/'engine/mochios/installed.json').read_text())
    installed=Path(receipt['destination'])
    port=json.loads((engine/'port.json').read_text())
    backend=[engine/name for name in port['backend_sources']]
    env=webkit.environment('mochios',Path('/deps'))
    env.update(PKG_CONFIG_SYSROOT_DIR=str(installed),
               PKG_CONFIG_LIBDIR=str(installed/'usr/lib/pkgconfig')+':/deps/usr/lib/pkgconfig')
    build=work/'adapter'
    webkit.execute(['cmake','-S',engine,'-B',build,'-G','Ninja',
        '-DCMAKE_TOOLCHAIN_FILE='+str(toolchain),'-DCMAKE_FIND_ROOT_PATH='+str(installed)+';/deps;'+str(sdk/metadata['sysroot']),
        '-DMWK_BUILD_ENGINE=ON','-DBUILD_TESTING=OFF','-DCMAKE_INSTALL_PREFIX=/usr',
        '-DCMAKE_SKIP_RPATH=ON','-DMWK_BACKEND_SOURCES='+';'.join(map(str,backend))],env,work/'adapter-configure.log')
    webkit.execute(['cmake','--build',build,'--parallel','2'],env,work/'adapter-build.log')
    stage=work/'stage';stage.mkdir(exist_ok=True)
    webkit.execute(['cmake','--install',build],dict(env,DESTDIR=str(stage)),work/'adapter-install.log')
    library=stage/'usr/lib/libwebkit.so.1'
    dynamic=container_build.validate_elf(library,shared=True)
    if 'Library soname: [libwebkit.so.1]' not in dynamic: raise ValueError('Wrong engine SONAME')
    symbols=subprocess.check_output(['nm','-D','--defined-only',library],text=True)
    exports=[line.split()[-1].split('@')[0] for line in symbols.splitlines() if len(line.split())>=3]
    if set(exports)-{'mwk_get_api','MOCHIOS_WEBKIT_1'} or 'mwk_get_api' not in exports:
        raise ValueError('Unexpected public C ABI exports')
    # All helper processes and resources remain in the same candidate tree.
    # This is a development install, never a signed/installed OS package.
    combined=container_build.merge_prefixes([installed,stage],work/'combined')
    shutil.rmtree(stage)
    combined.rename(stage)
    source=work/'source'/plan['source']['directory']
    notices=stage/'usr/share/licenses/webkit/upstream'
    for path in source.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.name.upper().startswith(('LICENSE','COPYING','COPYRIGHT','NOTICE')):
            target=notices/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,target)
    if not notices.exists(): raise ValueError('Upstream license inventory is missing')
    for path in stage.rglob('*'):
        if path.is_symlink():
            if not path.resolve().is_relative_to(stage): raise ValueError('External engine symlink')
        elif path.is_file():
            with path.open('rb') as stream: magic=stream.read(4)
            if magic==b'\x7fELF': container_build.validate_elf(path)
    helpers=[]
    for name in port['required_helpers']:
        matches=[p for p in stage.rglob(name) if p.is_file() and not p.is_symlink()]
        if len(matches)!=1: raise ValueError('Missing/ambiguous engine helper: '+name)
        with matches[0].open('rb') as stream:
            if stream.read(4)!=b'\x7fELF': raise ValueError('Engine helper is not an executable ELF')
        if name not in port['helper_capabilities']: raise ValueError('Unreviewed helper capabilities: '+name)
        helpers.append(dict(path=str(matches[0].relative_to(stage)),requires=port['helper_capabilities'][name]))
    webkit.write_json(work/'candidate.json',dict(format=1,target='mochios-1',abi_major=1,soname='libwebkit.so.1',
        source=plan['source'],inputs=plan['inputs'],sdk=plan['sdk'],build_plan_identity=plan['identity'],runtime_tested=False,publishable=False,
        verification='cross-build-only; native backend, helper Capability declarations and execution require separate verification',
        helpers=helpers,files=[dict(path=str(p.relative_to(stage)),sha256=webkit.digest(p)) for p in sorted(stage.rglob('*')) if p.is_file() and not p.is_symlink()]))


    archive=work/'webkit-candidate.tar.gz'
    with archive.open('wb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as compressed:
        with tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as tar:
            for path in [work/'candidate.json',*sorted(stage.rglob('*'))]:
                relative='candidate.json' if path==work/'candidate.json' else 'sysroot/'+str(path.relative_to(stage))
                info=tar.gettarinfo(path,arcname=relative)
                info.uid=info.gid=info.mtime=0;info.uname=info.gname=''
                if info.isfile():
                    with path.open('rb') as stream: tar.addfile(info,stream)
                else: tar.addfile(info)


if __name__=='__main__': main()
