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
    status = dict(project='water_auto_exchange', version='0.6.0', state='IDLE', reason='ready',
                  ready='1', fill='0', drain='0', outputs_known='1', need_fill='0',
                  overflow='0', cycle='0', overflow_protection='0')
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
            page.screenshot(path=str(output / 'login-desktop.png'), full_page=True)
            page.locator('#key').fill('bad')
            page.get_by_role('button', name='登录', exact=True).click()
            expect(page.locator('#message')).to_contain_text('管理密钥不正确')
            page.locator('#key').fill(server.admin_key)
            page.get_by_role('button', name='登录', exact=True).click()
            expect(page.locator('#console')).to_be_visible()
            expect(page.locator('#start')).to_be_disabled()
            expect(page.locator('#stop')).to_be_disabled()
            expect(page.locator('#drain-button')).to_be_disabled()
            page.screenshot(path=str(output / 'offline-desktop.png'), full_page=True)
            store.poll(device, status)
            page.locator('#refresh').click()
            expect(page.locator('#start')).to_be_enabled()
            expect(page.locator('#fill-button')).to_be_disabled()
            expect(page.locator('#drain-button')).to_be_enabled()
            expect(page.locator('#drain-button span')).to_have_text('冲水')
            expect(page.locator('#fill-button span')).to_have_text('补水')
            page.locator('#drain-button').click()
            expect(page.locator('#confirm-body')).to_contain_text('不会自动补水')
            page.get_by_role('button', name='取消', exact=True).click()
            assert store.snapshot()['commands'] == []
            page.locator('#drain-button').click()
            page.get_by_role('button', name='确认执行', exact=True).click()
            expect(page.locator('#history')).to_contain_text('等待设备领取')
            item = store.poll(device, status)['command']
            assert item['command'] == 'DRAIN'
            status.update(state='DRAINING', reason='flushing', drain='1')
            store.poll(device, status, dict(id=item['id'], status='succeeded', result='OK DRAIN drain_started'))
            page.locator('#refresh').click()
            expect(page.locator('#drain-button')).to_be_disabled()
            expect(page.locator('#fill-button')).to_be_disabled()
            expect(page.locator('#history')).to_contain_text('冲水')
            page.locator('#stop').click()
            expect(page.locator('#history')).to_contain_text('等待设备领取')
            item = store.poll(device, status)['command']
            assert item['command'] == 'STOP'
            status.update(state='IDLE', reason='stopped', drain='0', need_fill='1')
            store.poll(device, status, dict(id=item['id'], status='succeeded', result='OK STOP stopped'))
            page.locator('#refresh').click()
            expect(page.locator('#drain-button')).to_be_disabled()
            page.locator('#fill-button').click()
            page.get_by_role('button', name='确认执行', exact=True).click()
            expect(page.locator('#history')).to_contain_text('等待设备领取')
            item = store.poll(device, status)['command']
            assert item['command'] == 'FILL'
            status.update(state='DONE', reason='completed', need_fill='0')
            store.poll(device, status, dict(id=item['id'], status='succeeded', result='OK FILL fill_started'))
            page.locator('#refresh').click()
            expect(page.locator('#drain-button')).to_be_enabled()
            page.screenshot(path=str(output / 'independent-buttons.png'), full_page=True)
            status['version'] = '0.5.3'
            store.poll(device, status); page.locator('#refresh').click()
            expect(page.locator('#drain-button')).to_be_disabled()
            expect(page.locator('#drain-hint')).to_contain_text('更新设备')
            status['version'] = '0.6.0'
            store.poll(device, status); page.locator('#refresh').click()
            page.locator('.exchange-option summary').click()
            before = len(store.snapshot()['commands'])
            page.locator('#start').click()
            page.get_by_role('button', name='取消', exact=True).click()
            assert len(store.snapshot()['commands']) == before
            page.locator('#start').click()
            page.get_by_role('button', name='确认执行', exact=True).click()
            expect(page.locator('#history')).to_contain_text('等待设备领取')
            item = store.poll(device, status)['command']
            assert item['command'] == 'START'
            status.update(state='DRAINING', reason='draining', drain='1', cycle='1')
            store.poll(device, status, dict(id=item['id'], status='succeeded', result='OK START started'))
            page.locator('#refresh').click()
            expect(page.locator('#state')).to_have_text('正在排水')
            expect(page.locator('#history')).to_contain_text('设备已确认')
            expect(page.locator('#start')).to_be_disabled()
            page.screenshot(path=str(output / 'active-desktop.png'), full_page=True)
            page.locator('#stop').click()
            expect(page.locator('#history')).to_contain_text('等待设备领取')
            item = store.poll(device, status)['command']
            assert item['command'] == 'STOP'
            status.update(state='IDLE', reason='stopped', drain='0', need_fill='1')
            store.poll(device, status, dict(id=item['id'], status='succeeded', result='OK STOP stopped'))
            page.locator('#refresh').click()
            expect(page.locator('#fill-button')).to_be_enabled()
            expect(page.locator('#start')).to_be_disabled()
            status.update(state='FAULT', reason='<img src=x onerror=alert(1)>', ready='0')
            store.poll(device, status)
            page.locator('#refresh').click()
            expect(page.locator('#reset')).to_be_enabled()
            expect(page.locator('#reason')).to_contain_text('<img')
            assert page.locator('#reason img').count() == 0
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            now[0] += 11
            page.locator('#refresh').click()
            expect(page.locator('#stop')).to_be_disabled()
            expect(page.locator('#connection')).to_contain_text('离线')
            page.locator('#logout').click()
            expect(page.locator('#login-panel')).to_be_visible()
            assert not errors, errors
            browser.close()
            print('PASS browser: independent FILL/DRAIN, legacy guard, busy guard, cancel/ack, START, STOP, login/offline, fault/XSS, mobile, expiry, logout')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        store.db.close()


if __name__ == '__main__':
    main()
