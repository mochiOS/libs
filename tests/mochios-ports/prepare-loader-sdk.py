#!/usr/bin/env python3
"""Prepare an unpublished probe SDK exposing only real private-group loader APIs.

This does not update the release lock or install/publish an SDK. It requires an
existing authenticated-exec SDK candidate and an explicitly selected target
header. RTLD_GLOBAL/NOLOAD/default namespace support is not added by this tool.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

EXPORTS = ('dlopen', 'dlsym', 'dlclose', 'dlerror')

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--dlfcn-header',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    base,header,sdk=(p.resolve() for p in (args.base,args.dlfcn_header,args.output))
    if sdk.exists():parser.error('Choose a fresh output directory')
    metadata=json.loads((base/'candidate.json').read_text())
    if metadata.get('published') is not False or metadata.get('runtime_archive_sha256')!=sha(base/'lib/libmochi_user_newlib_runtime.a'):
        parser.error('Base must be a measured, unpublished runtime candidate')
    table=subprocess.check_output([base/'host/bin/nm','-g','--defined-only',base/'lib/libmochi_user_newlib_runtime.a'],text=True,stderr=subprocess.DEVNULL)
    actual={line.split()[2] for line in table.splitlines() if len(line.split())==3 and line.split()[1]=='T'}
    if not set(EXPORTS)<=actual:parser.error('Runtime does not implement the required loader APIs')
    header_bytes=header.read_bytes()
    if not all(name.encode() in header_bytes for name in EXPORTS):parser.error('Incomplete target dlfcn header')
    shutil.copytree(base,sdk,copy_function=os.link,symlinks=True)
    # Detach every file we change: the base/release SDK must remain immutable.
    for name in ('share/runtime.exports','share/runtime-roots.json','share/runtime-symbols.json','candidate.json'):
        path=sdk/name;content=path.read_bytes();path.unlink();path.write_bytes(content)
    target=sdk/'sysroot/include/dlfcn.h'
    target.unlink(missing_ok=True);target.write_bytes(header_bytes)
    path=sdk/'share/runtime.exports'
    path.write_text(path.read_text().replace('global:\n','global:\n'+''.join(name+';\n' for name in EXPORTS)))
    path=sdk/'share/runtime-roots.json'
    path.write_text(json.dumps(json.loads(path.read_text())+['-Wl,--undefined='+name for name in EXPORTS])+'\n')
    source=sdk.parent/(sdk.name+'-anchor.c');elf=sdk.parent/(sdk.name+'-anchor.elf')
    source.write_text('int main(void){return 0;}\n')
    subprocess.run([sdk/'bin/mochios-cc',source,'-o',elf],check=True)
    table=subprocess.check_output([sdk/'host/bin/nm','-D','--defined-only','--format=posix',elf],text=True)
    exported=sorted({line.split()[0] for line in table.splitlines() if len(line.split())>=2})
    if not set(EXPORTS)<=set(exported):raise ValueError('Linked executable does not export the loader ABI')
    (sdk/'share/runtime-symbols.json').write_text(json.dumps(exported)+'\n')
    metadata['additional_headers']={'dlfcn.h':sha(target)}
    metadata['additional_runtime_exports']=list(EXPORTS)
    (sdk/'candidate.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print('Unpublished loader probe SDK prepared; release SDK/lock unchanged')

if __name__=='__main__':main()
