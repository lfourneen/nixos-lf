"""Read-only source comparison without authentication values."""
import argparse
import difflib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata

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


def compare(settings, source, private):
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
    if json.loads((baseline / 'source-manifest.json').read_text()).get('source_id') != source.name:
        raise ReviewError('The accepted baseline belongs to another source.')
    candidate_id = stage.snapshot_digest(candidate)
    record = stage.read_record(Path(stage_settings['state']) / 'profile-refresh.json')
    current_audit = record.get('digest') == candidate_id
    audit = (record.get('audit') or {}) if current_audit else {}
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
    report = dict(source=source.name, candidate=candidate_id, baseline=baseline_id,
                  audit=audit, nodes=changes, removed_nodes=removed, rules=rules, groups=groups,
                  removed_groups=sorted(old_by_name.keys() - {group['name'] for group in new_groups}),
                  before_nodes=len(before.get('proxies') or []), after_nodes=len(after.get('proxies') or []),
                  audit_current=current_audit,
                  mode=stage.read_record(Path(stage_settings['state']) / 'state.json').get('mode', 'unknown'),
                  intent=(Path(stage_settings['state']) / 'intent').read_text().strip())
    return report


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
    counts = [f'{field} {name}={count}' for field in ('deny', 'warn', 'unknown')
              for name, count in sorted(report['audit'].get(field, {}).items())]
    panel('Subscription changes', [
        'Source       ' + report['source'],
        'Accepted     ' + report['baseline'][:12],
        'Candidate    ' + report['candidate'][:12],
        'Mode         ' + mode_text(report.get('mode'), report.get('intent')),
        f"Nodes        {report['before_nodes']} → {report['after_nodes']}",
        'Audit        ' + (', '.join(counts) or 'no findings' if report['audit_current'] else 'awaiting candidate audit'),
        'Diagnostic   Read-only · use proxy-status for automatic refresh results',
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
    settings_path = sys.argv[1]
    parser = argparse.ArgumentParser(prog='clash-diff',
        description='Read-only source comparison without authentication values or network mode changes.')
    parser.add_argument('--json', action='store_true', help='print structured output')
    args = parser.parse_args(sys.argv[2:])
    settings = json.loads(Path(settings_path).read_text())
    command = [settings['python'], '-I', settings['broker'], settings_path, 'diff']
    if os.geteuid() != 0:
        command = ['/run/wrappers/bin/sudo', '--', *command]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE)
    if result.returncode:
        raise SystemExit(result.returncode)
    report = json.loads(result.stdout)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        render(report)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
