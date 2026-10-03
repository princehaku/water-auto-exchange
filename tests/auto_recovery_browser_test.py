"""Recovery UI contract and real Edge layout checks; no device or physical IO.

Serve the actual local page and Three.js scene. HTTP status fixtures exercise the
public recovery contract; backend recovery/receipt behavior has separate tests.
"""
import copy
import json
import os
import threading
import time

from aquarium_browser_test import (
    ROOT, StaticHandler, assert_one_screen, assert_dialog_bounds, close_dialog,
    open_dialog, sync_status,
)
from playwright.sync_api import expect, sync_playwright
from server.app import Server, Store


def main():
    store = Store(':memory:')
    server = Server(('127.0.0.1', 0), store, 'recovery-ui-admin-' * 3,
                    'recovery-ui-device-' * 3, '')
    server.RequestHandlerClass = StaticHandler
    server.origin = 'http://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = store.snapshot()
    base.update(authenticated=True, online=True, last_seen=time.time())
    base['device'] = dict(project='water_auto_exchange', version='0.8.2',
                          control_mode='manual', state='IDLE', reason='ready',
                          ready='1', fill='0', drain='0', outputs_known='1',
                          overflow='0', need_fill='unknown', cycle='0')
    base['control_limits'].update(source='web', uncertain=False, timeout_pending=None)
    base['simulation'].update(calibrated=True, uncertain=False, level=60,
                              fill_seconds=200, drain_seconds=400, capacity_liters=60,
                              calibrated_at=time.time(), volume_liters=36,
                              fill_rate_lpm=18, drain_rate_lpm=9)
    snapshot = copy.deepcopy(base)
    requests, errors = [], []
    output = ROOT / 'build/browser/auto-recovery'
    output.mkdir(parents=True, exist_ok=True)

    def task(mode='exchange'):
        return dict(id='a' * 32, status='running', phase='active', mode=mode,
                    direction='drain' if mode == 'exchange' else 'fill', stage='drain',
                    start_level=60, target_level=100 if mode == 'exchange' else 80,
                    total_seconds=440 if mode == 'exchange' else 120,
                    elapsed_seconds=90 if mode == 'exchange' else 80,
                    remaining_seconds=350 if mode == 'exchange' else 40,
                    progress=90 / 440 * 100 if mode == 'exchange' else 80 / 120 * 100,
                    fill_seconds=200, drain_seconds=400, estimated_rounds=8,
                    round=2, round_limit_seconds=60, round_remaining_seconds=30,
                    estimated_liters=13.5, reason=None, created_at=time.time(),
                    recovery=dict(enabled=True, version=1, attempts=0, uncertain=False,
                                  last_reason=None, deadline=None,
                                  level_min=37.5, level_max=37.5,
                                  possible_extra_seconds=0,
                                  used_min=dict(fill=0, drain=90),
                                  used_max=dict(fill=0, drain=90),
                                  budgets=dict(fill=200, drain=240)))

    def status_route(route):
        snapshot['server_time'] = time.time()
        route.fulfill(status=200, content_type='application/json', body=json.dumps(snapshot))

    def mutation_route(route):
        request = route.request
        body = request.post_data_json
        requests.append((request.url, body))
        assert request.url.endswith('/api/level-job/cancel'), (request.url, body)
        assert body == {'job_id': snapshot['level_job']['id'], 'intent':'manual_stop',
                        'source':'web_ui', 'event':'click',
                        'button':'exchange-stop-button' if snapshot['level_job']['mode']=='exchange'
                        else 'cancel-level-job'}, body
        snapshot['level_job'].update(status='cancelled', phase='done', reason='cancelled_by_user',
                                     stop_origin='manual', stop_action='manual_stop')
        route.fulfill(status=200, content_type='application/json', body=json.dumps(snapshot['level_job']))

    def scene_route(route):
        # Observe calls into the real scene without replacing its rendering.
        source = (ROOT / 'deploy/www/aquarium-scene.js').read_text(encoding='utf-8')
        marker = 'function setFlow(value={}) {'
        assert source.count(marker) == 1
        route.fulfill(status=200, content_type='text/javascript', body=source.replace(
            marker, marker + 'window.__recoveryTestFlow={...value};', 1))

    def flow(page, fill=False, drain=False):
        page.wait_for_function('expected => window.__recoveryTestFlow?.fill === expected.fill && '
                               'window.__recoveryTestFlow?.drain === expected.drain',
                               arg=dict(fill=fill, drain=drain))

    def block_controls(page):
        for selector in ('#fill-button', '#drain-button', '#start-level-job', '#save-calibration'):
            expect(page.locator(selector)).to_be_disabled()
        expect(page.locator('#cancel-level-job')).to_be_enabled()
        expect(page.locator('#target-level')).to_be_disabled()

    def layouts(page, label):
        for width, height, name in ((1440, 900, 'desktop'), (390, 844, 'mobile'), (360, 640, 'small')):
            page.set_viewport_size(dict(width=width, height=height))
            assert_one_screen(page)
            page.screenshot(path=str(output / (label + '-' + name + '.png')), full_page=True)
            open_dialog(page, 'level-job')
            assert_dialog_bounds(page, 'level-job')
            close_dialog(page, 'level-job')
        page.set_viewport_size(dict(width=1440, height=900))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get('WATER_TEST_BROWSER', 'msedge'), headless=True,
                args=['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
            context = browser.new_context(viewport=dict(width=1440, height=900))
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/status', status_route)
            page.route('**/api/**', lambda route: status_route(route) if
                       route.request.url.endswith('/api/status') else mutation_route(route))
            page.route('**/aquarium-scene.js', scene_route)
            page.goto(server.origin + '/water/')
            expect(page.locator('#exchange-button')).to_be_enabled()

            # Updated confirmation keeps the action visible on the shortest phone.
            for width, height in ((1440, 900), (390, 844), (360, 640)):
                page.set_viewport_size(dict(width=width, height=height))
                page.locator('#exchange-button').click()
                expect(page.locator('#exchange-confirm-note')).to_contain_text('通信异常会暂停')
                expect(page.locator('#exchange-confirm-note')).to_contain_text('永久取消')
                box = page.locator('#confirm-exchange-start').bounding_box()
                assert box and box['y'] + box['height'] <= height, (width, height, box)
                page.locator('[data-close="exchange-confirm"]').last.click()
            assert not requests
            page.set_viewport_size(dict(width=1440, height=900))

            snapshot['level_job'] = task()
            snapshot['device'].update(state='DRAINING', drain='1')
            sync_status(page)
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false')
            flow(page, drain=True)
            before = page.locator('#job-progress').evaluate('el => String(el.value)')

            # Offline pause keeps the task cancellable, preserves progress, and
            # stops live flow despite the retained historical ON report.
            snapshot['online'] = False
            snapshot['simulation']['uncertain'] = True
            snapshot['level_job']['phase'] = 'paused'
            snapshot['level_job']['recovery'].update(attempts=1, uncertain=True,
                                                     last_reason='device_disconnected')
            sync_status(page)
            expect(page.locator('#job-summary-state')).to_have_text('等待设备恢复')
            expect(page.locator('#scene-status')).to_have_text('等待设备恢复')
            expect(page.locator('#exchange-stop-button')).to_be_enabled()
            expect(page.locator('#drain-detail')).to_contain_text('上次开启')
            block_controls(page)
            flow(page)
            assert page.locator('#job-progress').evaluate('el => String(el.value)') == before
            countdown = page.locator('#drain-countdown').inner_text()
            page.wait_for_timeout(2200)
            assert page.locator('#drain-countdown').inner_text() == countdown
            layouts(page, 'paused')
            open_dialog(page, 'calibration')
            expect(page.locator('#fill-seconds')).to_be_disabled()
            expect(page.locator('#anchor-level')).to_be_disabled()
            expect(page.locator('#calibration-message')).to_contain_text('等待恢复')
            close_dialog(page, 'calibration')

            # Reconnected IDLE is checked before restarting; a communication
            # FAULT gets its own visible RESET stage and cannot be manually reset.
            snapshot['online'] = True
            snapshot['device'].update(state='IDLE', fill='0', drain='0')
            snapshot['level_job']['phase'] = 'recovering'
            sync_status(page)
            expect(page.locator('#job-summary-state')).to_have_text('确认恢复状态')
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false')
            flow(page)
            block_controls(page)
            snapshot['device'].update(state='FAULT', reason='communication_timeout')
            snapshot['level_job']['phase'] = 'resetting'
            sync_status(page)
            expect(page.locator('#job-summary-state')).to_have_text('恢复通信故障')
            expect(page.locator('#reset')).to_be_disabled()
            expect(page.locator('#job-reason')).to_contain_text('仅自动复位本次通信超时故障')
            flow(page)
            block_controls(page)
            layouts(page, 'resetting')
            assert page.locator('#job-progress').evaluate('el => String(el.value)') == before
            assert page.locator('#drain-countdown').inner_text() == countdown
            assert not requests, 'The browser must not send automatic RESET or ON'

            # Resume uses the existing progress and actual ON receipt. Even in
            # the fill stage, uncertain water is never promoted to trusted 100%.
            snapshot['level_job'].update(phase='active', elapsed_seconds=95,
                                         remaining_seconds=345, progress=95 / 440 * 100)
            snapshot['device'].update(state='DRAINING', reason='ready', drain='1')
            sync_status(page)
            flow(page, drain=True)
            assert float(page.locator('#job-progress').evaluate('el => String(el.value)')) > float(before)
            expect(page.locator('#job-summary-stage')).to_contain_text('保守续行')
            expect(page.locator('#job-remaining-label')).to_have_text('保守可继续时长')
            expect(page.locator('#level-detail')).to_contain_text('估算不确定')
            snapshot['level_job'].update(stage='fill', direction='fill', elapsed_seconds=340,
                                         remaining_seconds=100, progress=340 / 440 * 100)
            snapshot['device'].update(state='FILLING', drain='0', fill='1')
            sync_status(page)
            flow(page, fill=True)
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true')
            layouts(page, 'resumed-fill')
            snapshot['level_job'].update(status='completed', phase='done',
                                         reason='completed_with_uncertainty',
                                         elapsed_seconds=440, remaining_seconds=0, progress=100)
            snapshot['simulation'].update(level=94, volume_liters=56.4)
            snapshot['device'].update(state='IDLE', fill='0', drain='0')
            sync_status(page)
            flow(page)
            expect(page.locator('#command-feedback')).to_contain_text('已完成保守续行')
            expect(page.locator('#command-feedback')).to_contain_text('重新校准')
            expect(page.locator('#job-current-level')).to_have_text('94% · 需校准')
            expect(page.locator('#job-reason')).not_to_contain_text('完成冲水至 0% 和补水至 100%')
            expect(page.locator('#exchange-button')).to_be_disabled()
            layouts(page, 'completed-uncertain')

            # Both automatic entries can be permanently cancelled while offline.
            for mode in ('exchange', 'target'):
                snapshot['level_job'] = task(mode)
                snapshot['level_job']['phase'] = 'paused'
                snapshot['level_job']['recovery'].update(attempts=1, uncertain=True)
                snapshot['online'] = False
                sync_status(page)
                block_controls(page)
                if mode == 'exchange':
                    with page.expect_response('**/api/level-job/cancel'):
                        page.locator('#exchange-stop-button').click()
                else:
                    open_dialog(page, 'level-job')
                    with page.expect_response('**/api/level-job/cancel'):
                        page.locator('#cancel-level-job').click()
                    close_dialog(page, 'level-job')
                sync_status(page)
                assert snapshot['level_job']['status'] == 'cancelled'
                expect(page.locator('#job-summary')).to_be_hidden()
                expect(page.locator('#job-reason')).to_have_text('人工停止已提交，已取消本次任务与自动恢复。')
            assert len(requests) == 2, requests
            assert all(url.endswith('/api/level-job/cancel') for url, _ in requests)

            # Genuine faults still require manual investigation; polling cannot
            # generate RESET, restart the task, or conceal the FAULT report.
            snapshot['online'] = True
            snapshot['device'].update(state='FAULT', reason='overflow', overflow='1')
            snapshot['level_job'].update(status='failed', reason='device_fault', phase='done')
            sync_status(page)
            expect(page.locator('#job-reason')).to_contain_text('请检查现场')
            expect(page.locator('#exchange-button')).to_be_disabled()
            expect(page.locator('#reset')).to_be_enabled()
            page.wait_for_timeout(2200)
            assert len(requests) == 2
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    print('PASS automatic recovery UI: paused/recovering/resetting, frozen progress, '
          'actual valve/3D states, offline permanent cancel for both automatic entries, '
          'recovery control/calibration locks, conservative completion and uncertainty, '
          'no browser RESET/ON, genuine FAULT handling, 12 one-screen desktop/phone '
          'states and all three confirmation layouts')


if __name__ == '__main__':
    main()
