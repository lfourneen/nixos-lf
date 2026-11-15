"""Compare source candidates and bind an explicit apply to the displayed update."""
import argparse
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unicodedata

APPROVABLE = {'node_identity', 'node_authentication', 'node_transport', 'sensitive_route'}
IDENTITY = ('type', 'port', 'servername', 'sni', 'public-key')
AUTH = ('password', 'username', 'uuid', 'private-key', 'token')
TRANSPORT = ('tls', 'network', 'plugin', 'plugin-opts', 'ws-opts', 'grpc-opts', 'reality-opts')


class ReviewError(ValueError):
    pass


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def node_changes(before, after):
    old = {node['name']: node for node in before.get('proxies', [])}
    new = {node['name']: node for node in after.get('proxies', [])}
    result = []
    for name, node in new.items():
        previous = old.get(name)
        changes = []
        if previous is None:
            previous = {}
        for field in ('logical-server',) + IDENTITY + AUTH + TRANSPORT:
            if field == 'logical-server':
                first = previous.get('lf-source-server', previous.get('server'))
                second = node.get('lf-source-server', node.get('server'))
            else:
                first, second = previous.get(field), node.get(field)
            if first != second:
                hidden = field in AUTH or field.endswith('-opts')
                changes.append({'field': field, 'hidden': hidden,
                    **({} if hidden else {'before': first, 'after': second})})
        if changes:
            result.append({'name': name, 'added': name not in old, 'changes': changes})
    return result, sorted(old.keys() - new.keys())


def context(settings, source, private):
    stage_settings = json.loads(Path(settings['stageSettings']).read_text())
    stage = module('review_stage', stage_settings['snapshot_tool'])
    worker = module('review_worker', settings['worker'])
    _, filename = stage.source_input(stage_settings)
    candidate = Path(filename).parent
    if stage.source_directory(stage_settings) != source:
        raise ReviewError('Source changed. Run clash-diff again.')
    try:
        settled = json.loads(private(source / 'approved/settled.json'))
    except FileNotFoundError:
        raise ReviewError('This source has no accepted baseline to compare yet.') from None
    baseline_id = settled['digest']
    if not isinstance(baseline_id, str) or not re.fullmatch('[0-9a-f]{64}', baseline_id):
        raise ReviewError('The accepted baseline has an invalid identity.')
    baseline = source / 'approved' / baseline_id
    stage.snapshot_metadata(baseline)
    if baseline.is_symlink() or stage.snapshot_digest(baseline) != baseline_id:
        raise ReviewError('The accepted baseline has changed.')
    registry = private(source / 'registry.json')
    if json.loads((baseline / 'source-manifest.json').read_text()).get('source_id') != source.name:
        raise ReviewError('The accepted baseline belongs to another source.')
    candidate_id = stage.snapshot_digest(candidate)
    record = stage.read_record(Path(stage_settings['state']) / 'profile-refresh.json')
    current_audit = record.get('digest') == candidate_id
    audit = (record.get('audit') or {}) if current_audit else {}
    deny, unknown = audit.get('deny') or {}, audit.get('unknown') or {}
    before = worker.load(private(baseline / 'clash-verge.yaml', 32 * 1024 * 1024))
    after = worker.load(private(candidate / 'clash-verge.yaml', 32 * 1024 * 1024))
    changes, removed = node_changes(before, after)
    rules = list(difflib.unified_diff(before.get('rules') or [], after.get('rules') or [],
                                     fromfile='accepted', tofile='candidate', lineterm=''))
    old_groups, new_groups = before.get('proxy-groups') or [], after.get('proxy-groups') or []
    groups = []
    old_by_name = {group['name']: group for group in old_groups}
    for group in new_groups:
        previous = old_by_name.get(group['name'], {})
        fields = sorted(key for key in previous.keys() | group.keys() if previous.get(key) != group.get(key))
        if fields:
            groups.append({'name': group['name'], 'changes': [dict(field=key,
                hidden=key not in ('type', 'proxies', 'use'),
                **({'before': previous.get(key), 'after': group.get(key)}
                   if key in ('type', 'proxies', 'use') else {})) for key in fields]})
    signature = dict(source_id=source.name, candidate=candidate_id, baseline=baseline_id,
                     generation=candidate.name, revision=hashlib.sha256(registry).hexdigest(),
                     policy=hashlib.sha256(Path(stage_settings['managed_policy']).read_bytes()).hexdigest(),
                     categories=sorted(deny), audit=audit)
    blocked = sorted((set(deny) - APPROVABLE) | set(unknown))
    if candidate_id == baseline_id:
        blocked = ['no pending change']
    elif not current_audit:
        blocked = ['awaiting candidate audit']
    elif record.get('result') not in ('fallback', 'refused'):
        blocked = ['already applied or verifying']
    elif not deny and not blocked:
        blocked = [record.get('reason') or 'no rejected semantic change']
    report = dict(source=source.name, candidate=candidate_id, baseline=baseline_id,
                  audit=audit, nodes=changes, removed_nodes=removed, rules=rules, groups=groups,
                  removed_groups=sorted(old_by_name.keys() - {group['name'] for group in new_groups}),
                  before_nodes=len(before.get('proxies') or []), after_nodes=len(after.get('proxies') or []),
                  can_apply=bool(deny) and not blocked, blocked_categories=blocked,
                  mode=stage.read_record(Path(stage_settings['state']) / 'state.json').get('mode', 'unknown'),
                  intent=(Path(stage_settings['state']) / 'intent').read_text().strip())
    return signature, report, stage, stage_settings


def compare(settings, source, private, atomic):
    signature, report, _, _ = context(settings, source, private)
    (source / 'review.json').unlink(missing_ok=True)
    if report['can_apply']:
        atomic(source / 'review.json', json.dumps(dict(signature, expires=time.time() + 3600)).encode())
    return report


def approve_reviewed(settings, source, private, atomic):
    try:
        reviewed = json.loads(private(source / 'review.json'))
    except FileNotFoundError:
        raise ReviewError('Run clash-diff before clash-apply.') from None
    signature, report, stage, stage_settings = context(settings, source, private)
    expires = reviewed.pop('expires', 0)
    if reviewed != signature or not 0 < expires - time.time() <= 3600:
        raise ReviewError('The reviewed update changed or expired. Run clash-diff again.')
    if not report['can_apply']:
        raise ReviewError('This candidate has checks that cannot be approved.')
    approval = {key: signature[key] for key in ('candidate', 'baseline', 'revision', 'policy', 'categories')}
    atomic(source / 'approval.json', json.dumps(dict(approval, expires=time.time() + 3600)).encode())
    return signature, stage, stage_settings


def clean(value):
    return ''.join(char if not unicodedata.category(char).startswith('C') else ' ' for char in str(value))[:1000]


def width(value):
    return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in 'WF' else 1
               for char in value)


def panel(title, rows):
    columns = max(40, min(shutil.get_terminal_size((100, 24)).columns, 140))
    inner = columns - 4
    title = clean(title)
    if width(title) > inner:
        while width(title + '…') > inner:
            title = title[:-1]
        title += '…'
    print('╭ ' + title + ' ' + '─' * max(0, columns - width(title) - 4) + '╮')
    for value in rows:
        line = ''
        for char in clean(value):
            if width(line + char) > inner:
                print('│ ' + line + ' ' * (inner - width(line)) + ' │')
                line = ''
            line += char
        print('│ ' + line + ' ' * (inner - width(line)) + ' │')
    print('╰' + '─' * (columns - 2) + '╯')


def change_lines(changes):
    rows = []
    for change in changes:
        if change['hidden']:
            rows.append(change['field'] + ': changed (values hidden)')
        else:
            def shown(value):
                return '(absent)' if value is None else json.dumps(value, ensure_ascii=False)
            rows.extend([change['field'], '  old: ' + shown(change['before']), '  new: ' + shown(change['after'])])
    return rows


def render(report):
    deny = report['audit'].get('deny') or {}
    panel('Subscription changes', [
        'Source       ' + report['source'],
        'Accepted     ' + report['baseline'][:12],
        'Candidate    ' + report['candidate'][:12],
        'Mode         ' + mode_text(report.get('mode'), report.get('intent')),
        f"Nodes        {report['before_nodes']} → {report['after_nodes']}",
        'Audit        ' + ', '.join(f'{name}: {count}' for name, count in deny.items()),
        'Apply        ' + ('clash-apply · this reviewed candidate only' if report['can_apply'] else
                          'blocked · ' + ', '.join(report['blocked_categories'])),
    ])
    for node in report['nodes']:
        panel(('Added · ' if node['added'] else '') + node['name'], change_lines(node['changes']))
    if report['removed_nodes']:
        panel('Removed nodes', report['removed_nodes'])
    for group in report['groups']:
        panel('Group · ' + group['name'], change_lines(group['changes']))
    if report['removed_groups']:
        panel('Removed groups', report['removed_groups'])
    if report['rules']:
        panel('Routing changes', report['rules'])


def mode_text(mode, intent):
    names = {'direct': 'A · Direct', 'proxy': 'B · Proxy', 'blocked': 'C · Protected'}
    return names.get(mode, 'Unknown') + '; requested ' + names.get(intent, 'Unknown')


def main():
    settings_path, action = sys.argv[1:3]
    parser = argparse.ArgumentParser(prog='clash-diff' if action == 'diff' else 'clash-apply',
        description='Compare source changes without authentication values.' if action == 'diff' else
                    'Approve and apply the candidate last displayed by clash-diff.')
    parser.add_argument('--json', action='store_true', help='print structured output')
    args = parser.parse_args(sys.argv[3:])
    settings = json.loads(Path(settings_path).read_text())
    command = [settings['python'], '-I', settings['broker'], settings_path, action]
    if os.geteuid() != 0:
        command = ['/run/wrappers/bin/sudo', '--', *command]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE)
    if result.returncode:
        raise SystemExit(result.returncode)
    report = json.loads(result.stdout)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    elif action == 'diff':
        render(report)
    else:
        panel('Subscription update', ['Source       ' + report['source'],
              'Candidate    ' + report['candidate'][:12],
              'Mode         ' + mode_text(report.get('mode'), report.get('intent')),
              'Result       ' + ('Applied · health verification pending' if report['result'] == 'applied'
                                else 'Approved · run clash-on to activate')])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
