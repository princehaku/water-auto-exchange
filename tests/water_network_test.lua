local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local network = require "water_network"
local count = 0
local function test(name, fn) fn(); count=count+1; print("PASS "..name) end
local function fixture()
    local f = {tick=0, sent={}, calls={}, state="IDLE", decoded={}}
    f.config = {enabled=true, url="wss://example.test/water/api/device/ws",device_key=string.rep("k",32),long_connection_cert={caCert="water-ca.crt",hostNameFlag=1,insist=0},
        heartbeat_ms=1000,active_heartbeat_ms=1000,offline_stop_ms=10000,idle_timeout_ms=75000}
    f.controller = {
        status=function() return {state=f.state} end,
        start=function() f.calls[#f.calls+1]="START"; f.state="DRAINING"; return true,"started" end,
        fill=function() f.calls[#f.calls+1]="FILL"; f.state="FILLING"; return true,"fill_started" end,
        drain=function() f.calls[#f.calls+1]="DRAIN"; f.state="DRAINING"; return true,"drain_started" end,
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
        usb={format_status=function() return "project=water_auto_exchange version=0.7.4 state="..f.state end},
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
    function f.advance(ms) f.tick=(f.tick+ms/5)%4294967296;f.step() end
    function f.last() return f.messages[f.sent[#f.sent]] end
    function f.offer(command,id) f.reply({type="offer",command=command,id=id or string.rep("b",32)}) end
    function f.execute(command,ttl,id) f.reply({type="execute",command=command,id=id or string.rep("b",32),ttl_ms=ttl or 8000}) end
    return f
end

test("shipped heartbeat defaults are one second",function() local c=require "water_network_config";assert(c.heartbeat_ms==1000 and c.active_heartbeat_ms==1000);assert(c.offline_stop_ms==10000 and c.idle_timeout_ms==75000) end)

test("disabled network has no I/O",function() assert(not network.start({}, {enabled=false})) end)
test("remote DRAIN executes once and disconnect stops the owned drain",function()
    local f=fixture();f.connect();f.offer("DRAIN");f.execute("DRAIN");f.execute("DRAIN")
    assert(#f.calls==1 and f.calls[1]=="DRAIN")
    assert(f.last().ack.result=="OK DRAIN drain_started")
    f.callbacks.close();assert(f.calls[2]=="STOP" and f.state=="IDLE")
end)
test("plaintext and old HTTP URL rejected",function() local f=fixture();f.config.url="https://example.test/water";assert(not f.start()) end)
test("missing CA prevents connection",function() local f=fixture();f.no_cert=true;assert(not f.start());assert(not f.started) end)

test("SMS-compatible default connects without reading a CA file",function()
    assert(require("water_network_config").long_connection_cert==nil)
    local f=fixture();f.config.long_connection_cert=nil
    f.deps.read_cert=function() error("no certificate should be read") end
    f.connect();assert(f.started)
end)

test("empty or SNI-only certificate options do not require a CA",function()
    for _,cert in ipairs({{}, {hostNameFlag=1}}) do
        local f=fixture();f.config.long_connection_cert=cert
        f.deps.read_cert=function() error("CA absent") end
        f.connect();assert(f.started)
    end
end)

test("explicit certificate file read errors never downgrade verification",function()
    local f=fixture();f.deps.read_cert=function(name) assert(name=="water-ca.crt");error("read failure") end
    local ok,reason=f.start();assert(not ok and reason=="network_ca_missing" and not f.started)
end)

test("malformed optional certificate settings fail before transport starts",function()
    for _,cert in ipairs({false,"water-ca.crt",{caCert=false},{caCert="../ca.crt"},
        {caCert=""},{clientKey=5},{clientCert="/lua/client.crt"},{insist=2},{hostNameFlag=true},{clientPassword=42}}) do
        local f=fixture();f.config.long_connection_cert=cert
        local ok,reason=f.start();assert(not ok and reason=="network_cert_config_invalid" and not f.started)
    end
end)

test("connection budget accepts bounded seconds expressed in milliseconds",function()
    assert(require("water_network_config").tls_connect_timeout_ms==60000)
    for _,ms in ipairs({15000,30000,60000,120000}) do
        local f=fixture();f.config.tls_connect_timeout_ms=ms;assert(f.start())
    end
    for _,ms in ipairs({0,60,14999,15500,121000,"60000",false,0/0,math.huge}) do
        local f=fixture();f.config.tls_connect_timeout_ms=ms
        local ok,reason=f.start()
        assert(not ok and reason=="network_connect_timeout_invalid" and not f.started)
    end
end)

test("long connection budget never extends active output communication deadline",function()
    local f=fixture();f.config.tls_connect_timeout_ms=120000
    f.connect();f.offer("FILL");f.execute("FILL")
    f.advance(9995);assert(f.state=="FILLING" and not f.closed)
    f.advance(5);assert(f.closed and f.state=="IDLE" and f.calls[2]=="STOP")
end)
test("auth key has a fixed nonsecret prefix",function() local f=fixture();f.connect();assert(f.sent[1]:find('{"type":"auth","protocol":"water-ws-v1","key"',1,true)==1) end)
test("idle heartbeat follows one second boundaries",function() local f=fixture();f.connect();for i=1,5 do local n=#f.sent;f.advance(500);assert(#f.sent==n);f.advance(500);assert(#f.sent==n+1 and f.last().type=="ping");f.reply({type="pong",seq=f.last().seq}) end end)

test("Air724 200 raw ticks produce a one second heartbeat",function()
    local f=fixture();f.connect();local n=#f.sent
    f.tick=199;f.step();assert(#f.sent==n)
    f.tick=200;f.step();assert(#f.sent==n+1 and f.last().type=="ping")
end)

test("delayed cellular pong remains valid after the next ping",function()
    local f=fixture();f.connect();f.offer("START");f.execute("START")
    f.advance(1000);local previous=f.last().seq
    for i=1,15 do
        f.advance(1000);local latest=f.last().seq
        f.reply({type="pong",seq=previous});previous=latest
    end
    assert(not f.closed and #f.calls==1)
end)

test("expired and duplicate pongs cannot renew active deadline",function()
    local f=fixture();f.connect();f.offer("START");f.execute("START")
    f.advance(1000);local seq=f.last().seq;f.reply({type="pong",seq=seq})
    f.advance(8000);f.reply({type="pong",seq=seq})
    f.advance(2000);assert(f.closed and f.calls[2]=="STOP")
    local g=fixture();g.connect();g.offer("START");g.execute("START")
    g.advance(1000);seq=g.last().seq
    -- Deliver the expired reply before the watchdog next runs.
    g.tick=g.tick+2000;g.reply({type="pong",seq=seq});g.step()
    assert(g.closed and g.calls[2]=="STOP")
end)

test("a pong from a previous connection cannot renew deadline",function()
    local f=fixture();f.connect();f.advance(1000);local seq=f.last().seq
    f.callbacks.close();f.closed=false;f.callbacks.open();f.reply({type="ready",session=string.rep("c",32)})
    f.advance(74000);f.reply({type="pong",seq=seq});f.advance(1000);assert(f.closed)
end)
test("state changes are immediately reported",function() local f=fixture();f.connect();f.state="FAULT";f.advance(500);assert(f.last().type=="status") end)
test("offer alone cannot start outputs",function() local f=fixture();f.connect();f.offer("START");assert(f.last().type=="claim" and #f.calls==0) end)
test("claim execute and ack completes",function() local f=fixture();f.connect();f.offer("START");f.execute("START");assert(f.calls[1]=="START");assert(f.last().ack.status=="succeeded") end)
test("expired execute cannot start",function() local f=fixture();f.connect();f.offer("START");f.advance(1000);f.execute("START",500);assert(#f.calls==0 and f.last().ack.status=="rejected") end)
test("unsolicited execute ignored",function() local f=fixture();f.connect();f.execute("START");assert(#f.calls==0) end)
test("duplicate command never repeats",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.offer("START");f.execute("START");assert(#f.calls==1) end)
test("USB stop invalidates pending execute",function() local f=fixture();f.connect();f.offer("START");f.controller.stop();f.execute("START");assert(#f.calls==1 and f.closed) end)
test("STOP offer invalidates prior claim",function() local f=fixture();f.connect();f.offer("START");f.offer("STOP",string.rep("c",32));f.execute("START");assert(#f.calls==0);f.execute("STOP",8000,string.rep("c",32));assert(f.calls[1]=="STOP") end)
test("disconnect stops owned cycle",function() local f=fixture();f.connect();f.offer("START");f.execute("START");f.callbacks.close();assert(f.calls[2]=="STOP") end)

test("reconnection does not resume manual outputs or execute old pending commands",function()
    for _,command in ipairs({"FILL","DRAIN"}) do
        local f=fixture();f.connect();f.offer(command);f.execute(command)
        f.offer(command,string.rep("d",32));f.callbacks.close()
        assert(f.state=="IDLE" and f.calls[2]=="STOP")
        f.callbacks.open();f.reply({type="ready",session=string.rep("c",32)})
        f.execute(command,8000,string.rep("d",32))
        f.advance(1000);f.reply({type="pong",seq=f.last().seq})
        assert(#f.calls==2 and f.state=="IDLE")
    end
end)
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
