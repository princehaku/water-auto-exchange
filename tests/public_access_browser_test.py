"""Public viewing and authenticated controls over isolated local HTTP."""
import os
import threading
import time

from aquarium_browser_test import (
    ROOT, StaticHandler, assert_canvas_rendered, assert_dialog_bounds, assert_one_screen,
    assert_logout_race, close_dialog, open_dialog, sync_status,
)
from playwright.sync_api import expect, sync_playwright
from server.app import Server, Store


def main():
    clock = [time.time()]
    store = Store(':memory:', lambda: clock[0])
    server = Server(('127.0.0.1', 0), store, 'public-view-local-admin', 'local-device-' * 4, '')
    server.RequestHandlerClass = StaticHandler
    server.origin = 'http://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    status = dict(project='water_auto_exchange', version='0.8.0', control_mode='manual',
                  state='IDLE', reason='ready', ready='1', fill='0', drain='0',
                  outputs_known='1', need_fill='unknown', overflow='0', cycle='0',
                  overflow_protection='0')
    gateway = 'a' * 32
    output = ROOT / 'build/browser/public-access'
    output.mkdir(parents=True, exist_ok=True)

    def report(**changes):
        clock[0] += .25
        status.update(changes)
        store.poll(gateway, status)

    def login(page):
        open_dialog(page, 'device')
        expect(page.locator('#login-panel')).to_be_visible()
        page.locator('#key').fill(server.admin_key)
        page.locator('#login-form button').click()
        expect(page.locator('#device-dialog')).to_be_hidden()
        expect(page.locator('#fill-button')).to_be_enabled()
        expect(page.locator('#message')).to_be_empty()

    def locked(page):
        for selector in ('#fill-button', '#drain-button', '#exchange-button', '#exchange-stop-button', '#reset',
                         '#save-calibration', '#start-level-job', '#cancel-level-job',
                         '#target-level', '#anchor-level', '#capacity-liters',
                         '#fill-seconds', '#drain-seconds'):
            expect(page.locator(selector)).to_be_disabled()
        for selector in ('#menu-device', '#menu-history', '#menu-calibration', '#menu-level-job'):
            expect(page.locator(selector)).to_be_enabled()

    report()
    store.configure_simulation(dict(level=50, fill_seconds=100, drain_seconds=200, capacity_liters=60))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('WATER_TEST_BROWSER', 'msedge'),
                headless=True, args=['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
            context = browser.new_context(viewport=dict(width=1440, height=900))
            page = context.new_page()
            errors, posts = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
            page.goto(server.origin + '/water/')
            expect(page.locator('#console')).to_be_visible()
            expect(page.locator('#header-connection')).to_have_text('设备正常')
            expect(page.locator('#level-value')).to_have_text('50%')
            expect(page.locator('#message')).to_be_empty()
            locked(page)
            assert_canvas_rendered(page)

            for width, height, name in ((1440, 900, 'desktop'), (390, 844, 'mobile'), (360, 640, 'small')):
                page.set_viewport_size(dict(width=width, height=height))
                assert_one_screen(page)
                page.screenshot(path=str(output / (name + '-guest.png')), full_page=True)
                for panel in ('device', 'history', 'calibration', 'level-job'):
                    open_dialog(page, panel)
                    assert_dialog_bounds(page, panel)
                    if panel == 'device':
                        expect(page.locator('#login-form')).to_be_visible()
                        page.screenshot(path=str(output / (name + '-device-login.png')))
                    close_dialog(page, panel)
            page.set_viewport_size(dict(width=1440, height=900))

            # Disabled UI and direct event dispatch must never submit a mutation.
            page.evaluate("""() => {
              for (const id of ['fill-button','drain-button','reset','exchange-button','exchange-stop-button','cancel-level-job'])
                document.getElementById(id).dispatchEvent(new MouseEvent('click'));
              for (const id of ['calibration-form','level-job-form'])
                document.getElementById(id).dispatchEvent(new Event('submit', {cancelable:true}));
            }""")
            page.wait_for_timeout(100)
            assert posts == [], posts
            assert store.snapshot()['commands'] == []

            # Polling and the real output indication remain live for anonymous viewers.
            report(state='FILLING', fill='1', reason='manual_filling')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true', timeout=6000)
            expect(page.locator('#fill-state')).to_have_text('开启')
            locked(page)
            report(state='IDLE', fill='0', reason='stopped')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false', timeout=6000)
            report(state='FAULT', reason='drain_timeout')
            expect(page.locator('#device-state')).to_have_text('故障锁定', timeout=6000)
            expect(page.locator('#reset')).to_be_disabled()
            report(state='IDLE', reason='ready')
            expect(page.locator('#header-connection')).to_have_text('设备正常', timeout=6000)

            page.route('**/api/status', lambda route: route.abort())
            expect(page.locator('#header-connection')).to_have_text('状态未知', timeout=6000)
            expect(page.locator('#console')).to_be_visible()
            page.unroute('**/api/status')
            expect(page.locator('#header-connection')).to_have_text('设备正常', timeout=6000)
            expect(page.locator('#message')).to_be_empty()
            locked(page)

            # A delayed anonymous response cannot relock a newly authenticated page.
            pending = []
            page.route('**/api/status', lambda route: pending.append(route))
            with page.expect_request('**/api/status'):
                page.evaluate("window.dispatchEvent(new Event('online'))")
            page.wait_for_timeout(100)
            stale = pending[0].fetch()
            open_dialog(page, 'device')
            page.locator('#key').fill(server.admin_key)
            page.locator('#login-form button').click()
            expect(page.locator('#device-dialog')).to_be_hidden()
            pending[0].fulfill(response=stale)
            page.wait_for_timeout(100)
            for route in pending[1:]:
                route.continue_()
            page.unroute('**/api/status')
            expect(page.locator('#fill-button')).to_be_enabled()
            expect(page.locator('#drain-button')).to_be_enabled()
            expect(page.locator('#save-calibration')).to_be_enabled()
            expect(page.locator('#message')).to_be_empty()

            # Both bookmarks restore the session without ever leaving the dashboard.
            for path in ('index.html', 'aquarium.html'):
                page.goto(server.origin + '/water/' + path)
                expect(page.locator('#fill-button')).to_be_enabled()
                expect(page.locator('#console')).to_be_visible()
            report(state='FAULT', reason='drain_timeout')
            expect(page.locator('#reset')).to_be_enabled(timeout=6000)
            report(state='IDLE', reason='ready')
            expect(page.locator('#fill-button')).to_be_enabled(timeout=6000)

            # Server expiry locks controls quietly while polling and menus continue.
            with store.lock:
                store.db.execute('DELETE FROM admin_sessions')
                store.db.commit()
            expect(page.locator('#fill-button')).to_be_disabled(timeout=6000)
            locked(page)
            expect(page.locator('#console')).to_be_visible()
            expect(page.locator('#message')).to_be_empty()
            login(page)
            assert_logout_race(page)
            locked(page)
            report(state='DRAINING', drain='1', reason='manual_draining')
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true', timeout=6000)
            expect(page.locator('#console')).to_be_visible()
            assert all(url.endswith('/login') or url.endswith('/logout') for url in posts), posts
            assert store.snapshot()['commands'] == []
            assert not errors, errors
            browser.close()
            print('PASS public dashboard: live status, desktop/phone panels, no anonymous actions, '
                  'device login, session restore/expiry, API recovery, login/logout response races')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        store.db.close()


if __name__ == '__main__':
    main()
