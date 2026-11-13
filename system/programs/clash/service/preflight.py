"""Read-only preparation check and credential-free subscription summary."""
import importlib.util
import grp
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import tempfile
import time


def disables_tls(value):
    if isinstance(value, dict):
        for key, flag in value.items():
            if str(key).lower().replace('_', '-') == 'skip-cert-verify':
                if not (flag is False or type(flag) in (int, float) and flag == 0 or
                        isinstance(flag, str) and flag in ('0', 'f', 'F', 'false', 'False', 'FALSE')):
                    return True
            if disables_tls(flag):
                return True
    elif isinstance(value, list):
        return any(disables_tls(item) for item in value)
    return False


def subscription_check(document, policy):
    proxies, providers = document.get('proxies') or [], document.get('proxy-providers') or {}
    if not isinstance(proxies, list) or not isinstance(providers, dict):
        raise ValueError('invalid node/provider structure')
    exceptions = policy.get('insecure-proxy-exceptions', [])
    insecure = [p for p in proxies if disables_tls(p)]
    rejected = sum(not isinstance(p, dict) or p.get('name') not in exceptions for p in insecure)
    issues = []
    if rejected:
        issues.append(f'{rejected} inline nodes disable TLS certificate verification without an exception')
    if not proxies and not providers:
        issues.append('no configured nodes or providers')
    if providers and policy.get('subscription-trust') == 'fixed':
        issues.append('fixed subscription policy refuses proxy providers')
    hosts = set()
    for provider in providers.values():
        if isinstance(provider, dict):
            if provider.get('type') != 'inline' and provider.get('path'):
                cache_path = Path(str(provider['path']))
                if cache_path.is_absolute() or '..' in cache_path.parts or cache_path.parts[:1] != ('proxies',):
                    issues.append('provider paths must be relative to the managed proxies/ cache directory')
            try:
                hosts.add(urlsplit(str(provider.get('url', ''))).hostname or 'local-file')
            except ValueError:
                issues.append('invalid provider URL')
    return {'inline_nodes': len(proxies), 'mutable_sources': len(providers),
            'certificate_exceptions_requested': len(insecure),
            'certificate_exceptions_allowed': len(insecure) - rejected,
            'certificate_exceptions_rejected': rejected, 'provider_hosts': sorted(hosts),
            'named_certificate_exceptions': len(exceptions),
            'provider_contents': 'runtime-validation' if providers else 'none'}, issues


def inspect_unlocked(settings):
    spec = importlib.util.spec_from_file_location('snapshot', settings['snapshot_tool'])
    snapshot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snapshot)
    issues = []
    try:
        if settings.get('source_root'):
            settings, _ = snapshot.source_input(settings)
        data = snapshot.config_input(settings, str(Path(settings['gui_data']) / 'clash-verge.yaml'))
        parse = [settings['yq'], '-o=json', '-I=0', '.', '-']
        if os.geteuid() == 0:
            parse = [settings['setpriv'], '--reuid=clash-core', '--regid=clash-core', '--clear-groups',
                     '--bounding-set=-all', '--no-new-privs', *parse]
        result = subprocess.run(parse, input=data,
                                capture_output=True, timeout=8, check=False)
        if result.returncode or len(result.stdout) > snapshot.MAX_BYTES:
            raise ValueError('profile parse failed')
        document = json.loads(result.stdout)
        if not isinstance(document, dict):
            raise ValueError('profile is not a mapping')
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'ready': False, 'phase': 'prepare-profile', 'issues': ['Registered source candidate or legacy GUI profile missing, unsafe or invalid'],
                'next_action': 'Register and refresh the controlled source, or prepare the legacy GUI profile; rerun clash-on.'}
    names = {path.name.lower() for path in Path(settings['gui_data']).iterdir() if path.is_file()}
    rules = '\n'.join(str(rule) for rule in document.get('rules', []))
    for match, asset in [('GEOSITE,', 'GeoSite.dat'),
                          ('GEOIP,', 'GeoIP.dat' if document.get('geodata-mode') else 'Country.mmdb')]:
        if match in rules.upper() and not any(alias in names for alias in snapshot.ASSETS[asset]):
            issues.append('missing required asset: ' + asset)
    try:
        policy_path = os.environ.get('LF_MANAGED_PROXY_POLICY', settings['managed_policy'])
        policy = json.loads(Path(policy_path).read_text())['lf-private-targets']
        subscription, policy_issues = subscription_check(document, policy)
    except (OSError, ValueError, KeyError, TypeError):
        return {'ready': False, 'phase': 'prepare-policy', 'issues': ['subscription policy unavailable or invalid'],
                'next_action': 'Check the managed policy and GUI profile; rerun proxy-control preflight.'}
    if policy_issues:
        return {'ready': False, 'phase': 'prepare-policy', 'issues': policy_issues,
                'subscription': subscription,
                'next_action': 'Regenerate the GUI profile with the managed Script.js; use exact-name exceptions only for deliberately trusted nodes.'}
    phase = 'prepare-assets' if issues else 'prepared'
    if not issues and settings.get('managed_core'):
        try:
            gid = grp.getgrnam('clash-core').gr_gid if os.geteuid() == 0 else os.getegid()
            with tempfile.TemporaryDirectory(prefix='proxy-preflight-') as directory:
                candidate = Path(directory)
                snapshot.private_dir(candidate, gid)
                snapshot.write_private(candidate / 'clash-verge.yaml', data, gid)
                for asset, aliases in snapshot.ASSETS.items():
                    for name in names:
                        if name in aliases:
                            # Preserve the actual case from the directory.
                            source = next(p for p in Path(settings['gui_data']).iterdir() if p.name.lower() == name)
                            snapshot.write_private(candidate / asset, snapshot.read_input(
                                settings['gui_data'], source.name, settings['desktop_uid']), gid)
                args = [settings['managed_core'], '-t', '-d', directory, '-f', str(candidate / 'clash-verge.yaml')]
                if os.geteuid() == 0:
                    args = [settings['setpriv'], '--reuid=clash-core', '--regid=clash-core', '--clear-groups',
                            '--bounding-set=-all', '--no-new-privs', *args]
                env = dict(os.environ, LF_MANAGED_PROXY_POLICY=os.environ.get('LF_MANAGED_PROXY_POLICY', settings['managed_policy']))
                parsed = subprocess.run(args, env=env, capture_output=True, timeout=60, check=False)
                if parsed.returncode:
                    if os.geteuid() == 0:
                        log = Path(settings['state']) / 'preflight.log'
                        log.parent.mkdir(parents=True, exist_ok=True)
                        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                        with os.fdopen(fd, 'wb') as stream:
                            os.fchmod(stream.fileno(), 0o600)
                            stream.write((parsed.stdout + parsed.stderr)[-65536:])
                    phase = 'prepare-core'
                    issues.append('managed core rejected the profile; inspect the root-only preflight.log')
        except (OSError, ValueError, subprocess.SubprocessError):
            phase = 'prepare-core'
            issues.append('managed profile validation unavailable')
    return {'ready': not issues, 'phase': phase, 'issues': issues,
            'scope': 'configuration', 'subscription': subscription,
            'warnings': ['Provider contents are validated at load/refresh. Named certificate exceptions also follow same-named provider replacements.']
                        if subscription['mutable_sources'] else [],
            'next_action': 'clash-on' if not issues else 'Check required assets and the root-only /run/proxy-mode/preflight.log; regenerate the GUI profile and rerun preflight.'}


def inspect(settings):
    spec = importlib.util.spec_from_file_location('snapshot_lock', settings['snapshot_tool'])
    snapshot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snapshot)
    try:
        with snapshot.source_lock(settings):
            return inspect_unlocked(settings)
    except OSError:
        return {'ready': False, 'phase': 'prepare-profile',
                'issues': ['Controlled source has not been registered'],
                'next_action': 'sudo clash-source review/register, then refresh'}


def publish(result, settings):
    # Only fixed classifications and counts are published, never parser logs,
    # provider URLs/hosts, node names or credentials.
    if os.geteuid() == 0:
        summary = {key: result[key] for key in ('ready', 'phase', 'scope') if key in result}
        summary['subscription'] = {key: value for key, value in result.get('subscription', {}).items()
                                   if key != 'provider_hosts'}
        summary['monotonic_seconds'] = time.clock_gettime(time.CLOCK_BOOTTIME)
        try:
            summary['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            summary['boot_id'] = ''
        destination = Path(settings['state']) / 'preflight.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name('preflight.json.new.' + str(os.getpid()))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), 0o644)
                json.dump(summary, stream)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    result = inspect(settings)
    publish(result, settings)
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
