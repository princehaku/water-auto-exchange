"""Refuse to replace the control service while any output or work is active."""
import json
import sys
import urllib.request
from pathlib import Path


def idle_reason(snapshot):
    device = snapshot.get('device') or {}
    if not snapshot.get('online') or device.get('outputs_known') != '1':
        return 'device state is not confirmed online'
    if device.get('fill') != '0' or device.get('drain') != '0':
        return 'an output is still open'
    if (snapshot.get('level_job') or {}).get('status') == 'running':
        return 'an automatic task is still running'
    if any((run or {}).get('status') == 'running' for run in (snapshot.get('output_runs') or {}).values()):
        return 'a manual task is still running'
    if any(command.get('status') in ('queued', 'delivered') for command in snapshot.get('commands', [])):
        return 'a command is awaiting confirmation'
    control = snapshot.get('control_limits') or {}
    if control.get('timeout_pending') or any(control.get(name) is not None for name in ('fill_on_since', 'drain_on_since', 'fill_deadline', 'drain_deadline')):
        return 'output authorization is still active'
    return ''


def main():
    env = {}
    for line in Path('/apps/water-auto-exchange/config/water.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            name, value = line.split('=', 1)
            env[name.strip()] = value.strip().strip('"').strip("'")
    cookie = None

    def request(path, body=None):
        headers = {'Origin': env.get('WATER_ORIGIN', 'https://bytegallop.com')}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if cookie:
            headers['Cookie'] = cookie
        req = urllib.request.Request('http://127.0.0.1:8790/water/api/' + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.load(response), response.headers

    try:
        _, headers = request('login', {'key': env['WATER_ADMIN_KEY']})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        snapshot, _ = request('status')
        reason = idle_reason(snapshot)
        if reason:
            print('Deployment deferred: ' + reason + '. Existing service stays running.', file=sys.stderr)
            return 2
        print('PASS deployment preflight: both outputs closed, no running tasks or pending commands.')
        return 0
    except Exception:
        print('Deployment deferred: unable to verify idle state. Existing service stays running.', file=sys.stderr)
        return 2
    finally:
        if cookie:
            try:
                request('logout', {})
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
