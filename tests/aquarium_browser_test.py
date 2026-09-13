"""Real local HTTP + Edge integration for the compact 3D console; no physical IO."""
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/debug-python'))
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from server.app import Handler, Server, Store


class StaticHandler(Handler):
    def do_GET(self):
        name = urlsplit(self.path).path.removeprefix('/water/') or 'index.html'
        allowed = ('aquarium.html', 'aquarium.css', 'aquarium.js', 'aquarium-scene.js', 'aquarium-motion.js',
                   'vendor/three.module.js', 'index.html')
        if name not in allowed:
            return self.dispatch()
        data = (ROOT / 'deploy/www' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/css' if name.endswith('.css') else
                         'text/javascript' if name.endswith('.js') else 'text/html')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def assert_one_screen(page):
    metrics = page.evaluate("""() => ({width: innerWidth, height: innerHeight,
      rootWidth: document.documentElement.scrollWidth,
      rootHeight: document.documentElement.scrollHeight,
      bodyWidth: document.body.scrollWidth, bodyHeight: document.body.scrollHeight})""")
    assert max(metrics['rootWidth'], metrics['bodyWidth']) <= metrics['width'] + 1, metrics
    assert max(metrics['rootHeight'], metrics['bodyHeight']) <= metrics['height'] + 1, metrics
    for selector in ('#header-connection', '#menu-device', '#menu-history', '#menu-calibration',
                     '#tank-canvas', '#level-value', '#fill-button', '#drain-button', '#stop',
                     '#fill-progress-text', '#drain-progress-text', '#orbit-hint'):
        box = page.locator(selector).bounding_box()
        assert box and box['width'] > 0 and box['height'] > 0, (selector, box)
        assert box['x'] >= -1 and box['x'] + box['width'] <= metrics['width'] + 1, (selector, box)
        assert box['y'] >= -1 and box['y'] + box['height'] <= metrics['height'] + 1, (selector, box)
    title = page.locator('#tank-title').bounding_box()
    hint = page.locator('#orbit-hint').bounding_box()
    assert title and hint
    overlap_width = min(title['x'] + title['width'], hint['x'] + hint['width']) - max(title['x'], hint['x'])
    overlap_height = min(title['y'] + title['height'], hint['y'] + hint['height']) - max(title['y'], hint['y'])
    assert overlap_width <= 1 or overlap_height <= 1, ('scene title overlaps gesture hint', title, hint)
    expect(page.locator('[data-view]')).to_have_count(0)


def assert_canvas_rendered(page):
    # Inspect framebuffer samples after animation frames; a throttled renderer
    # may deliberately skip a frame, so allow several frames to produce pixels.
    # This catches a black/cleared canvas without assuming specific tank geometry.
    rendered = page.evaluate("""() => new Promise(resolve => {
      const canvas = document.getElementById('tank-canvas');
      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      if (!gl || !gl.drawingBufferWidth || !gl.drawingBufferHeight) return resolve(false);
      const pixel = new Uint8Array(4);
      let attempts = 0;
      function sample() {
        for (const [x, y] of [[.25,.5],[.5,.5],[.75,.5],[.5,.25],[.5,.75]]) {
          gl.readPixels(Math.floor(gl.drawingBufferWidth*x), Math.floor(gl.drawingBufferHeight*y),
            1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
          if (pixel[0] + pixel[1] + pixel[2] > 30 && pixel[3] > 0) return resolve(true);
        }
        if (++attempts >= 6) return resolve(false);
        requestAnimationFrame(sample);
      }
      requestAnimationFrame(sample);
    })""")
    assert rendered, 'The visible scene canvas must render nonblack content'
    expect(page.locator('#scene-fallback')).to_be_hidden()


def camera_sample(page, frames=1):
    previous = page.evaluate('window.__testCamera?.frame || 0')
    page.wait_for_function('frame => window.__testCamera?.frame >= frame', arg=previous + frames)
    return page.evaluate('window.__testCamera')


def direction_distance(first, second):
    return sum((a - b) ** 2 for a, b in zip(first['direction'], second['direction'])) ** .5


def mouse_orbit(page, horizontal=.18, vertical=-.10):
    box = page.locator('#tank-canvas').bounding_box()
    start_x, start_y = box['x'] + box['width'] * .43, box['y'] + box['height'] * .52
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + box['width'] * horizontal,
                    start_y + box['height'] * vertical, steps=8)
    page.mouse.up()
    return camera_sample(page)


def assert_orbit_controls(page, context, output):
    canvas = page.locator('#tank-canvas')
    expect(canvas).to_have_attribute('tabindex', '0')
    expect(page.locator('#orbit-hint')).to_be_visible()
    expect(page.locator('[data-view]')).to_have_count(0)
    # Observe actual camera orientation rather than differences caused by turtles.
    # The hook is confined to this disposable test page, and preserves any callback.
    page.evaluate("""async () => {
      const THREE = await import('./vendor/three.module.js');
      const previous = THREE.Scene.prototype.onBeforeRender;
      THREE.Scene.prototype.onBeforeRender = function(renderer, scene, camera, ...rest) {
        previous?.call(this, renderer, scene, camera, ...rest);
        window.__testCamera = {frame:(window.__testCamera?.frame || 0)+1,
          direction:camera.getWorldDirection(new THREE.Vector3()).toArray(),
          position:camera.position.toArray()};
      };
    }""")
    initial = camera_sample(page)
    assert all(abs(value) > .05 for value in initial['direction']), initial
    assert_canvas_rendered(page)
    page.screenshot(path=str(output / 'aquarium-orbit-default.png'), full_page=True)

    dragged = mouse_orbit(page)
    assert direction_distance(initial, dragged) > .03, ('mouse drag did not rotate camera', initial, dragged)
    released = camera_sample(page, frames=12)
    assert direction_distance(dragged, released) < .002, ('camera moved back after release', dragged, released)
    page.screenshot(path=str(output / 'aquarium-orbit-dragged.png'), full_page=True)
    canvas.dblclick()
    double_click_reset = camera_sample(page, frames=3)
    assert direction_distance(initial, double_click_reset) < .002, 'Double-click did not restore the default view'
    released = mouse_orbit(page)
    assert direction_distance(double_click_reset, released) > .03, 'Double-click reset left dragging unavailable'
    page.set_viewport_size({'width': 1366, 'height': 768})
    resized = camera_sample(page, frames=3)
    assert direction_distance(released, resized) < .002, ('resize reset camera direction', released, resized)
    assert_canvas_rendered(page)

    # Cancel a real mouse pointer so setPointerCapture has an actual active pointer.
    canvas.evaluate("el => el.addEventListener('pointerdown', event => {window.__testPointerId=event.pointerId;}, {once:true})")
    box = canvas.bounding_box()
    x, y = box['x'] + box['width'] * .4, box['y'] + box['height'] * .5
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 35, y + 15, steps=3)
    pointer_id = page.evaluate('window.__testPointerId')
    canvas.dispatch_event('pointercancel', dict(pointerId=pointer_id, pointerType='mouse', isPrimary=True,
                                              button=0, buttons=0, clientX=x + 35, clientY=y + 15))
    cancelled = camera_sample(page)
    page.mouse.move(x + 110, y + 30, steps=3)
    page.mouse.up()
    after_cancel = camera_sample(page, frames=3)
    assert direction_distance(cancelled, after_cancel) < .002, 'Cancelled pointer kept rotating the camera'
    redragged = mouse_orbit(page, horizontal=-.14, vertical=.06)
    assert direction_distance(after_cancel, redragged) > .03, 'Pointer cancellation left orbit controls stuck'

    canvas.focus()
    page.keyboard.press('ArrowLeft')
    keyboard_rotated = camera_sample(page)
    assert direction_distance(redragged, keyboard_rotated) > .01, 'Arrow key did not rotate the camera'
    page.keyboard.press('Home')
    restored = camera_sample(page, frames=3)
    assert direction_distance(initial, restored) < .002, ('Home did not restore the default view', initial, restored)

    page.set_viewport_size({'width': 390, 'height': 844})
    before_touch = camera_sample(page, frames=3)
    assert direction_distance(restored, before_touch) < .002, 'Phone resize changed the selected view'
    touch = context.new_cdp_session(page)
    touch.send('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 1})
    try:
        box = canvas.bounding_box()
        x, y = box['x'] + box['width'] * .38, box['y'] + box['height'] * .5
        def touch_point(px, py):
            return {'x': px, 'y': py, 'id': 0, 'radiusX': 1, 'radiusY': 1, 'force': 1}
        touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [touch_point(x, y)]})
        for step in range(1, 7):
            touch.send('Input.dispatchTouchEvent', {'type': 'touchMove',
                       'touchPoints': [touch_point(x + step * 12, y - step * 5)]})
        touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        touched = camera_sample(page)
        assert direction_distance(before_touch, touched) > .03, 'Phone touch drag did not rotate camera'
        assert direction_distance(touched, camera_sample(page, frames=8)) < .002, 'Phone view moved back after touch release'
        assert_one_screen(page)
        assert_canvas_rendered(page)
        page.screenshot(path=str(output / 'aquarium-mobile-orbit.png'), full_page=True)
    finally:
        touch.send('Emulation.setTouchEmulationEnabled', {'enabled': False})
        touch.detach()
    canvas.focus()
    page.keyboard.press('Home')
    page.set_viewport_size({'width': 1440, 'height': 900})
    assert direction_distance(initial, camera_sample(page, frames=3)) < .002


def open_dialog(page, name, via_indicator=False):
    page.locator('#header-connection' if via_indicator else '#menu-' + name).click()
    dialog = page.locator('#' + name + '-dialog')
    expect(dialog).to_be_visible()
    assert dialog.evaluate("el => el.tagName === 'DIALOG' && el.open")
    assert page.evaluate("document.querySelectorAll('dialog[open]').length") == 1
    return dialog


def close_dialog(page, name, escape=False):
    if escape:
        page.keyboard.press('Escape')
    else:
        page.locator('#close-' + name).click()
    expect(page.locator('#' + name + '-dialog')).to_be_hidden()


def assert_dialog_bounds(page, name, require_scroll=False):
    dialog = page.locator('#' + name + '-dialog')
    box = dialog.bounding_box()
    viewport = page.viewport_size
    assert box and box['x'] >= -1 and box['y'] >= -1, box
    assert box['x'] + box['width'] <= viewport['width'] + 1, box
    assert box['y'] + box['height'] <= viewport['height'] + 1, box
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight + 1')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    if require_scroll:
        scrollable = dialog.evaluate("""dialog => {
          const elements = [dialog, ...dialog.querySelectorAll('*')];
          const scroller = elements.find(el => el.scrollHeight > el.clientHeight + 4 &&
            /auto|scroll/.test(getComputedStyle(el).overflowY));
          if (!scroller) return false;
          scroller.scrollTop = scroller.scrollHeight;
          return scroller.scrollTop > 0;
        }""")
        assert scrollable, 'Long history must scroll inside the dialog'


def main(layout_only=False):
    # A controlled clock keeps simulated device connectivity stable during UI work.
    now = [time.time()]
    store = Store(':memory:', lambda: now[0])
    server = Server(('127.0.0.1', 0), store, 'test-admin-key-' * 3, 'test-device-key-' * 3, '')
    server.RequestHandlerClass = StaticHandler
    server.origin = 'http://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    status = dict(project='water_auto_exchange', version='0.8.0', control_mode='manual',
                  state='IDLE', reason='ready', ready='1', fill='0', drain='0',
                  outputs_known='1', need_fill='unknown', overflow='0', cycle='0',
                  overflow_protection='0')
    gateway = 'a' * 32
    for index in range(24):
        now[0] += 1
        store.connection_event(index % 2 == 0, 'connected' if index % 2 == 0 else 'peer_disconnected')
    store.poll(gateway, status)
    command_count = 0

    def report(**changes):
        now[0] += .25
        status.update(changes)
        store.poll(gateway, status)

    def submitted(page, selector, expected):
        nonlocal command_count
        with page.expect_response('**/api/commands') as response:
            page.locator(selector).click()
        assert response.value.ok
        command_count += 1
        commands = store.snapshot()['commands']
        assert len(commands) == command_count
        assert commands[0]['command'] == expected, commands[0]
        return commands[0]

    def acknowledge(item, accepted=True, **changes):
        # Device poll delivers the command; only a later receipt changes reported IO.
        report()
        status.update(changes)
        now[0] += .25
        result = ('OK ' if accepted else 'ERROR ') + item['command']
        store.poll(gateway, status, dict(id=item['id'],
                   status='succeeded' if accepted else 'rejected', result=result))

    output = ROOT / 'build/browser'
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True,
                args=['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, device_scale_factor=1)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.origin + '/water/')
            page.locator('#key').fill('bad')
            page.locator('#login-form button[type="submit"]').click()
            expect(page.locator('#message')).to_contain_text('管理密钥不正确')
            page.locator('#key').fill(server.admin_key)
            page.locator('#login-form button[type="submit"]').click()
            expect(page.locator('#console')).to_be_visible()
            expect(page.locator('#header-connection')).to_contain_text('设备正常')
            expect(page.locator('#fill-button')).to_be_enabled()
            assert page.evaluate("document.querySelector('#tank-canvas').width > 0")
            assert page.evaluate("document.querySelector('#scene-fallback').hidden")
            expect(page.get_by_text('经典控制台', exact=False)).to_have_count(0)

            # Both the default route and the old 3D bookmark reach this same console.
            for path in ('/water/index.html', '/water/aquarium.html'):
                page.goto(server.origin + path)
                expect(page.locator('#console')).to_be_visible()
                expect(page.locator('#header-connection')).to_contain_text('设备正常')
                expect(page.locator('#menu-calibration')).to_be_visible()
                expect(page.get_by_text('经典控制台', exact=False)).to_have_count(0)
            for detail in ('hero-status', 'hero-seen', 'version', 'device-state', 'device-mode',
                           'sync-status', 'fill-state', 'drain-state', 'last-seen',
                           'online-duration', 'device-reason', 'traffic-total', 'traffic-boot',
                           'traffic-interval', 'traffic-reported'):
                assert page.locator('#' + detail).evaluate("el => el.closest('dialog')?.id") == 'device-dialog', detail

            assert_orbit_controls(page, context, output)

            # These common desktop and phone sizes must show the scene and all controls.
            for width, height, name in ((1440, 900, 'desktop'), (1366, 768, 'laptop'),
                                        (390, 844, 'mobile'), (360, 640, 'mobile-small')):
                page.set_viewport_size({'width': width, 'height': height})
                assert_one_screen(page)
                page.screenshot(path=str(output / ('aquarium-' + name + '.png')), full_page=True)
                for dialog_name in ('device', 'history', 'calibration'):
                    open_dialog(page, dialog_name)
                    if dialog_name == 'history':
                        page.locator('#tab-connection').click()
                        expect(page.locator('#connection-list tr')).to_have_count(25)
                    assert_dialog_bounds(page, dialog_name, require_scroll=dialog_name == 'history' and width < 500)
                    if width == 390:
                        page.screenshot(path=str(output / ('aquarium-mobile-' + dialog_name + '.png')), full_page=True)
                    close_dialog(page, dialog_name, escape=dialog_name == 'calibration')
                    assert_one_screen(page)
                open_dialog(page, 'device', via_indicator=True)
                close_dialog(page, 'device', escape=True)

            if layout_only:
                assert not errors, errors
                browser.close()
                print('PASS compact 3D layout: default view, mouse/touch orbit, double-click reset, release/resize/cancel/keyboard, '
                      'four viewports, modal bounds and phone history scrolling')
                return

            page.set_viewport_size({'width': 1440, 'height': 900})
            open_dialog(page, 'calibration')
            page.locator('#fill-seconds').fill('100')
            page.locator('#drain-seconds').fill('50')
            page.locator('[data-anchor="100"]').click()
            page.locator('#save-calibration').click()
            expect(page.locator('#level-value')).to_have_text('100%')
            close_dialog(page, 'calibration')
            assert_one_screen(page)

            # Rejection and pending receipts must never optimistically toggle outputs.
            item = submitted(page, '#fill-button', 'FILL')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false')
            expect(page.locator('#drain-button')).to_be_disabled()
            acknowledge(item, accepted=False)
            expect(page.locator('#command-feedback')).to_contain_text('设备已拒绝', timeout=6000)
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false')
            expect(page.locator('#fill-button')).to_be_enabled()
            item = submitted(page, '#fill-button', 'FILL')
            acknowledge(item, state='FILLING', fill='1', reason='manual_filling')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true', timeout=6000)
            expect(page.locator('#drain-button')).to_be_enabled()
            item = submitted(page, '#drain-button', 'DRAIN')
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false')
            # Polling and receipts must keep working while a menu dialog is open.
            open_dialog(page, 'device')
            acknowledge(item, state='EXCHANGING', drain='1', reason='manual_exchanging')
            expect(page.locator('#device-state')).to_have_text('补水与排水同时进行', timeout=6000)
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true')
            expect(page.locator('#header-connection')).to_contain_text('设备正常')
            close_dialog(page, 'device')
            open_dialog(page, 'calibration')
            expect(page.locator('#save-calibration')).to_be_disabled()
            close_dialog(page, 'calibration')
            item = submitted(page, '#fill-button', 'FILL_OFF')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true')
            acknowledge(item, state='DRAINING', fill='0', reason='manual_draining')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false', timeout=6000)
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true')
            item = submitted(page, '#drain-button', 'DRAIN_OFF')
            acknowledge(item, state='IDLE', drain='0', reason='stopped')
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false', timeout=6000)
            expect(page.locator('#command-feedback')).to_contain_text('关闭冲水：设备已确认')
            open_dialog(page, 'history')
            page.locator('#tab-commands').click()
            expect(page.locator('#commands-list tr')).to_have_count(command_count)
            close_dialog(page, 'history')

            # A connected device can be abnormal; connection alone is not health.
            report(state='FAULT', reason='fill_timeout', ready='0')
            expect(page.locator('#header-connection')).to_contain_text('设备异常', timeout=6000)
            expect(page.locator('#fill-button')).to_be_disabled()
            expect(page.locator('#reset')).to_be_enabled()
            open_dialog(page, 'device', via_indicator=True)
            expect(page.locator('#device-state')).to_have_text('故障锁定')
            expect(page.locator('#device-reason')).to_contain_text('补水超时')
            # RESET feedback must be visible in the dialog where the action lives.
            page.locator('#reset').click()
            expect(page.locator('#confirm')).to_be_visible()
            item = submitted(page, '#confirm button[value="ok"]', 'RESET')
            expect(page.locator('#confirm')).to_be_hidden()
            expect(page.locator('#device-dialog')).to_be_visible()
            expect(page.locator('#device-feedback')).to_be_visible()
            expect(page.locator('#device-feedback')).to_contain_text('命令已提交')
            expect(page.locator('#device-state')).to_have_text('故障锁定')
            acknowledge(item, accepted=False)
            expect(page.locator('#device-feedback')).to_contain_text('故障复位：设备已拒绝', timeout=6000)
            expect(page.locator('#command-feedback')).to_contain_text('故障复位：设备已拒绝')
            expect(page.locator('#reset')).to_be_enabled()
            page.locator('#reset').click()
            item = submitted(page, '#confirm button[value="ok"]', 'RESET')
            acknowledge(item, state='IDLE', reason='reset', ready='1')
            expect(page.locator('#device-feedback')).to_contain_text('故障复位：设备已确认', timeout=6000)
            expect(page.locator('#device-feedback')).to_be_visible()
            expect(page.locator('#device-state')).to_have_text('待机')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false')
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'false')
            expect(page.locator('#reset')).to_be_disabled()
            report(state='FAULT', ready='0', reason='<img src=x onerror=alert(1)>', outputs_known='0')
            expect(page.locator('#device-reason')).to_contain_text('<img src=x', timeout=6000)
            expect(page.locator('#device-reason img')).to_have_count(0)
            expect(page.locator('#fill-detail')).to_have_text('状态未知')
            close_dialog(page, 'device')
            report(state='IDLE', reason='ready', ready='1', outputs_known='1')
            expect(page.locator('#header-connection')).to_contain_text('设备正常', timeout=6000)

            # Old firmware stays interlocked and uses STOP to turn an output off.
            report(version='0.7.7')
            expect(page.locator('#version')).to_have_text('v0.7.7', timeout=6000)
            item = submitted(page, '#fill-button', 'FILL')
            acknowledge(item, state='FILLING', fill='1', reason='manual_filling')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true', timeout=6000)
            expect(page.locator('#drain-button')).to_be_disabled()
            item = submitted(page, '#fill-button', 'STOP')
            acknowledge(item, state='IDLE', fill='0', reason='stopped')
            expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false', timeout=6000)
            report(version='0.6.0')
            expect(page.locator('#fill-button')).to_be_disabled(timeout=6000)
            expect(page.locator('#drain-button')).to_be_disabled()
            report(version='0.8.0', control_mode='automatic')
            expect(page.locator('#device-mode')).to_have_text('自动模式', timeout=6000)
            expect(page.locator('#fill-button')).to_be_disabled()
            report(control_mode='manual')
            expect(page.locator('#fill-button')).to_be_enabled(timeout=6000)

            # Browser service errors do not invent a device disconnect event.
            # An open calibration dialog must expose the retry notice and clear it
            # in place on recovery, without requiring the user to close the dialog.
            open_dialog(page, 'calibration')
            events_before = len(store.snapshot()['connection']['events'])
            page.route('**/api/status', lambda route: route.abort())
            expect(page.locator('#message')).to_contain_text('正在自动重试', timeout=6000)
            expect(page.locator('#calibration-message')).to_be_visible()
            expect(page.locator('#calibration-message')).to_contain_text('正在自动重试')
            expect(page.locator('#save-calibration')).to_be_disabled()
            expect(page.locator('#header-connection')).to_contain_text('状态未知')
            expect(page.locator('#hero-status')).to_contain_text('未知')
            expect(page.locator('#fill-button')).to_be_disabled()
            assert len(store.snapshot()['connection']['events']) == events_before
            page.unroute('**/api/status')
            expect(page.locator('#header-connection')).to_contain_text('设备正常', timeout=6000)
            expect(page.locator('#message')).to_be_empty()
            expect(page.locator('#calibration-dialog')).to_be_visible()
            expect(page.locator('#calibration-message')).to_be_empty()
            expect(page.locator('#save-calibration')).to_be_enabled()
            expect(page.locator('.panel-message').filter(has_text='正在自动重试')).to_have_count(0)
            close_dialog(page, 'calibration')
            # The first status request on a restored login also retries automatically.
            page.route('**/api/status', lambda route: route.abort())
            page.reload()
            expect(page.locator('#message')).to_contain_text('正在自动重试')
            page.unroute('**/api/status')
            expect(page.locator('#console')).to_be_visible(timeout=6000)
            expect(page.locator('#header-connection')).to_contain_text('设备正常')
            expect(page.locator('#message')).to_be_empty()

            store.gateway = None
            store.connection_event(False, 'peer_disconnected')
            expect(page.locator('#header-connection')).to_contain_text('设备异常', timeout=6000)
            expect(page.locator('#fill-button')).to_be_disabled()
            expect(page.locator('#stop')).to_be_disabled()
            open_dialog(page, 'device')
            expect(page.locator('#hero-status')).to_contain_text('离线')
            close_dialog(page, 'device')

            # Timer/focus events cannot overlap a slow request; logout invalidates it.
            pending = []
            page.route('**/api/status', lambda route: pending.append(route))
            with page.expect_request('**/api/status'):
                page.evaluate("window.dispatchEvent(new Event('online'))")
            page.wait_for_timeout(100)
            assert len(pending) == 1
            late_status = pending[0].fetch()
            page.evaluate("window.dispatchEvent(new Event('focus'));window.dispatchEvent(new Event('online'))")
            page.wait_for_timeout(2200)
            assert len(pending) == 1
            open_dialog(page, 'device')
            page.locator('#logout').click()
            expect(page.locator('#login-panel')).to_be_visible()
            expect(page.locator('#device-dialog')).to_be_hidden()
            pending[0].fulfill(response=late_status)
            page.wait_for_timeout(2200)
            expect(page.locator('#console')).to_be_hidden()
            assert len(pending) == 1
            assert not errors, errors
            browser.close()
            print('PASS compact 3D browser: both entry URLs, desktop 1440/1366, phone 390/360, '
                  'one-screen controls, mouse/touch/keyboard orbit, modal scrolling/close, automatic polling/receipts, '
                  'concurrent outputs, legacy guards, fault/offline indicator, visible dialog feedback, '
                  'API recovery, logout race')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        store.db.close()


if __name__ == '__main__':
    main(layout_only='--layout-only' in sys.argv)
