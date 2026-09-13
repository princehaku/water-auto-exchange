"""One-click exchange over local HTTP and real 0.8.2 WebSocket receipts."""
import json
import math
import threading
import time

from aquarium_browser_test import (
    ROOT, StaticHandler, assert_one_screen, assert_dialog_bounds, close_dialog,
    open_dialog, post_from_page, sync_status,
)
from playwright.sync_api import expect, sync_playwright
import websocket
from server.app import Server, Store


def main():
    clock = [time.time()]
    store = Store(':memory:', lambda: clock[0])
    server = Server(('127.0.0.1', 0), store, 'exchange-local-admin-' * 3,
                    'exchange-local-device-' * 3, '')
    server.RequestHandlerClass = StaticHandler
    server.origin = 'http://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    status = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
                  state='IDLE', reason='ready', ready='1', fill='0', drain='0',
                  outputs_known='1', need_fill='unknown', overflow='0', cycle='0',
                  overflow_protection='0')
    client = websocket.create_connection(server.origin.replace('http:', 'ws:') +
                                         '/water/api/device/ws', timeout=3)
    inbox, trace, errors, heartbeat_errors = [], [], [], []
    wire = threading.RLock()
    stopped = threading.Event()
    sequence = 0
    output = ROOT / 'build/browser/exchange'
    output.mkdir(parents=True, exist_ok=True)

    def receive(kind):
        for i, frame in enumerate(inbox):
            if frame['type'] == kind:
                return inbox.pop(i)
        for _ in range(30):
            raw = client.recv()
            assert raw, ('unexpected local WS disconnect', store.snapshot())
            frame = json.loads(raw)
            if frame['type'] == kind:
                return frame
            inbox.append(frame)
        raise AssertionError((kind, inbox))

    client.send(json.dumps(dict(type='auth', key=server.device_key, status=status)))
    assert receive('ready')['soft_limits']['watchdog_ms'] == 5000

    def ping(seconds=0):
        nonlocal sequence
        with wire:
            clock[0] += seconds
            sequence += 1
            client.send(json.dumps(dict(type='ping', seq=sequence)))
            assert receive('pong') == dict(type='pong', seq=sequence)

    def heartbeat():
        while not stopped.wait(.4):
            try:
                ping()
            except Exception as error:
                heartbeat_errors.append(str(error))
                return

    heart = threading.Thread(target=heartbeat, daemon=True)
    heart.start()

    def advance(seconds):
        while seconds > 0:
            step = min(1, seconds)
            ping(step)
            seconds -= step
        assert not heartbeat_errors, heartbeat_errors

    def claim(command):
        with wire:
            offer = receive('offer')
            assert offer['command'] == command, (command, offer, trace)
            client.send(json.dumps(dict(type='claim', id=offer['id'])))
            execute = receive('execute')
            assert (execute['id'], execute['command']) == (offer['id'], command)
            return offer

    def acknowledge(offer):
        with wire:
            command = offer['command']
            if command in ('DRAIN', 'FILL'):
                status[command.lower()] = '1'
            elif command in ('DRAIN_OFF', 'FILL_OFF'):
                status[command[:-4].lower()] = '0'
            elif command == 'STOP':
                status.update(fill='0', drain='0')
            else:
                raise AssertionError(command)
            assert not (status['fill'] == status['drain'] == '1'), status
            status.update(state='FILLING' if status['fill'] == '1' else
                          'DRAINING' if status['drain'] == '1' else 'IDLE', reason='ready')
            client.send(json.dumps(dict(type='ack', status=status, ack=dict(
                id=offer['id'], status='succeeded', result='OK ' + command))))
            receive('received')
            trace.append((command, clock[0], status['fill'], status['drain']))

    def receipt(command):
        acknowledge(claim(command))

    def job():
        return store.snapshot()['level_job']

    def calibrate(page, level):
        result = post_from_page(page, 'simulation', dict(
            level=level, fill_seconds=200, drain_seconds=400, capacity_liters=60))
        assert result['status'] == 200, result
        sync_status(page)

    def click_exchange(page, cancel=False):
        with page.expect_response('**/api/level-job/cancel' if cancel else '**/api/level-job') as response:
            page.locator('#exchange-button').click()
        assert response.value.ok, response.value.text()
        sync_status(page)
        return response.value.json()

    def layouts(page, stage):
        for width, height, name in ((1440, 900, 'desktop'), (390, 844, 'mobile'),
                                    (360, 640, 'small')):
            page.set_viewport_size(dict(width=width, height=height))
            assert_one_screen(page)
            exchange = page.locator('#exchange-button').bounding_box()
            target = page.locator('#menu-level-job').bounding_box()
            manual = page.locator('#fill-button').bounding_box()
            assert exchange and target and manual
            assert exchange['x'] + exchange['width'] <= target['x'] + 1
            assert abs(exchange['y'] - target['y']) <= 1
            if width <= 700:
                assert exchange['y'] + exchange['height'] <= manual['y'] + 1
            page.screenshot(path=str(output / (stage + '-' + name + '.png')), full_page=True)
        page.set_viewport_size(dict(width=1440, height=900))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True,
                args=['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
            context = browser.new_context(viewport=dict(width=1440, height=900))
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.origin + '/water/')
            page.locator('#key').fill(server.admin_key)
            page.locator('#login-form button[type="submit"]').click()
            expect(page.locator('#console')).to_be_visible()
            expect(page.locator('#exchange-button')).to_be_disabled()
            calibrate(page, 80)
            expect(page.locator('#exchange-button')).to_be_enabled()
            layouts(page, 'idle')

            first = click_exchange(page)
            assert first['mode'] == 'exchange' and first['direction'] == 'drain'
            assert math.isclose(first['total_seconds'], 520)
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false')
            receipt('DRAIN')
            sync_status(page)
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true')
            expect(page.locator('#fill-button')).to_be_disabled()
            expect(page.locator('#menu-level-job')).to_be_disabled()
            expect(page.locator('#exchange-button')).to_contain_text('停止换水')
            layouts(page, 'draining')
            for width, height in ((1440, 900), (390, 844), (360, 640)):
                page.set_viewport_size(dict(width=width, height=height))
                page.locator('#job-summary').click()
                assert_dialog_bounds(page, 'level-job')
                expect(page.locator('#cancel-level-job')).to_be_enabled()
                close_dialog(page, 'level-job')
            advance(290)
            receipt('DRAIN_OFF')
            sync_status(page)
            assert job()['phase'] == 'waiting' and job()['stage'] == 'drain'
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false')
            open_dialog(page, 'calibration')
            expect(page.locator('#save-calibration')).to_be_disabled()
            close_dialog(page, 'calibration')
            advance(2)
            receipt('DRAIN')
            # Close the browser page: server alone must finish draining and start filling.
            page.close()
            advance(30)
            off = claim('DRAIN_OFF')
            assert job()['phase'] == 'stopping' and job()['stage'] == 'drain'
            advance(1)
            assert not any(frame.get('command') == 'FILL' for frame in inbox), inbox
            acknowledge(off)
            assert job()['phase'] == 'waiting', job()
            advance(1.9)
            assert job()['phase'] == 'waiting', job()
            advance(.1)
            receipt('FILL')
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.origin + '/water/')
            expect(page.locator('#console')).to_be_visible()
            sync_status(page)
            assert job()['stage'] == 'fill' and job()['id'] == first['id']
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true')
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false')
            layouts(page, 'filling')
            advance(170)
            receipt('FILL_OFF')
            advance(2)
            receipt('FILL')
            advance(30)
            receipt('FILL_OFF')
            sync_status(page)
            assert job()['status'] == 'completed', job()
            assert math.isclose(store.snapshot()['simulation']['level'], 100, abs_tol=.0001)
            expect(page.locator('#exchange-button')).to_be_enabled()
            expect(page.locator('#exchange-button')).to_contain_text('一键换水')
            expect(page.locator('#command-feedback')).to_contain_text('一键换水已完成')
            layouts(page, 'completed')
            assert [item[0] for item in trace] == [
                'DRAIN', 'DRAIN_OFF', 'DRAIN', 'DRAIN_OFF',
                'FILL', 'FILL_OFF', 'FILL', 'FILL_OFF'], trace

            # The transition pause remains cancellable; it must never queue a fill afterwards.
            calibrate(page, 1)
            second = click_exchange(page)
            receipt('DRAIN')
            advance(4)
            receipt('DRAIN_OFF')
            sync_status(page)
            assert job()['phase'] == 'waiting'
            click_exchange(page, cancel=True)
            receipt('STOP')
            advance(3)
            sync_status(page)
            assert job()['status'] == 'cancelled' and job()['id'] == second['id']
            expect(page.locator('#command-feedback')).to_contain_text('一键换水已停止')
            assert not any(frame.get('command') == 'FILL' for frame in inbox), inbox

            # An empty calibrated tank starts filling directly and can be stopped while active.
            calibrate(page, 0)
            third = click_exchange(page)
            assert third['direction'] == 'fill' and third['total_seconds'] == 200
            receipt('FILL')
            sync_status(page)
            click_exchange(page, cancel=True)
            receipt('STOP')
            advance(3)
            sync_status(page)
            assert job()['status'] == 'cancelled'
            assert not errors, errors
            browser.close()
    finally:
        stopped.set()
        heart.join(timeout=5)
        client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        store.db.close()
    assert not heartbeat_errors, heartbeat_errors
    print('PASS one-click exchange: real local HTTP/WS 0.8.2, 80 -> 0 -> 100%, '
          '290+30 drain / 170+30 fill, no fill before confirmed drain OFF and 2s pause, '
          'closed-page continuation, stage and valve UI, transition and fill cancellation, '
          '0% direct fill, 12 desktop/mobile screenshots, no page errors or physical IO')


if __name__ == '__main__':
    main()
