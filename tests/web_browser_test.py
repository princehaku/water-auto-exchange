"""Real HTTP API + Edge browser integration, using simulated telemetry only."""
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.app import Handler, Server, Store
from playwright.sync_api import sync_playwright, expect


class WebHandler(Handler):
    def do_GET(self):
        files = {'/water/': 'index.html', '/water/app.js': 'app.js', '/water/style.css': 'style.css'}
        if self.path not in files:
            return self.dispatch()
        name = files[self.path]
        data = (ROOT / 'deploy/www' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name])
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    now = [1000]
    store = Store(':memory:', lambda: now[0])
    server = Server(('127.0.0.1', 0), store, 'test-admin-key-' * 3, 'test-device-key-' * 3, '')
    server.RequestHandlerClass = WebHandler
    server.origin = 'http://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    status = dict(project='water_auto_exchange', version='0.7.1', control_mode='manual',
                  state='IDLE', reason='ready', ready='1', fill='0', drain='0',
                  outputs_known='1', need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')
    device = 'a' * 32
    output = ROOT / 'build/browser'
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.origin + '/water/')
            page.locator('#key').fill('bad')
            page.get_by_role('button', name='登录', exact=True).click()
            expect(page.locator('#message')).to_contain_text('管理密钥不正确')
            page.locator('#key').fill(server.admin_key)
            page.get_by_role('button', name='登录', exact=True).click()
            expect(page.locator('#console')).to_be_visible()
            fill = page.get_by_role('switch', name='补水', exact=True)
            drain = page.get_by_role('switch', name='冲水', exact=True)
            for switch in (fill, drain):
                expect(switch).to_be_disabled()
            expect(page.locator('#stop')).to_be_disabled()
            assert page.locator('#start').count() == 0
            def report(**changes):
                status.update(changes)
                store.poll(device, status)
                page.locator('#refresh').click()
            # Both manual releases remain supported after backend deployment.
            report(version='0.7.0')
            page.wait_for_function("!document.getElementById('fill-button').disabled")
            report(version='0.7.1')
            # Do not send manual intentions to legacy automatic firmware.
            report(version='0.6.0')
            expect(fill).to_be_disabled(); expect(drain).to_be_disabled()
            report(version='0.7.1', control_mode='automatic')
            expect(fill).to_be_disabled(); expect(drain).to_be_disabled()
            report(control_mode='manual')
            expect(fill).to_be_enabled(); expect(drain).to_be_enabled()
            expect(page.locator('#need-fill')).to_have_text('手动开关')
            def delivered(command):
                expect(page.locator('#history')).to_contain_text('等待设备领取')
                item = store.poll(device, status)['command']
                assert item['command'] == command
                return item
            def ack(item, accepted=True, **changes):
                status.update(changes)
                result = ('OK ' if accepted else 'ERROR ') + item['command'] + ' test_response'
                store.poll(device, status, dict(id=item['id'], status='succeeded' if accepted else 'rejected', result=result))
                page.locator('#refresh').click()
                expect(page.locator('#history')).not_to_contain_text('等待设备领取')
            fill.click()
            assert not page.locator('#confirm').is_visible()
            item = delivered('FILL')
            expect(fill).to_have_attribute('aria-checked', 'false')
            expect(fill).to_be_disabled(); expect(drain).to_be_disabled()
            expect(page.locator('#stop')).to_be_enabled()
            ack(item, accepted=False)
            expect(fill).to_have_attribute('aria-checked', 'false')
            expect(fill).to_be_enabled()
            fill.click(); item = delivered('FILL')
            ack(item, state='FILLING', reason='manual_filling', fill='1', cycle='1')
            expect(fill).to_have_attribute('aria-checked', 'true')
            expect(fill).to_be_enabled(); expect(drain).to_be_disabled()
            page.screenshot(path=str(output / 'manual-switches-desktop.png'), full_page=True)
            # Same switch sends STOP; checked state remains until device confirms OFF.
            fill.click(); item = delivered('STOP')
            expect(fill).to_have_attribute('aria-checked', 'true')
            ack(item, state='IDLE', reason='stopped', fill='0')
            expect(fill).to_have_attribute('aria-checked', 'false')
            expect(drain).to_be_enabled()
            drain.click(); item = delivered('DRAIN')
            ack(item, state='DRAINING', reason='manual_draining', drain='1', cycle='2')
            expect(drain).to_have_attribute('aria-checked', 'true')
            expect(fill).to_be_disabled()
            drain.click(); item = delivered('STOP')
            ack(item, state='IDLE', reason='stopped', drain='0')
            expect(drain).to_have_attribute('aria-checked', 'false')
            # Fault/uncertain output must not look like a confirmed successful close.
            report(state='FAULT', reason='<img src=x onerror=alert(1)>', ready='0', outputs_known='0')
            expect(fill).to_be_disabled(); expect(drain).to_be_disabled()
            expect(page.locator('#fill-hint')).to_have_text('状态未知')
            expect(page.locator('#stop')).to_be_enabled()
            expect(page.locator('#reset')).to_be_enabled()
            assert page.locator('#reason img').count() == 0
            report(state='IDLE', reason='ready', ready='1', outputs_known='1')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output / 'manual-switches-mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            now[0] += 11
            page.locator('#refresh').click()
            expect(fill).to_be_disabled(); expect(drain).to_be_disabled()
            expect(page.locator('#stop')).to_be_disabled()
            expect(page.locator('#connection')).to_contain_text('离线')
            page.locator('#logout').click()
            expect(page.locator('#login-panel')).to_be_visible()
            assert not errors, errors
            browser.close()
            print('PASS browser: manual ON/OFF without sensors, receipt-driven switches, rejected command, interlock, STOP, legacy/mode guards, fault/unknown, mobile, offline, login/logout')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        store.db.close()


if __name__ == '__main__':
    main()
