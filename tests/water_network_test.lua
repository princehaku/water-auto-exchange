local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local network = require "water_network"
local count = 0
local function test(name, fn) fn(); count=count+1; print("PASS "..name) end
local function fixture()
    local f = {tick=0, sent={}, calls={}, state="IDLE", decoded={}}
    f.config = {enabled=true, url="wss://example.test/water/api/device/ws",device_key=string.rep("k",32),ca_cert="water-ca.crt",
        heartbeat_ms=1000,active_heartbeat_ms=1000,offline_stop_ms=10000,idle_timeout_ms=75000}
    f.controller = {
        status=function() return {state=f.state} end,
        start=function() f.calls[#f.calls+1]="START"; f.state="DRAINING"; return true,"started" end,
        fill=function() f.calls[#f.calls+1]="FILL"; f.state="FILLING"; return true,"fill_started" end,
        stop=function() f.calls[#f.calls+1]="STOP"; f.state="IDLE"; return true,"stopped" end,
        reset=function() return false,"not_faulted" end
    }
    f.client = {
        send=function(_,value) f.sent[#f.sent+1]=value; return not f.send_fail end,
        close=function() f.closed=true end,
        start=function() f.started=true end
    }
    f.deps={tick=function() return f.tick end,
        sys={timerLoopStart=function(fn) f.step=fn; return 1 end},
        transport={new=function(_,callbacks) f.callbacks=callbacks; return f.client end},
        json={encode=function(value) return value end,decode=function() if f.decode_fail then error("decode") end; return f.decoded end},
        usb={format_status=function() return "project=water_auto_exchange version=0.5.2 state="..f.state end},
        read_cert=function() return f.no_cert and "" or "-----BEGIN CERTIFICATE-----" end}
    -- Ordinary messages use fake JSON tokens; auth concatenation needs strings.
    local serial=0
    f.messages={}
    f.deps.json.encode=function(value)
        serial=serial+1
        local token="json"..serial
        f.messages[token]=value
        return token
    end
    function f.start() return network.start(f.controller,f.config,f.deps) end
    function f.reply(value) f.decoded=value; f.callbacks.message("{}"); end
    function f.connect() assert(f.start()); f.callbacks.open(); f.reply({type="ready",session=string.rep("a",32)}) end
    function f.advance(ms) f.tick=(f.tick+ms*16)%4294967296;f.step() end
    function f.last() return f.messages[f.sent[#f.sent]] end
    function f.offer(command,id) f.reply({type="offer",command=command,id=id or string.rep("b",32)}) end
    function f.execute(command,ttl,id) f.reply({type="execute",command=command,id=id or string.rep("b",32),ttl_ms=ttl or 8000}) end
    return f
end

test("shipped heartbeat defaults are one second",function() local c=require "water_network_config";assert(c.heartbeat_ms==1000 and c.active_heartbeat_ms==1000);assert(c.offline_stop_ms==10000 and c.idle_timeout_ms==75000) end)

test("disabled network has no I/O",function() assert(not network.start({}, {enabled=false})) end)
test("plaintext and old HTTP URL rejected",function() local f=fixture();f.config.url="https://example.test/water";assert(not f.start()) end)
test("missing CA prevents connection",function() local f=fixture();f.no_cert=true;assert(not f.start());assert(not f.started) end)
test("auth key has a fixed nonsecret prefix",function() local f=fixture();f.connect();assert(f.sent[1]:find('{"type":"auth","protocol":"water-ws-v1","key"',1,true)==1) end)
test("idle heartbeat follows one second boundaries",function() local f=fixture();f.connect();for i=1,5 do local n=#f.sent;f.advance(500);assert(#f.sent==n);f.advance(500);assert(#f.sent==n+1 and f.last().type=="ping");f.reply({type="pong",seq=f.last().seq}) end end)
test("state changes are immediately reported",function() local f=fixture();f.connect();f.state="FAULT";f.advance(500);assert(f.last().type=="status") end)
test("offer alone cannot start outputs",function() local f=fixture();f.connect();f.offer("START");assert(f.last().type=="claim" and #f.calls==0) end)
test("claim execute and ack completes",function() local f=fixture();f.connect();f.offer("START");f.execute("START");assert(f.calls[1]=="START");assert(f.last().ack.status=="succeeded") end)
test("expired execute cannot start",function() local f=fixture();f.connect();f.offer("START");f.advance(1000);f.execute("START",500);assert(#f.calls==0 and f.last().ack.status=="rejected") end)
test("unsolicited execute ignored",function() local f=fixture();f.connect();f.execute("START");assert(#f.calls==0) end)
test("duplicate command never repeats",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.offer("START");f.execute("START");assert(#f.calls==1) end)
test("USB stop invalidates pending execute",function() local f=fixture();f.connect();f.offer("START");f.controller.stop();f.execute("START");assert(#f.calls==1 and f.closed) end)
test("STOP offer invalidates prior claim",function() local f=fixture();f.connect();f.offer("START");f.offer("STOP",string.rep("c",32));f.execute("START");assert(#f.calls==0);f.execute("STOP",8000,string.rep("c",32));assert(f.calls[1]=="STOP") end)
test("disconnect stops owned cycle",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.callbacks.close();assert(f.calls[2]=="STOP") end)
test("active heartbeat follows one second boundaries",function() local f=fixture();f.connect();f.offer("START");f.execute("START");for i=1,15 do local n=#f.sent;f.advance(500);assert(#f.sent==n);f.advance(500);assert(#f.sent==n+1 and f.last().type=="ping");f.reply({type="pong",seq=f.last().seq}) end;assert(not f.closed and #f.calls==1) end)
test("active timeout stops outputs",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.advance(10000);assert(f.calls[2]=="STOP" and f.closed) end)
test("pong preserves connection across idle heartbeat",function() local f=fixture();f.connect();for i=1,4 do f.advance(30000);f.reply({type="pong",seq=f.last().seq}) end;assert(not f.closed) end)
test("wrong pong does not extend deadline",function() local f=fixture();f.connect();f.advance(30000);f.reply({type="pong",seq=-1});f.advance(45000);assert(f.closed) end)
test("invalid JSON fails closed",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.decode_fail=true;f.reply({});assert(f.calls[2]=="STOP") end)
test("write exception reports uncertainty and stops",function() local f=fixture();f.controller.start=function() error("write") end;f.connect();f.offer("START");f.execute("START");assert(f.calls[1]=="STOP" and f.last().ack.status=="uncertain") end)
test("queue failure stops remote output",function() local f=fixture();f.connect();f.offer("START");f.send_fail=true;f.execute("START");assert(f.calls[2]=="STOP") end)
test("tick rollover keeps polling local",function() local f=fixture();f.tick=4294960000;f.connect();f.advance(30000);assert(f.last().type=="ping") end)
test("reversed clock stops and disables",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.tick=f.tick-1;f.step();assert(f.calls[2]=="STOP") end)
test("standalone USB cycle survives unrelated WS failure",function() local f=fixture();f.connect();f.controller.start();f.callbacks.close();assert(#f.calls==1) end)
test("timer failure does not start owner task",function() local f=fixture();f.deps.sys.timerLoopStart=function() return nil end;assert(not f.start());assert(not f.started) end)
test("dead transport owner stops remote cycle",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.client.failed=function() return true end;f.advance(500);assert(f.calls[2]=="STOP" and f.closed) end)
print("water_network: "..count.." tests passed")
