"""Local HTTP + Edge check for the separate aquarium page. No physical outputs."""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/debug-python'))
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from server.app import Handler, Server, Store


class StaticHandler(Handler):
    def do_GET(self):
        name = self.path.removeprefix('/water/')
        allowed = ('aquarium.html', 'aquarium.css', 'aquarium.js', 'aquarium-scene.js',
                   'vendor/three.module.js', 'index.html', 'style.css', 'app.js')
        if name not in allowed:
            return self.dispatch()
        data = (ROOT / 'deploy/www' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/css' if name.endswith('.css') else
                         'text/javascript' if name.endswith('.js') else 'text/html')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    store = Store(':memory:')
    server = Server(('127.0.0.1', 0), store, 'test-admin-key-' * 3, 'test-device-key-' * 3, '')
    server.RequestHandlerClass = StaticHandler
    server.origin = 'http://127.0.0.1:' + str(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    status = dict(project='water_auto_exchange', version='0.8.0', control_mode='manual',
                  state='IDLE', reason='ready', ready='1', fill='0', drain='0',
                  outputs_known='1', need_fill='unknown', overflow='0', cycle='0',
                  overflow_protection='0')
    gateway = 'a' * 32
    store.poll(gateway, status)
    def newest_command(expected, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            commands = store.snapshot()['commands']
            if len(commands) >= count:
                assert commands[0]['command'] == expected
                return commands[0]
            time.sleep(.05)
        raise AssertionError('command was not submitted: ' + expected)
    output = ROOT / 'build/browser'
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True,
                                        args=['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.origin + '/water/aquarium.html')
            page.locator('#key').fill(server.admin_key)
            page.get_by_role('button', name='进入控制台').click()
            expect(page.locator('#console')).to_be_visible()
            expect(page.locator('#header-connection')).to_contain_text('设备在线')
            expect(page.locator('#fill-button')).to_be_enabled()
            assert page.evaluate("document.querySelector('#tank-canvas').width > 0")
            assert page.evaluate("document.querySelector('#scene-fallback').hidden")

            page.locator('#fill-seconds').fill('100')
            page.locator('#drain-seconds').fill('50')
            page.locator('[data-anchor="100"]').click()
            page.locator('#save-calibration').click()
            expect(page.locator('#level-value')).to_have_text('100%')
            page.locator('#fill-button').click()
            newest_command('FILL', 1)
            store.poll(gateway, status)
            status.update(state='FILLING', fill='1', reason='manual_filling')
            store.poll(gateway, status, dict(id=store.snapshot()['commands'][0]['id'],
                                             status='succeeded', result='OK FILL'))
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true', timeout=6000)
            expect(page.locator('#drain-button')).to_be_enabled()
            page.locator('#drain-button').click()
            newest_command('DRAIN', 2)
            store.poll(gateway, status)
            status.update(state='EXCHANGING', drain='1', reason='manual_exchanging')
            store.poll(gateway, status, dict(id=store.snapshot()['commands'][0]['id'],
                                             status='succeeded', result='OK DRAIN'))
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true', timeout=6000)
            expect(page.locator('#save-calibration')).to_be_disabled()
            page.locator('#fill-button').click()
            newest_command('FILL_OFF', 3)
            store.poll(gateway, status)
            status.update(state='DRAINING', fill='0', reason='manual_draining')
            store.poll(gateway, status, dict(id=store.snapshot()['commands'][0]['id'],
                                             status='succeeded', result='OK FILL_OFF'))
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false', timeout=6000)
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true')
            page.locator('#drain-button').click()
            newest_command('DRAIN_OFF', 4)
            store.poll(gateway, status)
            status.update(state='IDLE', drain='0', reason='stopped')
            store.poll(gateway, status, dict(id=store.snapshot()['commands'][0]['id'],
                                             status='succeeded', result='OK DRAIN_OFF'))
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false', timeout=6000)
            page.locator('#history-toggle').click()
            expect(page.locator('#commands-list tr')).to_have_count(4)
            page.locator('#tab-connection').click()
            expect(page.locator('#connection-panel')).to_be_visible()
            page.locator('#tab-commands').click()
            page.screenshot(path=str(output / 'aquarium-desktop.png'), full_page=True)

            mobile = browser.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=1)
            mobile.on('pageerror', lambda error: errors.append(str(error)))
            mobile.goto(server.origin + '/water/aquarium.html')
            mobile.locator('#key').fill(server.admin_key)
            mobile.get_by_role('button', name='进入控制台').click()
            expect(mobile.locator('#console')).to_be_visible()
            assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            mobile.screenshot(path=str(output / 'aquarium-mobile.png'), full_page=True)
            status.update(version='0.7.7', state='IDLE', fill='0', drain='0')
            store.poll(gateway, status)
            expect(page.locator('#version')).to_have_text('v0.7.7', timeout=6000)
            page.locator('#fill-button').click()
            newest_command('FILL', 5)
            store.poll(gateway, status)
            status.update(state='FILLING', fill='1')
            store.poll(gateway, status, dict(id=store.snapshot()['commands'][0]['id'],
                                             status='succeeded', result='OK FILL'))
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true', timeout=6000)
            expect(page.locator('#drain-button')).to_be_disabled()
            store.gateway = None
            store.connection_event(False, 'peer_disconnected')
            expect(page.locator('#header-connection')).to_contain_text('设备离线', timeout=6000)
            expect(page.locator('#fill-button')).to_be_disabled()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        store.db.close()


if __name__ == '__main__':
    main()
