"""USB -> HTTPS gateway. Never replays a delivered command. Requires pyserial."""
import argparse
import json
import secrets
import time
import urllib.request
from pathlib import Path

ALLOWED = ('START', 'FILL', 'DRAIN', 'STOP', 'RESET')


def parse_status(line):
    if not line.startswith('OK STATUS '):
        raise ValueError('status_missing')
    result = dict(word.split('=', 1) for word in line.split()[2:] if '=' in word)
    if result.get('project') != 'water_auto_exchange' or result.get('version') not in ('0.3.0', '0.4.0', '0.5.0', '0.5.1', '0.5.2', '0.5.3', '0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3'):
        raise ValueError('firmware_mismatch')
    return result


class Controller:
    def __init__(self, port):
        import serial
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()

    def command(self, command):
        self.serial.reset_input_buffer()
        self.serial.write((command + '\r\n').encode('ascii'))
        deadline, pending = time.monotonic() + 3, b''
        while time.monotonic() < deadline:
            pending += self.serial.read(512)
            if len(pending) > 8192:
                raise ValueError('serial_response_too_long')
            while b'\n' in pending:
                raw, pending = pending.split(b'\n', 1)
                line = raw.decode('ascii', errors='replace').strip()
                if line.startswith(('OK ' + command + ' ', 'ERROR ' + command + ' ')):
                    return line
        raise TimeoutError('serial_response_timeout')

    def status(self):
        return parse_status(self.command('STATUS'))

    def close(self):
        self.serial.close()


def execute(controller, item, ttl_ms, elapsed):
    command = item.get('command')
    ack = dict(id=item.get('id'), status='rejected', result='invalid_or_expired_command')
    if command not in ALLOWED or not isinstance(ttl_ms, (int, float)) or elapsed * 1000 >= ttl_ms:
        return ack
    try:
        started = time.monotonic()
        status = controller.status()  # Re-identify before EVERY mutation.
        if command == 'DRAIN' and status.get('version') not in ('0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3'):
            ack['result'] = 'firmware_upgrade_required'
            return ack
        if (elapsed + time.monotonic() - started) * 1000 >= ttl_ms:
            return ack
        # Firmware applies fresh input, interlock and fault checks.
        response = controller.command(command)
        ack.update(status='succeeded' if response.startswith('OK ') else 'rejected', result=response[:256])
    except Exception:
        ack.update(status='uncertain', result='serial_failed_no_retry')
        try:
            controller.status()
            controller.command('STOP')
        except Exception:
            pass
    return ack


def post(url, key, value):
    request = urllib.request.Request(url + '/api/device/poll',
                                     json.dumps(value).encode(),
                                     {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, help='Verified USB user port, e.g. COM4')
    parser.add_argument('--config', required=True, help='Private JSON file with url and device_key')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    url = config['url'].rstrip('/')
    if not url.startswith('https://') or len(config['device_key']) < 32:
        raise SystemExit('HTTPS and a device key are required')
    gateway, controller, ack = secrets.token_hex(16), None, None
    try:
        while True:
            try:
                if controller is None:
                    controller = Controller(args.port)
                    controller.status()
                    # A gateway restart cannot silently resume an earlier remote cycle.
                    controller.command('STOP')
                status = controller.status()
                started = time.monotonic()
                response = post(url, config['device_key'], dict(gateway=gateway, status=status, ack=ack))
                ack = None
                if response.get('command'):
                    ack = execute(controller, response['command'], response.get('ttl_ms', 0), time.monotonic() - started)
                time.sleep(1)
            except Exception as exc:
                print('Gateway unavailable; attempting local STOP. ' + type(exc).__name__, flush=True)
                if controller is not None:
                    try:
                        controller.status()
                        controller.command('STOP')
                    except Exception:
                        pass
                    controller.close()
                    controller = None
                time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        if controller is not None:
            try:
                controller.status()
                print(controller.command('STOP'), flush=True)
            finally:
                controller.close()


if __name__ == '__main__':
    main()
