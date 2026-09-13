"""Real local HTTP + Edge integration for the compact 3D console; no physical IO."""
import json
import math
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/debug-python'))
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
import websocket
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
                     '#tank-canvas', '#level-value', '#fill-button', '#drain-button', '#stop', '#menu-level-job',
                     '#fill-progress-text', '#drain-progress-text', '#orbit-hint',
                     '#fill-countdown', '#drain-countdown',
                     '#volume-value', '#fill-rate', '#drain-rate', '#fill-volume',
                     '#drain-volume', '#net-flow', '#eta-label', '#eta-value', '#estimate-status'):
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
    if metrics['width'] <= 700 and metrics['height'] >= 640:
        scene = page.locator('#tank-canvas').bounding_box()
        assert scene['height'] >= 110, ('Water metrics must leave a usable phone scene', scene)
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


def expect_number(page, selector, value, tolerance=.11):
    """Assert the rendered quantity without coupling to unit placement or rounding."""
    page.wait_for_function("""({selector, value, tolerance}) => {
      const text = document.querySelector(selector)?.textContent.replaceAll('−', '-');
      const match = text?.match(/[+-]?\\d+(?:\\.\\d+)?/);
      return match && Math.abs(Number(match[0]) - value) <= tolerance;
    }""", arg=dict(selector=selector, value=value, tolerance=tolerance), timeout=6000)


def expect_eta(page, seconds):
    text = page.locator('#eta-value').inner_text().strip()
    assert re.fullmatch(r'\d{2,}:\d{2}(?::\d{2})?', text), text
    parts = [int(part) for part in text.split(':')]
    actual = sum(part * 60 ** index for index, part in enumerate(reversed(parts)))
    assert abs(actual - math.ceil(seconds)) <= 2, (text, seconds)


def sync_status(page):
    with page.expect_response('**/api/status') as response:
        page.evaluate("window.dispatchEvent(new Event('online'))")
    assert response.value.ok, response.value.text()


def expect_countdown(page, output, seconds):
    selector = '#' + output + '-countdown'
    if seconds is None:
        expect(page.locator(selector)).to_have_text('—', timeout=6000)
        return
    page.wait_for_function("""({selector, seconds}) => {
      const text = document.querySelector(selector)?.textContent.trim();
      if (!/^\\d{2}:\\d{2}$/.test(text || '')) return false;
      const [minutes, remaining] = text.split(':').map(Number);
      return Math.abs(minutes * 60 + remaining - seconds) <= 2;
    }""", arg=dict(selector=selector, seconds=seconds), timeout=6000)


def assert_output_countdowns(page, store, report, output):
    """Board-version limits and observed starts survive UI refreshes independently."""
    command_requests = []

    def track_commands(request):
        if request.method == 'POST' and urlsplit(request.url).path.endswith('/api/commands'):
            command_requests.append(request.url)

    page.on('request', track_commands)
    commands_before = store.snapshot()['commands']
    try:
        report(version='0.8.1', state='IDLE', fill='0', drain='0', ready='1',
               outputs_known='1', reason='ready')
        sync_status(page)
        expect(page.locator('#version')).to_have_text('v0.8.1')
        expect(page.locator('#fill-progress-text')).to_have_text(re.compile(r'/\s*180\s*秒$'))
        expect(page.locator('#drain-progress-text')).to_have_text(re.compile(r'/\s*300\s*秒$'))
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        # Output protection does not require a calibrated capacity or water level.
        assert not store.snapshot()['simulation']['calibrated']
        report(state='EXCHANGING', fill='1', drain='1', reason='manual_exchanging')
        starts = store.snapshot()['simulation']
        report(elapsed=10)
        sync_status(page)
        expect_countdown(page, 'fill', 170)
        expect_countdown(page, 'drain', 290)
        expect_number(page, '#fill-progress-text', 10, tolerance=2)
        expect_number(page, '#drain-progress-text', 10, tolerance=2)
        for width, height, name in ((1440, 900, 'desktop'), (1366, 768, 'laptop'),
                                    (390, 844, 'mobile'), (360, 640, 'mobile-small')):
            page.set_viewport_size({'width': width, 'height': height})
            assert_one_screen(page)
            page.screenshot(path=str(output / ('aquarium-countdown-' + name + '.png')), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 900})
        page.reload()
        expect(page.locator('#console')).to_be_visible()
        expect_countdown(page, 'fill', 170)
        expect_countdown(page, 'drain', 290)
        for key in ('fill_on_since', 'drain_on_since'):
            assert store.snapshot()['simulation'][key] == starts[key]

        # Closing/reopening the inlet cannot restart the already-running drain.
        report(elapsed=5, state='DRAINING', fill='0', reason='manual_draining')
        sync_status(page)
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', 285)
        report(elapsed=0, state='EXCHANGING', fill='1', reason='manual_exchanging')
        sync_status(page)
        expect_countdown(page, 'fill', 180)
        expect_countdown(page, 'drain', 285)
        assert store.snapshot()['simulation']['drain_on_since'] == starts['drain_on_since']
        fill_restarted = store.snapshot()['simulation']['fill_on_since']
        report(elapsed=5, state='FILLING', drain='0', reason='manual_filling')
        sync_status(page)
        expect_countdown(page, 'fill', 175)
        expect_countdown(page, 'drain', None)
        assert store.snapshot()['simulation']['fill_on_since'] == fill_restarted
        report(elapsed=0, state='EXCHANGING', drain='1', reason='manual_exchanging')
        sync_status(page)
        expect_countdown(page, 'fill', 175)
        expect_countdown(page, 'drain', 300)

        # Losing browser/API synchronization or IO knowledge must remove both
        # clocks instead of displaying a confident count from a stale snapshot.
        page.route('**/api/status', lambda route: route.abort())
        page.evaluate("window.dispatchEvent(new Event('online'))")
        expect(page.locator('#header-connection')).to_contain_text('状态未知', timeout=6000)
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        page.unroute('**/api/status')
        sync_status(page)
        expect_countdown(page, 'fill', 175)
        expect_countdown(page, 'drain', 300)
        report(outputs_known='0')
        sync_status(page)
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        assert store.snapshot()['simulation']['uncertain']
        report(outputs_known='1')
        sync_status(page)
        # Unknown IO may have been ON throughout. A new known ON cannot claim
        # that firmware restarted either protection timer at the latest report.
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        assert store.snapshot()['simulation']['uncertain']
        report(state='IDLE', fill='0', drain='0', reason='stopped')
        sync_status(page)
        assert not store.snapshot()['simulation']['uncertain']
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        report(elapsed=0, state='EXCHANGING', fill='1', drain='1', reason='manual_exchanging')
        sync_status(page)
        expect_countdown(page, 'fill', 180)
        expect_countdown(page, 'drain', 300)
        # A still-running 0.8.0 board retains its actual 120-second limits.
        report(version='0.8.0', state='IDLE', fill='0', drain='0', reason='ready')
        report(elapsed=0, state='EXCHANGING', fill='1', drain='1', reason='manual_exchanging')
        report(elapsed=10)
        sync_status(page)
        expect(page.locator('#version')).to_have_text('v0.8.0')
        for name in ('fill', 'drain'):
            expect_countdown(page, name, 110)
            expect(page.locator('#' + name + '-progress-text')).to_have_text(re.compile(r'/\s*120\s*秒$'))

        # Simulated telemetry deliberately keeps outputs ON beyond both limits:
        # the browser can show zero, but firmware—not a web timer—stops hardware.
        report(version='0.8.1', state='IDLE', fill='0', drain='0', reason='ready')
        report(elapsed=0, state='EXCHANGING', fill='1', drain='1', reason='manual_exchanging')
        for _ in range(18):
            report(elapsed=10)
        sync_status(page)
        expect(page.locator('#fill-countdown')).to_have_text('00:00')
        expect_countdown(page, 'drain', 120)
        expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'true')
        assert not command_requests, command_requests
        for _ in range(12):
            report(elapsed=10)
        sync_status(page)
        for name in ('fill', 'drain'):
            expect(page.locator('#' + name + '-countdown')).to_have_text('00:00')
            expect(page.locator('#' + name + '-button')).to_have_attribute('aria-checked', 'true')
        assert not command_requests, command_requests
        assert store.snapshot()['commands'] == commands_before
        store.gateway = None
        store.connection_event(False, 'peer_disconnected')
        sync_status(page)
        expect(page.locator('#header-connection')).to_contain_text('设备异常')
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        report()
        sync_status(page)
        expect(page.locator('#header-connection')).to_contain_text('设备正常')
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        assert store.snapshot()['simulation']['uncertain']
        report(state='IDLE', fill='0', drain='0', reason='stopped')
        report(elapsed=0, state='EXCHANGING', fill='1', drain='1', reason='manual_exchanging')
        sync_status(page)
        expect_countdown(page, 'fill', 180)
        expect_countdown(page, 'drain', 300)
    finally:
        page.remove_listener('request', track_commands)
        page.unroute('**/api/status')
        report(version='0.8.0', state='IDLE', fill='0', drain='0', ready='1',
               outputs_known='1', reason='ready')
        sync_status(page)


def assert_water_estimates(page, store, report, output):
    """Exercise liters through real local HTTP, using only simulated device reports."""
    # Capacity is deliberately optional: an existing time-only calibration remains
    # useful for percentages without inventing a volume or a configured flow rate.
    assert store.snapshot()['simulation']['capacity_liters'] is None
    expect(page.locator('#volume-value')).to_contain_text('容量未设置')
    expect(page.locator('#fill-volume')).to_have_text('— L')
    expect(page.locator('#drain-volume')).to_have_text('— L')

    def calibrate(level, capacity='60'):
        open_dialog(page, 'calibration')
        page.locator('#capacity-liters').fill(capacity)
        page.locator('#fill-seconds').fill('100')
        page.locator('#drain-seconds').fill('50')
        page.locator('#anchor-level').fill(str(level))
        with page.expect_response('**/api/simulation') as response:
            page.locator('#save-calibration').click()
        assert response.value.ok, response.value.text()
        expect(page.locator('#level-value')).to_have_text(str(level) + '%')
        close_dialog(page, 'calibration')
        expect(page.locator('#message')).to_be_empty()
        assert page.locator('#stop').evaluate("""button => {
          const box = button.getBoundingClientRect();
          return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
            ?.closest('#stop') === button;
        }"""), 'Calibration feedback must not cover the stop control after closing its dialog'

    def expect_amounts(volume, fill_run, drain_run, fill_total, drain_total):
        expected = dict(volume_liters=volume, fill_run_liters=fill_run,
                        drain_run_liters=drain_run, fill_total_liters=fill_total,
                        drain_total_liters=drain_total)
        simulation = store.snapshot()['simulation']
        for key, value in expected.items():
            assert abs(simulation[key] - value) < .00001, (key, simulation[key], value)
        expect_number(page, '#volume-value', volume)
        expect_number(page, '#fill-volume', fill_run)
        expect_number(page, '#drain-volume', drain_run)
        expect_number(page, '#fill-total', fill_total)
        expect_number(page, '#drain-total', drain_total)

    calibrate(50)
    expect_amounts(30, 0, 0, 0, 0)
    expect_number(page, '#fill-rate', 36)
    expect_number(page, '#drain-rate', 72)
    expect_number(page, '#net-flow', 0)
    expect(page.locator('#eta-value')).to_have_text('—')
    open_dialog(page, 'calibration')
    expect(page.locator('#calibration-updated')).not_to_have_text('—')
    expect(page.locator('#fill-total')).to_be_visible()
    expect(page.locator('#drain-total')).to_be_visible()
    close_dialog(page, 'calibration')

    report(state='FILLING', fill='1', reason='manual_filling')
    report(elapsed=10)
    expect_amounts(36, 6, 0, 6, 0)
    expect_number(page, '#net-flow', 36)
    expect(page.locator('#eta-label')).to_contain_text('满')
    expect_eta(page, 40)

    # Only continuous, fresh reports account for delivery. A second open output
    # changes net flow while keeping the already-running inlet's own total.
    report(state='EXCHANGING', drain='1', reason='manual_exchanging')
    report(elapsed=5)
    expect_amounts(33.15, 9.15, 6, 9.15, 6)
    expect_number(page, '#net-flow', -36)
    expect(page.locator('#eta-label')).to_contain_text('空')
    expect_eta(page, 55.25)
    for width, height, name in ((1440, 900, 'desktop'), (1366, 768, 'laptop'),
                                (390, 844, 'mobile'), (360, 640, 'mobile-small')):
        page.set_viewport_size({'width': width, 'height': height})
        assert_one_screen(page)
        page.screenshot(path=str(output / ('aquarium-estimates-' + name + '.png')), full_page=True)
        if width == 360:
            open_dialog(page, 'calibration')
            assert_dialog_bounds(page, 'calibration')
            page.locator('#drain-total').scroll_into_view_if_needed()
            expect(page.locator('#drain-total')).to_be_visible()
            page.screenshot(path=str(output / 'aquarium-estimates-mobile-calibration.png'), full_page=True)
            close_dialog(page, 'calibration')
    page.set_viewport_size({'width': 1440, 'height': 900})

    report(state='DRAINING', fill='0', reason='manual_draining')
    report(elapsed=5)
    expect_amounts(27, 9.3, 12.3, 9.3, 12.3)
    expect_number(page, '#net-flow', -72)
    expect_eta(page, 22.5)
    report(state='IDLE', drain='0', reason='stopped')
    expect_amounts(26.7, 9.3, 12.6, 9.3, 12.6)
    expect_number(page, '#net-flow', 0)
    expect(page.locator('#eta-value')).to_have_text('—')
    page.reload()
    expect(page.locator('#console')).to_be_visible()
    expect_amounts(26.7, 9.3, 12.6, 9.3, 12.6)

    # A new inlet run starts at zero; its calibration total survives that reset.
    report(state='FILLING', fill='1', reason='manual_filling')
    report(elapsed=2)
    expect_amounts(27.9, 1.2, 12.6, 10.5, 12.6)
    expect_number(page, '#net-flow', 36)
    expect_eta(page, 53.5)
    page.route('**/api/status', lambda route: route.abort())
    expect(page.locator('#estimate-status')).to_contain_text('同步中断', timeout=6000)
    expect(page.locator('#net-flow')).to_contain_text('—')
    expect(page.locator('#eta-value')).to_have_text('—')
    expect_number(page, '#fill-volume', 1.2)
    expect_number(page, '#fill-total', 10.5)
    page.unroute('**/api/status')
    expect(page.locator('#header-connection')).to_contain_text('设备正常', timeout=6000)
    expect_amounts(27.9, 1.2, 12.6, 10.5, 12.6)
    expect_number(page, '#net-flow', 36)

    # A gap beyond fresh telemetry's allowance cannot be reconstructed from the
    # later report. The old quantities remain explicitly uncertain, even online.
    report(elapsed=13)
    expect(page.locator('#estimate-status')).to_contain_text('不确定', timeout=6000)
    expect_countdown(page, 'fill', None)
    expect_countdown(page, 'drain', None)
    assert store.snapshot()['simulation']['uncertain']
    expect_amounts(27.9, 1.2, 12.6, 10.5, 12.6)
    expect(page.locator('#net-flow')).to_contain_text('—')
    expect(page.locator('#eta-value')).to_have_text('—')
    report(state='IDLE', fill='0', reason='stopped')
    expect(page.locator('#fill-button')).to_have_attribute('aria-checked', 'false', timeout=6000)
    expect(page.locator('#estimate-status')).to_contain_text('不确定')
    expect_amounts(27.9, 1.2, 12.6, 10.5, 12.6)

    calibrate(100)
    expect_amounts(60, 0, 0, 0, 0)
    assert not store.snapshot()['simulation']['uncertain']
    expect(page.locator('#estimate-status')).not_to_contain_text('不确定')
    # Full/empty are volume bounds, not an excuse to discard a still-open
    # valve's delivered-water estimate. This also matches the reference card.
    report(state='FILLING', fill='1', reason='manual_filling')
    report(elapsed=10)
    expect_amounts(60, 6, 0, 6, 0)
    expect_eta(page, 0)
    report(state='IDLE', fill='0', reason='stopped')
    calibrate(100)
    expect_amounts(60, 0, 0, 0, 0)
    assert_one_screen(page)


def assert_logout_race(page):
    """Timer/focus events cannot overlap a slow request; logout invalidates it."""
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


def assert_web_soft_limits(page, store, clock, output, version='0.8.2'):
    """Exercise versioned Web policies with simulated authenticated WS receipts."""
    unlocked_timeout = version in ('0.8.2', '0.8.3')
    image_prefix = 'aquarium-web-limits-' + version.replace('.', '')
    console_url = page.url
    # Leave a calibrated water estimate uncertain using an actual telemetry gap.
    # The new control timer must remain usable without recalibrating that history.
    status = dict(store.status, version='0.8.1', control_mode='manual', state='IDLE',
                  fill='0', drain='0', ready='1', outputs_known='1', reason='ready')
    gateway = store.gateway or 'a' * 32
    store.poll(gateway, status)
    store.configure_simulation(dict(level=40, fill_seconds=100, drain_seconds=200))
    status.update(state='FILLING', fill='1', reason='manual_filling')
    store.poll(gateway, status)
    clock[0] += 13
    status.update(state='IDLE', fill='0', reason='stopped')
    store.poll(gateway, status)
    assert store.snapshot()['simulation']['uncertain']
    store.gateway = None
    store.gateway_seen = 0
    status.update(version=version, state='IDLE', reason='ready')
    session = store.ws_open(status)
    command_requests = []

    def track_commands(request):
        if request.method == 'POST' and urlsplit(request.url).path.endswith('/api/commands'):
            command_requests.append(request.url)

    def report(seconds=0, ack=None, **changes):
        clock[0] += seconds
        status.update(changes)
        store.ws_touch(session, status, ack)

    def advance(seconds):
        # Keep the device's active one-second reports fresh while fake time moves.
        for _ in range(seconds):
            report(seconds=1)

    def apply_command(selector, expected_command, **changes):
        with page.expect_response('**/api/commands') as response:
            page.locator(selector).click()
        assert response.value.ok, response.value.text()
        offer = store.ws_offer(session)
        assert offer and offer['command'] == expected_command, offer
        execute = store.ws_claim(session, offer['id'])
        assert execute['type'] == 'execute', execute
        report(ack=dict(id=offer['id'], status='succeeded', result='OK ' + expected_command), **changes)
        sync_status(page)

    page.on('request', track_commands)
    try:
        sync_status(page)
        expect(page.locator('#version')).to_have_text('v' + version)
        expect(page.locator('#device-timeout-note')).to_contain_text('5 秒')
        expect(page.locator('#fill-button')).to_be_enabled()
        expect(page.locator('#estimate-status')).to_contain_text('不确定')
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        apply_command('#fill-button', 'FILL', state='FILLING', fill='1', reason='manual_filling')
        advance(10)
        apply_command('#drain-button', 'DRAIN', state='EXCHANGING', drain='1', reason='manual_exchanging')
        starts = store.snapshot()['control_limits']
        assert starts['source'] == 'web' and not starts['uncertain'], starts
        assert store.snapshot()['simulation']['uncertain']
        assert store.snapshot()['simulation']['fill_on_since'] is None
        expect_countdown(page, 'fill', 170)
        expect_countdown(page, 'drain', 300)
        advance(5)
        sync_status(page)
        apply_command('#fill-button', 'FILL_OFF', state='DRAINING', fill='0', reason='manual_draining')
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', 295)
        apply_command('#fill-button', 'FILL', state='EXCHANGING', fill='1', reason='manual_exchanging')
        expect_countdown(page, 'fill', 180)
        expect_countdown(page, 'drain', 295)
        assert store.snapshot()['control_limits']['drain_on_since'] == starts['drain_on_since']
        restarted = store.snapshot()['control_limits']['fill_on_since']
        page.reload()
        expect(page.locator('#console')).to_be_visible()
        expect_countdown(page, 'fill', 180)
        assert store.snapshot()['control_limits']['fill_on_since'] == restarted
        for width, height, name in ((1440, 900, 'desktop'), (390, 844, 'mobile'), (360, 640, 'mobile-small')):
            page.set_viewport_size({'width': width, 'height': height})
            assert_one_screen(page)
            page.screenshot(path=str(output / (image_prefix + '-' + name + '.png')), full_page=True)

        # Browser synchronization loss hides countdowns while the independent
        # server policy continues observing the authenticated device connection.
        page.route('**/api/status', lambda route: route.abort())
        page.evaluate("window.dispatchEvent(new Event('online'))")
        expect(page.locator('#header-connection')).to_contain_text('状态未知', timeout=6000)
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        advance(2)
        page.unroute('**/api/status')
        sync_status(page)
        expect_countdown(page, 'fill', 178)
        expect_countdown(page, 'drain', 293)

        manual_count = len(command_requests)
        page.goto('about:blank')
        advance(177)
        page.goto(console_url)
        expect(page.locator('#console')).to_be_visible()
        expect_countdown(page, 'fill', 1)
        assert store.snapshot()['control_limits']['fill_on_since'] == restarted
        advance(1)
        sync_status(page)
        expect(page.locator('#fill-countdown')).to_have_text('关断中')
        expect_countdown(page, 'drain', 115)
        expect(page.locator('#control-hint')).to_contain_text('服务端正在确认全部关闭')
        for name in ('fill', 'drain'):
            expect(page.locator('#' + name + '-button')).to_have_attribute('aria-checked', 'true')
        assert len(command_requests) == manual_count, 'The browser posted an automatic command'
        assert store.snapshot()['control_limits']['timeout_pending'] == 'fill'
        page.screenshot(path=str(output / (image_prefix + '-pending-mobile.png')), full_page=True)

        def timeout_receipt(command, reason):
            requests_before = len(command_requests)
            command_ids_before = {item['id'] for item in store.snapshot()['commands']}
            offer = store.ws_offer(session)
            assert offer and offer['command'] == command, offer
            execute = store.ws_claim(session, offer['id'])
            assert execute['type'] == 'execute', execute
            report(ack=dict(id=offer['id'], status='succeeded', result='OK ' + command),
                   state='IDLE' if unlocked_timeout else 'FAULT',
                   ready='1' if unlocked_timeout else '0', fill='0', drain='0', reason=reason)
            sync_status(page)
            for name in ('fill', 'drain'):
                expect(page.locator('#' + name + '-button')).to_have_attribute('aria-checked', 'false')
                expect_countdown(page, name, None)
            if unlocked_timeout:
                expect(page.locator('#header-connection')).to_contain_text('设备正常')
                expect(page.locator('#device-state')).to_have_text('待机')
                expect(page.locator('#control-hint')).not_to_contain_text('故障锁定')
                if version == '0.8.3':
                    expect(page.locator('#control-hint')).to_contain_text('到时已关闭，可再次开启')
                else:
                    expect(page.locator('#control-hint')).to_contain_text('输出已关闭，可再次开启')
                expect(page.locator('#fill-button')).to_be_enabled()
                expect(page.locator('#drain-button')).to_be_enabled()
                expect(page.locator('#reset')).to_be_disabled()
                assert store.snapshot()['control_limits']['timeout_pending'] is None
                assert store.snapshot()['control_limits']['fill_on_since'] is None
                assert store.snapshot()['control_limits']['drain_on_since'] is None
                # Allow both the local UI timer and the status poll to run. An
                # automatic RESET or restart must never be used to unlock limits.
                page.wait_for_timeout(2200)
                assert len(command_requests) == requests_before, 'The browser auto-reset or restarted after timeout'
                assert {item['id'] for item in store.snapshot()['commands']} == command_ids_before, \
                    'The service auto-reset or restarted after timeout'
                for name in ('fill', 'drain'):
                    expect(page.locator('#' + name + '-button')).to_have_attribute('aria-checked', 'false')
                assert_one_screen(page)
                page.screenshot(path=str(output / (image_prefix + '-' + command.lower() + '-idle.png')), full_page=True)
            else:
                expect(page.locator('#header-connection')).to_contain_text('设备异常')
                expect(page.locator('#fill-button')).to_be_disabled()
                expect(page.locator('#reset')).to_be_enabled()

        def reset_fault():
            open_dialog(page, 'device')
            page.locator('#reset').click()
            expect(page.locator('#confirm')).to_be_visible()
            apply_command('#confirm button[value="ok"]', 'RESET', state='IDLE', ready='1',
                          fill='0', drain='0', reason='reset')
            close_dialog(page, 'device')
            expect(page.locator('#fill-button')).to_be_enabled()

        timeout_receipt('STOP' if version == '0.8.2' else 'FILL_TIMEOUT',
                        'stopped' if version == '0.8.2' else 'fill_timeout')
        if version == '0.8.3':
            expect(page.locator('#device-reason')).to_contain_text('补水')
        if not unlocked_timeout:
            reset_fault()
        # A drain-only run gets its complete five-minute policy, independently
        # of the previous inlet timeout and the still-uncertain water estimate.
        apply_command('#drain-button', 'DRAIN', state='DRAINING', drain='1', reason='manual_draining')
        expect_countdown(page, 'drain', 300)
        manual_count = len(command_requests)
        advance(299)
        sync_status(page)
        expect_countdown(page, 'drain', 1)
        advance(1)
        sync_status(page)
        expect(page.locator('#drain-countdown')).to_have_text('关断中')
        expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true')
        assert len(command_requests) == manual_count
        assert store.snapshot()['control_limits']['timeout_pending'] == 'drain'
        timeout_receipt('STOP' if version == '0.8.2' else 'DRAIN_TIMEOUT',
                        'stopped' if version == '0.8.2' else 'drain_timeout')
        if version == '0.8.3':
            expect(page.locator('#device-reason')).to_contain_text('排水')
        if unlocked_timeout:
            # Confirmed normal timeout leaves the controls ready for a new,
            # explicitly requested run with a complete new policy deadline.
            apply_command('#fill-button', 'FILL', state='FILLING', fill='1', reason='manual_filling')
            expect_countdown(page, 'fill', 180)
            new_limits = store.snapshot()['control_limits']
            assert new_limits['fill_on_since'] > restarted
            assert new_limits['fill_deadline'] - new_limits['fill_on_since'] == 180
            expect_countdown(page, 'drain', None)
            advance(1)
            sync_status(page)
            expect_countdown(page, 'fill', 179)
            apply_command('#fill-button', 'FILL_OFF', state='IDLE', fill='0', reason='stopped')

            # Other firmware faults retain their actual lock and require an
            # explicit user RESET; a normal timeout is not a blanket fault bypass.
            requests_before = len(command_requests)
            command_ids_before = {item['id'] for item in store.snapshot()['commands']}
            report(state='FAULT', ready='0', reason='communication_timeout')
            sync_status(page)
            expect(page.locator('#header-connection')).to_contain_text('设备异常')
            expect(page.locator('#fill-button')).to_be_disabled()
            expect(page.locator('#drain-button')).to_be_disabled()
            expect(page.locator('#reset')).to_be_enabled()
            expect(page.locator('#control-hint')).to_contain_text('故障锁定')
            page.wait_for_timeout(2200)
            assert len(command_requests) == requests_before, 'The browser automatically reset a genuine fault'
            assert {item['id'] for item in store.snapshot()['commands']} == command_ids_before
        reset_fault()
        store.ws_close(session, 'peer_disconnected')
        sync_status(page)
        expect(page.locator('#header-connection')).to_contain_text('设备异常')
        expect_countdown(page, 'fill', None)
        expect_countdown(page, 'drain', None)
        expect(page.locator('#fill-button')).to_be_disabled()
        expect(page.locator('#stop')).to_be_disabled()
        page.screenshot(path=str(output / (image_prefix + '-offline-mobile.png')), full_page=True)
        assert_one_screen(page)
    finally:
        page.remove_listener('request', track_commands)
        page.unroute('**/api/status')
        store.ws_close(session, 'peer_disconnected')


def assert_level_heartbeats(page, store, clock, server, output):
    """Real 0.8.2 bare pings and acknowledged, bounded target-water rounds."""
    client = None
    inbox = []
    seq = 0
    executed = []
    status = dict(store.status, control_mode='manual', state='IDLE', reason='ready',
                  ready='1', fill='0', drain='0', outputs_known='1')

    def receive(kind):
        for index, message in enumerate(inbox):
            if message['type'] == kind:
                return inbox.pop(index)
        for _ in range(10):
            raw = client.recv()
            assert raw, 'The local simulated device unexpectedly disconnected'
            message = json.loads(raw)
            if message['type'] == kind:
                return message
            inbox.append(message)
        raise AssertionError(('Expected WS frame', kind, inbox))

    def close_device():
        nonlocal client
        if client:
            client.close()
            client = None
            for _ in range(100):
                if store.ws_gateway is None:
                    break
                time.sleep(.01)
            assert store.ws_gateway is None

    def open_device(version='0.8.2'):
        nonlocal client, seq
        close_device()
        store.gateway = None
        store.gateway_seen = 0
        inbox.clear()
        seq = 0
        status.update(version=version, state='IDLE', reason='ready', ready='1', fill='0', drain='0')
        client = websocket.create_connection('ws://127.0.0.1:%d/water/api/device/ws' % server.server_port, timeout=3)
        client.send(json.dumps(dict(type='auth', key=server.device_key, status=status)))
        ready = receive('ready')
        assert ready['soft_limits']['watchdog_ms'] == 5000
        sync_status(page)
        expect(page.locator('#version')).to_have_text('v' + version)

    def calibrate(level):
        open_dialog(page, 'calibration')
        for field, value in [('anchor-level', level), ('fill-seconds', 3000),
                             ('drain-seconds', 3000), ('capacity-liters', 60)]:
            page.locator('#' + field).fill(str(value))
        with page.expect_response('**/api/simulation') as response:
            page.locator('#save-calibration').click()
        assert response.value.ok, response.value.text()
        close_dialog(page, 'calibration')
        sync_status(page)
        assert not store.snapshot()['simulation']['uncertain']

    def claim(expected_command):
        offer = receive('offer')
        assert offer['command'] == expected_command, offer
        client.send(json.dumps(dict(type='claim', id=offer['id'])))
        execute = receive('execute')
        assert execute['id'] == offer['id'] and execute['command'] == expected_command
        executed.append((expected_command, clock[0], offer['id']))
        return offer

    def acknowledge(offer, **changes):
        command = offer['command']
        if command in ('FILL', 'DRAIN'):
            status.update(state='FILLING' if command == 'FILL' else 'DRAINING',
                          reason='manual_filling' if command == 'FILL' else 'manual_draining',
                          fill='1' if command == 'FILL' else '0', drain='1' if command == 'DRAIN' else '0')
        elif command in ('FILL_OFF', 'DRAIN_OFF', 'STOP'):
            status.update(state='IDLE', ready='1', fill='0', drain='0', reason='stopped')
        status.update(changes)
        client.send(json.dumps(dict(type='ack', status=status,
            ack=dict(id=offer['id'], status='succeeded', result='OK ' + command))))
        receive('received')

    def receipt(expected_command, **changes):
        acknowledge(claim(expected_command), **changes)
        sync_status(page)

    def command(output_name, expected_command, **changes):
        with page.expect_response('**/api/commands') as response:
            page.locator('#' + output_name + '-button').click()
        assert response.value.ok, response.value.text()
        receipt(expected_command, **changes)

    def ping(seconds=1):
        nonlocal seq
        clock[0] += seconds
        seq += 1
        message = dict(type='ping', seq=seq)
        # Production 0.8.2 has no ping.status. Never inject fresh output reports
        # here: only a genuine ON/OFF ACK can change the device's known output.
        client.send(json.dumps(message))
        assert receive('pong') == dict(type='pong', seq=seq)
        snapshot = store.snapshot()
        assert snapshot['online'], snapshot['connection']
        return snapshot['simulation']

    def advance(seconds):
        for _ in range(seconds):
            result = ping()
            assert not result['uncertain'], result
        return store.snapshot()

    def expect_level(value, text):
        sim = store.snapshot()['simulation']
        assert math.isclose(sim['level'], value, abs_tol=1e-4), sim
        expect(page.locator('#level-value')).to_have_text(text, timeout=6000)
        bar = float(page.locator('#level-fill').evaluate('el => el.style.width').removesuffix('%'))
        assert abs(bar - value) < .001, (bar, value)

    def job():
        return store.snapshot()['level_job']

    def start_job(target):
        open_dialog(page, 'level-job')
        page.locator('#target-level').fill(str(target))
        expect(page.locator('#start-level-job')).to_be_enabled()
        with page.expect_response('**/api/level-job') as response:
            page.locator('#start-level-job').click()
        assert response.value.ok, response.value.text()
        close_dialog(page, 'level-job')
        assert job()['status'] == 'running', job()
        return job()

    def assert_no_restart(seconds=5):
        before = len(executed)
        for _ in range(seconds):
            ping()
        assert not any(frame['type'] == 'offer' for frame in inbox), inbox
        assert len(executed) == before
        assert status['fill'] == status['drain'] == '0'
        assert job()['status'] != 'running', job()

    def waiting_round():
        """Reach the first ordinary OFF, retaining the active task's pause."""
        calibrate(100)
        start_job(60)
        receipt('DRAIN')
        advance(290)
        receipt('DRAIN_OFF')
        assert job()['phase'] == 'waiting', job()

    def capture_layouts():
        # WebSocket inactivity uses real monotonic time even when the Store's
        # simulation clock is frozen. Keep the simulated device alive while
        # screenshots render; no output telemetry or water time is added.
        stop_heartbeat = threading.Event()
        heartbeat_errors = []

        def keep_alive():
            while not stop_heartbeat.wait(.5):
                try:
                    ping(0)
                except Exception as error:
                    heartbeat_errors.append(error)
                    return

        heartbeat = threading.Thread(target=keep_alive, daemon=True)
        heartbeat.start()
        try:
            for width, height, name in [(1440, 900, 'desktop'), (390, 844, 'mobile'), (360, 640, 'mobile-small')]:
                page.set_viewport_size(dict(width=width, height=height))
                assert_one_screen(page)
                expect(page.locator('#menu-level-job')).to_be_visible()
                expect(page.locator('#job-summary')).to_be_visible()
                page.screenshot(path=str(output / ('level-job-082-' + name + '.png')), full_page=True)
                open_dialog(page, 'level-job')
                assert_dialog_bounds(page, 'level-job')
                expect(page.locator('#job-round')).to_contain_text('1')
                expect(page.locator('#job-target')).to_contain_text('80')
                expect(page.locator('#cancel-level-job')).to_be_enabled()
                expect(page.locator('#job-elapsed')).to_have_text('3 秒')
                expect_number(page, '#job-volume', .06, .06)
                page.screenshot(path=str(output / ('level-job-dialog-082-' + name + '.png')), full_page=True)
                close_dialog(page, 'level-job')
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=5)
        assert not heartbeat.is_alive() and not heartbeat_errors, heartbeat_errors

    try:
        open_device()
        calibrate(100)
        # Idle pings alone cannot invent a run, and an unacknowledged ON grant
        # cannot turn a known OFF output into estimated moving water.
        advance(20)
        assert store.snapshot()['simulation']['level'] == 100
        with page.expect_response('**/api/commands') as response:
            page.locator('#drain-button').click()
        assert response.value.ok
        on = claim('DRAIN')
        advance(3)
        assert store.snapshot()['simulation']['level'] == 100
        assert store.snapshot()['simulation']['drain_total_liters'] == 0
        acknowledge(on)
        for elapsed in range(1, 21):
            sim = ping()
            assert not sim['uncertain'], (elapsed, sim)
            assert math.isclose(sim['level'], 100 - elapsed / 30, abs_tol=1e-7), (elapsed, sim)
            if elapsed == 3:
                # The ordinary two-second HTTP refresh must show a decimal
                # change even without another device status frame.
                expect_level(99.9, '99.9%')
        sync_status(page)
        expect_level(100 - 20 / 30, '99.3%')
        assert math.isclose(store.snapshot()['simulation']['drain_total_liters'], .4, abs_tol=1e-7)
        command('drain', 'DRAIN_OFF')

        calibrate(100)
        trace_start = len(executed)
        start_job(66.6667)
        sync_status(page)
        open_dialog(page, 'calibration')
        expect(page.locator('#save-calibration')).to_be_disabled()
        close_dialog(page, 'calibration')
        first_on = claim('DRAIN')
        # The latest status still says OFF, but an already-issued ON may arrive
        # late. Both the UI and the API must refuse a new calibration anchor.
        sync_status(page)
        pending_job_id = job()['id']
        open_dialog(page, 'calibration')
        expect(page.locator('#save-calibration')).to_be_disabled()
        # Use the browser's authenticated fetch (including its loopback Secure
        # cookie behavior), rather than Playwright's separate HTTP client.
        response = page.evaluate("""async body => {
          const response = await fetch('./api/simulation', {method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
          return {status:response.status, body:await response.json()};
        }""", dict(level=90, fill_seconds=3000, drain_seconds=3000, capacity_liters=60))
        assert response['status'] == 409 and response['body']['error'] == 'simulation_requires_idle', response
        assert store.snapshot()['simulation']['level'] == 100
        assert job()['id'] == pending_job_id and job()['status'] == 'running' and job()['phase'] == 'starting', job()
        close_dialog(page, 'calibration')
        acknowledge(first_on)
        sync_status(page)
        total_active = 0
        previous_off = None
        console_url = page.url
        for round_number, seconds in enumerate((290, 290, 290, 130), 1):
            if round_number > 1:
                receipt('DRAIN')
            started = clock[0]
            if previous_off is not None:
                assert math.isclose(started - previous_off, 2, abs_tol=1e-7)
            assert job()['round'] == round_number and job()['phase'] == 'active', job()
            if round_number == 2:
                # The task belongs to the server; closing the web view neither
                # pauses its timer nor removes the next acknowledged stop.
                page.goto('about:blank')
            for elapsed in range(1, seconds + 1):
                sim = ping()
                assert not sim['uncertain'], (round_number, elapsed, sim)
                assert math.isclose(sim['level'], 100 - (total_active + elapsed) / 30, abs_tol=1e-6), sim
            total_active += seconds
            assert job()['phase'] == 'stopping', job()
            if round_number == 2:
                page.goto(console_url)
                expect(page.locator('#console')).to_be_visible()
            # A queued OFF is not physical confirmation: the UI must continue
            # showing ON until the real simulated device ACK says both are OFF.
            sync_status(page)
            expect(page.locator('#drain-button')).to_have_attribute('aria-checked', 'true')
            receipt('DRAIN_OFF')
            previous_off = clock[0]
            assert status['state'] == 'IDLE'
            expect(page.locator('#reset')).to_be_disabled()
            if round_number < 4:
                assert job()['phase'] == 'waiting', job()
                waiting_level = store.snapshot()['simulation']['level']
                ping()
                assert job()['phase'] == 'waiting' and job()['round'] == round_number, job()
                assert store.snapshot()['simulation']['level'] == waiting_level
                assert not any(frame['type'] == 'offer' for frame in inbox), inbox
                ping()
            else:
                assert job()['status'] == 'completed' and job()['phase'] == 'done', job()

        sync_status(page)
        expect_level(100 - 1000 / 30, '66.7%')
        sim = store.snapshot()['simulation']
        assert math.isclose(sim['drain_total_liters'], 20, abs_tol=1e-6), sim
        assert math.isclose(sim['drain_run_liters'], 2.6, abs_tol=1e-6), sim
        assert math.isclose(sim['volume_liters'], 40, abs_tol=1e-6), sim
        assert math.isclose(job()['elapsed_seconds'], 1000, abs_tol=1e-6), job()
        assert math.isclose(job()['estimated_liters'], 20, abs_tol=1e-6), job()
        open_dialog(page, 'level-job')
        expect(page.locator('#job-state')).to_have_text('目标任务已完成')
        expect(page.locator('#job-elapsed')).to_have_text('16 分 40 秒')
        expect_number(page, '#job-volume', 20)
        close_dialog(page, 'level-job')
        assert [command for command, _, _ in executed[trace_start:]] == ['DRAIN', 'DRAIN_OFF'] * 4
        assert_no_restart()

        # A new fill task demonstrates that successful rounds never require
        # RESET on the production firmware's FAULT-on-TIMEOUT protocol.
        start_job(80)
        receipt('FILL')
        advance(3)
        sync_status(page)
        expect_level(100 - 1000 / 30 + .1, '66.8%')
        capture_layouts()
        with page.expect_response('**/api/commands') as response:
            page.locator('#stop').click()
        assert response.value.ok, response.value.text()
        receipt('STOP')
        assert job()['status'] == 'cancelled', job()
        assert_no_restart()

        # Explicit cancel stops the active output and cancels future rounds.
        start_job(80)
        receipt('FILL')
        advance(3)
        open_dialog(page, 'level-job')
        with page.expect_response('**/api/level-job/cancel') as response:
            page.locator('#cancel-level-job').click()
        assert response.value.ok, response.value.text()
        close_dialog(page, 'level-job')
        receipt('STOP')
        assert job()['status'] == 'cancelled', job()
        assert_no_restart()

        # An ordinary manual OFF is an interruption, never permission to
        # schedule another automatic ON after the two-second gap.
        start_job(80)
        receipt('FILL')
        advance(3)
        sync_status(page)
        command('fill', 'FILL_OFF')
        assert job()['status'] == 'cancelled', job()
        assert_no_restart()

        # Recalibration is permitted during a confirmed-OFF inter-round gap;
        # changing the anchor must cancel the task that used the old anchor.
        waiting_round()
        calibrate(90)
        assert job()['status'] == 'cancelled', job()
        assert_no_restart()
        assert store.snapshot()['simulation']['level'] == 90

        start_job(80)
        receipt('DRAIN')
        advance(3)
        known_level = store.snapshot()['simulation']['level']
        close_device()
        assert job()['status'] in ('failed', 'cancelled'), job()
        clock[0] += 6
        open_device()
        assert_no_restart()
        assert store.snapshot()['simulation']['uncertain']
        assert store.snapshot()['simulation']['level'] == known_level
        sync_status(page)
        expect(page.locator('#estimate-status')).to_contain_text('不确定')
        calibrate(75)
        command('fill', 'FILL')
        advance(3)
        sync_status(page)
        expect_level(75.1, '75.1%')
        command('fill', 'FILL_OFF')
        assert not any(command in ('FILL_TIMEOUT', 'DRAIN_TIMEOUT', 'RESET') for command, _, _ in executed)

        # A claimed grant and a confirmed ON are both required. A free-standing
        # ON report with no authorized run closes the connection, never creating
        # an estimated flow from otherwise well-formed bare pings.
        open_device()
        calibrate(75)
        status.update(state='DRAINING', drain='1', reason='manual_draining')
        client.send(json.dumps(dict(type='status', status=status)))
        for _ in range(100):
            if store.ws_gateway is None:
                break
            time.sleep(.01)
        assert store.ws_gateway is None
        assert store.snapshot()['simulation']['level'] == 75
        assert store.snapshot()['simulation']['drain_total_liters'] == 0
    finally:
        close_device()


def main(layout_only=False, estimates_only=False, countdowns_only=False, soft_limits_only=False,
         level_heartbeats_only=False):
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

    def report(elapsed=.25, **changes):
        now[0] += elapsed
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

            if level_heartbeats_only:
                assert_level_heartbeats(page, store, now, server, output)
                assert not errors, errors
                browser.close()
                print('PASS level targets: real WS 0.8.2 bare pings only after confirmed ON, '
                      'unacknowledged grants cannot invent water flow, 1000 seconds in 290/290/290/130 rounds, '
                      'ordinary OFF ACKs with 2-second gaps, correct run/total liters and decimal updates, '
                      'STOP/cancel/manual/recalibration/disconnection interruptions, no automatic restart, '
                      'uncertainty survives reconnect until recalibration, desktop and two phone viewports')
                return

            if soft_limits_only:
                for version in ('0.8.2', '0.8.3'):
                    assert_web_soft_limits(page, store, now, output, version)
                assert_logout_race(page)
                assert not errors, errors
                browser.close()
                print('PASS Web 0.8.2/0.8.3 soft limits: authenticated WS claim/receipt, independent 180/300 seconds, '
                      'uncertain water estimate with valid control timer, reload and closed-page persistence, '
                      'API outage hides clocks, server timeout pending without browser commands, '
                      '0.8.2 STOP / 0.8.3 timeout confirmed IDLE and user-started new run without automatic RESET/ON, '
                      'other faults require RESET, desktop/phone layouts and logout race')
                return

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
            if not estimates_only:
                assert_output_countdowns(page, store, report, output)
            if countdowns_only:
                assert not errors, errors
                browser.close()
                print('PASS independent output countdowns: firmware 0.8.1 180/300 seconds, '
                      'legacy 0.8.0 120 seconds, reload persistence, independent output restarts, '
                      'uncalibrated levels, API/IO/offline unknowns, zero without web commands and four viewports')
                return
            open_dialog(page, 'calibration')
            page.locator('#fill-seconds').fill('100')
            page.locator('#drain-seconds').fill('50')
            page.locator('[data-anchor="100"]').click()
            page.locator('#save-calibration').click()
            expect(page.locator('#level-value')).to_have_text('100%')
            close_dialog(page, 'calibration')
            assert_one_screen(page)

            assert_water_estimates(page, store, report, output)
            if estimates_only:
                assert not errors, errors
                browser.close()
                print('PASS water estimate browser: optional capacity, calibrated rates, current/run/total liters, '
                      'concurrent net flow and ETA, independent run reset, service interruption, stale telemetry, '
                      'recalibration, four viewports and phone calibration dialog')
                return

            # Rejection and pending receipts must never optimistically toggle outputs.
            report(version='0.8.1')
            sync_status(page)
            expect(page.locator('#version')).to_have_text('v0.8.1')
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

            for version in ('0.8.2', '0.8.3'):
                assert_web_soft_limits(page, store, now, output, version)
            report()
            sync_status(page)
            assert_level_heartbeats(page, store, now, server, output)
            report()
            sync_status(page)
            store.gateway = None
            store.connection_event(False, 'peer_disconnected')
            expect(page.locator('#header-connection')).to_contain_text('设备异常', timeout=6000)
            expect(page.locator('#fill-button')).to_be_disabled()
            expect(page.locator('#stop')).to_be_disabled()
            open_dialog(page, 'device')
            expect(page.locator('#hero-status')).to_contain_text('离线')
            close_dialog(page, 'device')

            assert_logout_race(page)
            assert not errors, errors
            browser.close()
            print('PASS compact 3D browser: both entry URLs, desktop 1440/1366, phone 390/360, '
                  'one-screen controls, mouse/touch/keyboard orbit, modal scrolling/close, automatic polling/receipts, '
                  'concurrent outputs, legacy guards, fault/offline indicator, visible dialog feedback, '
                  'optional capacity, liters/rates/run and calibration totals, net flow/ETA, '
                  'versioned independent protection countdowns, reload persistence, no web timer commands, '
                  '0.8.2/0.8.3 server limits, normal timeout recovery and fault/reset receipts independent of water estimates, '
                  '0.8.2 bare heartbeats, 1000-second target task with four acknowledged bounded rounds, '
                  'task stop/cancel/manual/calibration/disconnection handling and decimal level display, '
                  'independent run reset, uncertainty/recalibration, API recovery, logout race')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        store.db.close()


if __name__ == '__main__':
    main(layout_only='--layout-only' in sys.argv, estimates_only='--estimates-only' in sys.argv,
         countdowns_only='--countdowns-only' in sys.argv, soft_limits_only='--soft-limits-only' in sys.argv,
         level_heartbeats_only=any(flag in sys.argv for flag in ('--level-heartbeats-only', '--target-level-only')))
