"""Real Edge checks for explicit STOP intent, job binding, and source labels.

Use the actual page with public-contract HTTP fixtures. Backend origin storage and
STOP continuation semantics are tested separately against the real local server.
"""
import copy
import json
import os
import threading
import time

from aquarium_browser_test import (ROOT, StaticHandler, assert_one_screen,
                                  assert_dialog_bounds, open_dialog, close_dialog,
                                  sync_status)
from playwright.sync_api import expect, sync_playwright
from server.app import Server, Store


def main():
    store = Store(':memory:')
    server = Server(('127.0.0.1', 0), store, 'stop-origin-ui-admin-' * 3,
                    'stop-origin-ui-device-' * 3, '')
    server.RequestHandlerClass = StaticHandler
    server.origin = 'http://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    snapshot = store.snapshot()
    snapshot.update(authenticated=True, online=True, last_seen=time.time())
    snapshot['device'] = dict(project='water_auto_exchange', version='0.8.2',
                             control_mode='manual', state='IDLE', reason='ready',
                             ready='1', fill='0', drain='0', outputs_known='1',
                             overflow='0', need_fill='unknown', cycle='0')
    snapshot['control_limits'].update(source='web', uncertain=False, timeout_pending=None)
    snapshot['simulation'].update(calibrated=True, uncertain=False, level=60,
                                  fill_seconds=200, drain_seconds=400, capacity_liters=60,
                                  calibrated_at=time.time(), volume_liters=36,
                                  fill_rate_lpm=18, drain_rate_lpm=9)
    posts, errors = [], []
    output = ROOT / 'build/browser/stop-origin'
    output.mkdir(parents=True, exist_ok=True)

    def task(identity, mode='exchange'):
        return dict(id=identity * 32, status='running', phase='waiting', mode=mode,
                    direction='drain' if mode == 'exchange' else 'fill', stage='drain',
                    start_level=60, target_level=100 if mode == 'exchange' else 80,
                    total_seconds=440, elapsed_seconds=60, remaining_seconds=380,
                    progress=60 / 440 * 100, fill_seconds=200, drain_seconds=400,
                    estimated_rounds=8, round=1, round_limit_seconds=60,
                    round_remaining_seconds=0, estimated_liters=9, reason=None,
                    created_at=time.time(), stop_origin='system', stop_action='round_stop')

    def route_api(route):
        if route.request.method == 'GET':
            snapshot['server_time'] = time.time()
            return route.fulfill(status=200, content_type='application/json', body=json.dumps(snapshot))
        body = route.request.post_data_json
        posts.append((route.request.url, copy.deepcopy(body)))
        assert route.request.url.endswith('/api/level-job/cancel'), posts
        assert body['job_id'] == snapshot['level_job']['id'], body
        assert body['intent'] == 'manual_stop' and body['source'] == 'web_ui', body
        assert body['event'] in ('click', 'keyboard_activation'), body
        assert body['button'] in ('exchange-stop-button', 'cancel-level-job'), body
        snapshot['level_job'].update(status='cancelled', phase='done', reason='cancelled_by_user',
                                     stop_origin='manual', stop_action='manual_stop',
                                     stop_source=body['source'], stop_event=body['event'],
                                     stop_button=body['button'])
        return route.fulfill(status=200, content_type='application/json', body=json.dumps(snapshot['level_job']))

    def press(page, selector):
        box = page.locator(selector).bounding_box()
        assert box
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down()

    def layouts(page):
        for width, height, name in ((1440, 900, 'desktop'), (390, 844, 'mobile'), (360, 640, 'small')):
            page.set_viewport_size(dict(width=width, height=height))
            assert_one_screen(page)
            expect(page.locator('#exchange-stop-button')).to_be_visible()
            expect(page.locator('#exchange-button')).to_be_hidden()
            page.screenshot(path=str(output / ('manual-stop-' + name + '.png')), full_page=True)
            open_dialog(page, 'history')
            assert_dialog_bounds(page, 'history')
            page.screenshot(path=str(output / ('sources-' + name + '.png')), full_page=True)
            close_dialog(page, 'history')
        page.set_viewport_size(dict(width=1440, height=900))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get('WATER_TEST_BROWSER', 'msedge'), headless=True,
                args=['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
            page = browser.new_page(viewport=dict(width=1440, height=900))
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', route_api)
            page.goto(server.origin + '/water/')
            expect(page.locator('#exchange-button')).to_be_enabled()

            # Pressing the startup button must never become STOP after refresh.
            press(page, '#exchange-button')
            snapshot['level_job'] = task('a')
            sync_status(page)
            expect(page.locator('#exchange-button')).to_be_hidden()
            page.mouse.up()
            page.wait_for_timeout(100)
            assert not posts, posts
            expect(page.locator('#exchange-confirm')).to_be_hidden()

            # A press on task A cannot stop newer task B appearing before release.
            press(page, '#exchange-stop-button')
            snapshot['level_job'] = task('b')
            sync_status(page)
            page.mouse.up()
            expect(page.locator('#command-feedback')).to_contain_text('未停止新任务')
            assert not posts

            # Synthetic click/keyboard events, polling, and a system round OFF
            # never create a cancellation request or pretend to be a manual STOP.
            page.evaluate("""() => {
              for (const id of ['exchange-stop-button','cancel-level-job']) {
                const button=document.getElementById(id);
                button.click();
                button.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}));
                button.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'Enter'}));
                button.dispatchEvent(new MouseEvent('click',{bubbles:true}));
              }
            }""")
            snapshot['commands'] = [
                dict(id='1'*32, created=time.time(), command='DRAIN_OFF', status='succeeded',
                     result='OK DRAIN_OFF', origin='system', origin_reason='round_stop', action='round_stop'),
                dict(id='2'*32, created=time.time(), command='STOP', status='succeeded',
                     result='OK STOP', origin='system', origin_reason='stop_unconfirmed', action='protection_stop'),
                dict(id='3'*32, created=time.time(), command='STOP', status='succeeded',
                     result='OK STOP', origin='manual', origin_reason='cancelled_by_user',
                     action='manual_stop', source='web_ui', event='click', button='exchange-stop-button'),
                dict(id='4'*32, created=time.time(), command='STOP', status='succeeded',
                     result='level_job_cancelled', origin='legacy_unknown', origin_reason=None, action=None),
                dict(id='5'*32, created=time.time(), command='FILL', status='succeeded',
                     result='OK FILL', origin='system', origin_reason='automatic_round_start', action='round_start'),
                dict(id='6'*32, created=time.time(), command='DRAIN', status='succeeded',
                     result='OK DRAIN', origin='system', origin_reason='automatic_round_start', action='recovery_resume'),
            ]
            sync_status(page)
            page.wait_for_timeout(2200)
            assert not posts
            rows = page.locator('#commands-list tr')
            expect(rows.nth(0)).to_contain_text('系统轮间关断')
            expect(rows.nth(1)).to_contain_text('系统保护停止')
            expect(rows.nth(2)).to_contain_text('人工停止')
            expect(rows.nth(3)).to_contain_text('来源未记录')
            expect(rows.nth(3)).not_to_contain_text('人工停止')
            expect(rows.nth(4)).to_contain_text('系统分轮开启')
            expect(rows.nth(5)).to_contain_text('系统续行开启')
            expect(rows.nth(4)).not_to_contain_text('系统轮间关断')
            expect(rows.nth(5)).not_to_contain_text('系统轮间关断')
            layouts(page)

            # The real pointer activation binds the id at press and declares the
            # explicit manual intent, independently of system OFF observations.
            with page.expect_response('**/api/level-job/cancel'):
                page.locator('#exchange-stop-button').click()
            assert posts[-1][1] == dict(job_id='b'*32, intent='manual_stop', source='web_ui',
                                       event='click', button='exchange-stop-button'), posts
            sync_status(page)
            expect(page.locator('#job-reason')).to_contain_text('人工停止已提交')

            snapshot['level_job'] = task('c', 'target')
            sync_status(page)
            open_dialog(page, 'level-job')
            page.locator('#cancel-level-job').focus()
            page.keyboard.down('Space')
            snapshot['level_job'] = task('d', 'target')
            sync_status(page)
            page.keyboard.up('Space')
            expect(page.locator('#command-feedback')).to_contain_text('未停止新任务')
            assert len(posts) == 1, posts
            close_dialog(page, 'level-job')

            # Both Enter and Space are trusted keyboard activation and are bound
            # to the task present at keydown. Target waiting no longer calibrates
            # implicitly; an explicit STOP must precede calibration.
            for index, key in enumerate(('Enter', 'Space')):
                snapshot['level_job'] = task(str(index + 5), 'target')
                sync_status(page)
                expect(page.locator('#save-calibration')).to_be_disabled()
                open_dialog(page, 'level-job')
                page.locator('#cancel-level-job').focus()
                with page.expect_response('**/api/level-job/cancel'):
                    page.keyboard.press(key)
                assert posts[-1][1]['event'] == 'keyboard_activation', posts
                assert posts[-1][1]['job_id'] == str(index + 5) * 32
                close_dialog(page, 'level-job')

            # Old cancelled_by_user only establishes a request, not a person.
            snapshot['level_job'].update(stop_origin='legacy_unknown', stop_action=None)
            sync_status(page)
            expect(page.locator('#job-reason')).to_have_text('已收到停止请求，来源未记录。')
            snapshot['level_job']['reason'] = 'manual_override'
            sync_status(page)
            expect(page.locator('#job-reason')).to_have_text('已收到停止或覆盖请求，来源未记录。')
            expect(page.locator('#job-reason')).not_to_contain_text('手动操作')
            assert len(posts) == 3, posts
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    print('PASS STOP UI origin: dedicated start/stop controls, trusted pointer/Enter/Space intent, '
          'press-bound job id and stale/new-job races, synthetic event and polling guards, '
          'target waiting calibration lock, distinct manual/system/legacy history, '
          'three desktop/phone one-screen states and history dialogs')


if __name__ == '__main__':
    main()
