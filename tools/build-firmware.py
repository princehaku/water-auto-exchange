"""Prepare the exact flash list without modifying tracked GPIO or credentials."""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ('main.lua', 'water_config.lua', 'water_cycle.lua', 'water_control.lua',
         'water_usb.lua', 'water_network.lua', 'water_network_config.lua', 'water_ws_transport.lua')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Private JSON: url, device_key')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    # ASCII restriction makes JSON string escaping also safe for Lua 5.1 here.
    import re
    if not re.fullmatch(r'https://[a-zA-Z0-9.-]+/[a-zA-Z0-9/_-]+', config.get('url', '')):
        raise SystemExit('Invalid HTTPS URL')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{32,128}', config.get('device_key', '')):
        raise SystemExit('Invalid device key')
    output = ROOT / 'build' / 'firmware'
    output.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(ROOT / 'src' / name, output / name)
    shutil.copyfile(ROOT / 'certs' / 'water-ca.crt', output / 'water-ca.crt')
    template = (ROOT / 'src/water_network_config.lua').read_text(encoding='utf-8-sig')
    template = template.replace('enabled = false', 'enabled = true')
    ws_url = config['url'].rstrip('/').replace('https://', 'wss://', 1) + '/api/device/ws'
    template = template.replace('"wss://bytegallop.com/water/api/device/ws"', json.dumps(ws_url))
    template = template.replace('device_key = ""', 'device_key = ' + json.dumps(config['device_key']))
    (output / 'water_network_config.lua').write_text(template, encoding='utf-8')
    (output / 'flash-files.txt').write_text('\n'.join(str(output / n) for n in FILES + ('water-ca.crt',)) + '\n', encoding='utf-8')
    print('Prepared 8 Lua files and 1 CA certificate in build/firmware; WSS enabled, GPIO mapping unchanged.')
    print('Import exactly the paths in build/firmware/flash-files.txt. The user performs the flash.')


if __name__ == '__main__':
    main()
