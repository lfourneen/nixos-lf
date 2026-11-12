#!/usr/bin/env python3
"""Run selected component checks and archive exact inputs and outcomes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


def capture(args, cwd):
    try:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--component', action='append', default=[])
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--online', action='store_true')
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / 'system/config/network-upgrades.json').read_text())['components']
    selected = sorted(inventory) if args.all else args.component
    if not selected or any(name not in inventory for name in selected):
        parser.error('choose --all or --component from: ' + ', '.join(sorted(inventory)))
    checks = sorted({check for name in selected for check in inventory[name]['checks']})
    if args.plan:
        print(json.dumps({'components': selected, 'checks': checks}, indent=2))
        return 0
    archive = args.archive or root / 'audit/validation' / time.strftime('%Y%m%d-%H%M%S')
    archive.mkdir(parents=True, exist_ok=False, mode=0o700)
    diff = subprocess.run(['git', 'diff', 'HEAD', '--binary'], cwd=root, capture_output=True, check=True).stdout
    manifest = {'schema': 1, 'commit': capture(['git', 'rev-parse', 'HEAD'], root),
                'diff_sha256': hashlib.sha256(diff).hexdigest(),
                'lock_sha256': hashlib.sha256((root / 'flake.lock').read_bytes()).hexdigest(),
                'host_kernel': capture(['uname', '-r'], root),
                'nix': capture(['nix', '--version'], root), 'components': selected, 'checks': [{'name':name, 'status':'not-run'} for name in checks]}
    offline = [] if args.online else ['--offline']
    packages = capture(['nix', 'eval', *offline, '--json', '.#nixosConfigurations.lfour.config',
                        '--apply', 'c: { kernel = c.boot.kernelPackages.kernel.version; '
                        'mihomo = c.programs.clash-verge.package.managedCore.version; '
                        'gost = c.my.proxy.gostPackage.version; unbound = c.services.unbound.package.version; }'], root)
    manifest['packages'] = json.loads(packages) if packages else {'status': 'unavailable'}
    (archive / 'results.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for record in manifest['checks']:
        check = record['name']
        print('Checking ' + check, flush=True)
        start = time.monotonic()
        current_diff = subprocess.run(['git', 'diff', 'HEAD', '--binary'], cwd=root,
                                      capture_output=True, check=True).stdout
        record['diff_sha256'] = hashlib.sha256(current_diff).hexdigest()
        with (archive / (check + '.log')).open('wb') as log:
            try:
                result = subprocess.run(['nix', 'build', '-L', *offline, '--no-link', '--json',
                                         '.#checks.x86_64-linux.' + check], cwd=root,
                                        stdout=subprocess.PIPE, stderr=log, timeout=args.timeout, check=False)
                record['exit_code'] = result.returncode
                record['status'] = 'passed' if result.returncode == 0 else 'failed'
                if result.returncode == 0:
                    record['outputs'] = json.loads(result.stdout)
            except FileNotFoundError:
                record.update(status='environment-unavailable', error='nix-missing')
            except subprocess.TimeoutExpired:
                record.update(status='failed', error='timeout')
        record['seconds'] = round(time.monotonic() - start, 3)
        (archive / 'results.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(str(archive / 'results.json'), flush=True)
    manifest['input_changed'] = any(check.get('diff_sha256') != manifest['diff_sha256']
                                    for check in manifest['checks'])
    (archive / 'results.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return 0 if not manifest['input_changed'] and all(check['status'] == 'passed' for check in manifest['checks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
