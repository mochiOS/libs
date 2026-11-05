#!/usr/bin/env python3
"""Build and test the native GIO provider on Linux using an isolated test CA.

The mochiOS target build rejects this fixture feature at compile time. This
runner does not install packages, change trust stores or publish artifacts.
"""
import argparse
from pathlib import Path
import subprocess
import os
import shutil
import tarfile
import io
import json

root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--user-source',type=Path,required=True)
parser.add_argument('--glib-networking-source',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
user=args.user_source.resolve()
lock=json.loads((root/'sources.lock.json').read_text())['sources']['glib-networking']
source=output/'source'
if source.exists():
    raise SystemExit('Choose a fresh output directory so patches cannot be applied twice')
source.mkdir()
data=subprocess.check_output(['git','-C',str(args.glib_networking_source.resolve()),'archive',lock['commit']])
with tarfile.open(fileobj=io.BytesIO(data)) as archive:archive.extractall(source,filter='data')
subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(root/'recipes/glib-networking/mochios.patch')],cwd=source,check=True)
subprocess.run(['python3',str(user/'crates/tls-stream-ffi/tests/run-host.py'),'--output',str(output/'engine')],check=True)
engine=output/'engine'
archive=engine/'libmochios_tls_pic.a'
shutil.copy2(engine/'rust/x86_64-unknown-linux-gnu/release/libmochios_tls_stream_ffi.a',archive)
subprocess.run(['cc','-O2','-fPIC','-I'+str(engine/'include'),'-c',str(user/'crates/tls-stream-ffi/native/tls.c'),'-o',str(engine/'tls.o')],check=True)
subprocess.run(['ar','rcs',str(archive),str(engine/'tls.o')],check=True)
build=output/'build'
subprocess.run(['meson','setup',str(build),str(source/'tls/mochios'),'--buildtype=release',
    '-Dc_args='+json.dumps(['-I'+str(engine/'include')]),
    '-Dc_link_args='+json.dumps(['-L'+str(engine),'-pthread','-ldl','-lm'])],check=True)
subprocess.run(['meson','compile','-C',str(build),'-j','2'],check=True)
import shlex
flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','gio-2.0'],text=True))
client=output/'client'
subprocess.run(['cc',str(root/'tests/gio-tls/client.c'),'-o',str(client),'-L'+str(build),
    '-Wl,--no-as-needed','-lgiomochios','-Wl,--as-needed','-Wl,-rpath,'+str(build),*flags],check=True)
subprocess.run(['python3',str(root/'tests/gio-tls/network.py'),'--user-source',str(user),'--client',str(client)],check=True)
