#!/usr/bin/env python3
"""Plan/build an independent WebKit candidate; never publish or claim native execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import libs
import webkit
import releases
ROOT = Path(__file__).resolve().parents[1]


def plan(root=ROOT):
    """List real missing dependency recipes/backend sources before allocating a runner."""
    engine = root / 'engines/webkit'
    port = json.loads((engine / 'port.json').read_text())
    modules = json.loads((engine / 'dependencies.json').read_text())['pkg_config']
    catalog = libs.read_catalog(root)
    providers = {}
    for name, value in catalog['libraries'].items():
        if not value['enabled']: continue
        recipe = libs.recipe(root, value)
        for module in recipe.get('pkg_config_modules', []): providers.setdefault(module, []).append(name)
    for module, owner in port.get('module_providers', {}).items():
        if owner not in catalog['libraries'] or not catalog['libraries'][owner]['enabled']:
            raise ValueError('Unavailable module provider: '+owner)
        providers.setdefault(module, []).append(owner)
    missing = sorted(set(modules) - set(providers))
    reasons = ['No enabled target dependency recipe supplies pkg-config module ' + name for name in missing]
    sources = port.get('backend_sources', [])
    if not sources: reasons.append('Native WebKit/WPE backend and Capability process/surface integration are not implemented')
    for name in sources:
        if not libs.relative(engine, name).is_file(): reasons.append('Missing native backend source: ' + name)
    for name in port.get('patches',[]):
        if not libs.relative(engine,name).is_file(): reasons.append('Missing native patch: '+name)
    helpers=port['required_helpers']
    declarations=port.get('helper_capabilities',{})
    if not helpers or len(set(helpers))!=len(helpers) or any('/' in h or not h for h in helpers):
        raise ValueError('Invalid helper inventory')
    if set(declarations)-set(helpers): raise ValueError('Undeclared WebKit helper')
    if any(not isinstance(v,list) or any(not isinstance(c,str) or not c for c in v) or len(set(v))!=len(v) for v in declarations.values()):
        raise ValueError('Invalid helper Capability declarations')
    if missing_helpers:=sorted(set(helpers)-set(declarations)):
        reasons.append('WebKit helper Capability declarations have not been reviewed: '+', '.join(missing_helpers))
    sdk = libs.sdk_lock(root)
    if not sdk['available']: reasons.append('Shared SDK unavailable')
    inputs = {str(path.relative_to(root)): libs.hash_file(path) for path in sorted(engine.rglob('*'))
              if path.is_file() and not any(p in ['build', 'src-cache', '__pycache__'] for p in path.relative_to(engine).parts)}
    for name in ['webkit.py','webkit_ci.py','webkit_container.py','container_build.py','build_backends.py']:
        inputs['tools/'+name] = libs.hash_file(root/'tools'/name)
    identity = libs.identity({'inputs': inputs, 'sdk': sdk, 'dependency_plan': libs.make_plan(root)})
    return dict(format=1, ready=not reasons, build_status='ready' if not reasons else 'blocked',
                product='libwebkit.so.1', runtime_tested=False, identity=identity,
                source=json.loads((engine/'webkit.lock.json').read_text()), sdk=sdk,
                abi_major=1, missing_modules=missing, blockers=reasons, inputs=inputs)


def build(root, output):
    """Reject the retired parallel pipeline; engine builds use the normal registry."""
    selected=plan(root)
    libs.write_json(output/'plan.json',selected)
    raise ValueError('WebKit build blocked: use the standard libraries.toml recipe and tools/libs.py build; no separate engine pipeline is supported')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','build'])
    parser.add_argument('--output',type=Path,default=Path('out/webkit'))
    parser.add_argument('--github-output',type=Path)
    args=parser.parse_args()
    if args.command=='build': build(ROOT,args.output.resolve()); return
    report=plan();libs.write_json(args.output/'plan.json',report)
    if args.github_output:
        with args.github_output.open('a') as stream: stream.write('ready='+str(report['ready']).lower()+'\n')
    print(json.dumps(report,indent=2))
    if summary:=os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary,'a') as stream:
            stream.write('### WebKit: '+report['build_status']+'\n')
            for reason in report['blockers']: stream.write('- '+reason+'\n')
            stream.write('\nNo engine artifact or native-runtime success is implied by a planning/check job.\n')


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError) as error:
        print('WebKit build failed: '+str(error),file=sys.stderr);raise SystemExit(1)
