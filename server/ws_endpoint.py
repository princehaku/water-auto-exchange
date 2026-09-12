"""RFC 6455 framing via wsproto; one owner thread per authenticated connection."""
import hashlib
import hmac
import json
import select
import time
from wsproto import WSConnection, ConnectionType
from wsproto.events import AcceptConnection, Request, TextMessage, BytesMessage, Ping, CloseConnection

SAFE_CLOSE_REASONS = frozenset((
    'auth_failed', 'another_device_active', 'firmware_mismatch', 'stale_session',
    'invalid_status', 'invalid_state', 'invalid_control_mode', 'invalid_flag',
    'invalid_level_or_cycle', 'invalid_message', 'message_too_large',
    'invalid_claim', 'session_limit', 'unclaimed_ack', 'invalid_ack', 'invalid_type', 'invalid_traffic'))


def safe_close_reason(error):
    reason = getattr(error, 'message', None)
    if reason is None and type(error) is ValueError and error.args:
        reason = error.args[0]
    return reason if isinstance(reason, str) and reason in SAFE_CLOSE_REASONS else 'protocol_or_internal_error'


def serve(handler):
    ws = WSConnection(ConnectionType.SERVER)
    store, session, authenticated = handler.server.store, None, False
    handler.close_connection = True
    # No application data is exposed until authentication of the first message.
    try:
        ws.initiate_upgrade_connection([(k.lower().encode('ascii'), v.encode('latin1')) for k, v in handler.headers.items()], handler.path)
        if not any(isinstance(event, Request) for event in ws.events()):
            return handler.respond(400, {'error': 'invalid_upgrade'})
    except Exception:
        return handler.respond(400, {'error': 'invalid_upgrade'})
    connection = handler.connection
    connection.settimeout(2)
    connection.sendall(ws.send(AcceptConnection()))
    last_rx = time.monotonic()
    close_reason = 'connection_closed'
    fragments, size, claimed = [], 0, set()

    def send(value):
        connection.sendall(ws.send(TextMessage(data=json.dumps(value, separators=(',', ':')))))

    try:
        while True:
            with store.lock:
                active = store.status and store.status.get('state') in ('DRAINING', 'SETTLING', 'FILLING')
            deadline = 10 if active else 75
            if time.monotonic() - last_rx > (deadline if authenticated else 30):
                close_reason = 'heartbeat_timeout' if authenticated else 'auth_timeout'
                break
            if authenticated:
                offer = store.ws_offer(session)
                if offer:
                    send(offer)
            readable, _, _ = select.select([connection], [], [], 0.1)
            if not readable:
                continue
            data = connection.recv(4096)
            if not data:
                close_reason = 'peer_disconnected'
                break
            ws.receive_data(data)
            for event in ws.events():
                if isinstance(event, CloseConnection):
                    close_reason = 'peer_closed'
                    connection.sendall(ws.send(event.response()))
                    return
                if isinstance(event, Ping):
                    connection.sendall(ws.send(event.response()))
                    continue
                if not isinstance(event, (TextMessage, BytesMessage)):
                    continue
                part = event.data.encode('utf-8') if isinstance(event.data, str) else event.data
                fragments.append(part)
                size += len(part)
                if size > 8192:
                    raise ValueError('message_too_large')
                if not event.message_finished:
                    continue
                message = json.loads(b''.join(fragments).decode('utf-8'))
                fragments, size = [], 0
                if not isinstance(message, dict):
                    raise ValueError('invalid_message')
                kind = message.get('type')
                if not authenticated:
                    key = message.get('key')
                    if kind not in ('auth', 'probe') or not isinstance(key, str) or not hmac.compare_digest(hashlib.sha256(key.encode()).digest(), hashlib.sha256(handler.server.device_key.encode()).digest()):
                        raise ValueError('auth_failed')
                    if kind == 'probe':
                        send(dict(type='probe_ok', transport='wss'))
                        connection.sendall(ws.send(CloseConnection(code=1000)))
                        return
                    session = store.ws_open(message.get('status'), message.get('previous_session'))
                    authenticated = True
                    send(dict(type='ready', session=session))
                elif kind == 'ping':
                    store.ws_touch(session)
                    send(dict(type='pong', seq=message.get('seq')))
                elif kind == 'status':
                    store.ws_touch(session, status=message.get('status'))
                    send(dict(type='received'))
                elif kind == 'traffic':
                    store.traffic_report(session, message)
                    send(dict(type='received'))
                elif kind == 'claim':
                    command_id = message.get('id')
                    if not isinstance(command_id, str) or len(command_id) != 32:
                        raise ValueError('invalid_claim')
                    if command_id not in claimed:
                        if len(claimed) >= 4096:
                            raise ValueError('session_limit')
                        claimed.add(command_id)
                        store.ws_touch(session)
                        send(store.ws_claim(session, command_id))
                elif kind == 'ack':
                    if not isinstance(message.get('ack'), dict) or message['ack'].get('id') not in claimed:
                        raise ValueError('unclaimed_ack')
                    store.ws_touch(session, status=message.get('status'), ack=message['ack'])
                    send(dict(type='received'))
                else:
                    raise ValueError('invalid_type')
                last_rx = time.monotonic()
    except Exception as error:
        # Whitelist only: arbitrary exception messages may contain credentials.
        close_reason = safe_close_reason(error)
        print('WATER WS close reason=' + close_reason, flush=True)
        try:
            connection.sendall(ws.send(CloseConnection(code=1008, reason='connection ended')))
        except Exception:
            pass
    finally:
        if session:
            store.ws_close(session, close_reason)
