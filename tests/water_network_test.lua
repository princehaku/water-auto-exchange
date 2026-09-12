local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local network = require "water_network"
local count = 0
local function test(name, fn) fn(); count=count+1; print("PASS "..name) end
local function fixture()
    local f = {tick=0, sent={}, kinds={}, calls={}, state="IDLE", decoded={}}
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
        send=function(_,value,kind) f.sent[#f.sent+1]=value;f.kinds[#f.kinds+1]=kind; return not f.send_fail end,
        close=function() f.closed=true end,
        start=function() f.started=true end
    }
    f.deps={tick=function() return f.tick end,
        sys={timerLoopStart=function(fn) f.step=fn; return 1 end},
        transport={new=function(_,callbacks) f.callbacks=callbacks; return f.client end},
        json={encode=function(value) return value end,decode=function() if f.decode_fail then error("decode") end; return f.decoded end},
        usb={format_status=function() return "project=water_auto_exchange version=0.8.0 state="..f.state end},
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

test("shipped defaults save idle traffic while keeping active watchdog",function() local c=require "water_network_config";assert(c.heartbeat_ms==30000 and c.active_heartbeat_ms==1000);assert(c.traffic_interval_s==300);assert(c.offline_stop_ms==10000 and c.idle_timeout_ms==75000) end)

test("thirty second idle heartbeat keeps one connection with twenty pings in ten minutes",function()
    local f=fixture();f.config.heartbeat_ms=30000;f.connect()
    for _=1,20 do
        local n=#f.sent;f.advance(29500);assert(#f.sent==n)
        f.advance(500);assert(#f.sent==n+1 and f.last().type=="ping")
        f.reply({type="pong",seq=f.last().seq})
    end
    assert(not f.closed and #f.sent==21 and #f.calls==0)
end)

test("commands after long idle execute immediately then use one second active heartbeat",function()
    for _,command in ipairs({"FILL","DRAIN"}) do
        local f=fixture();f.config.heartbeat_ms=30000;f.connect();f.advance(25000)
        assert(#f.sent==1)
        f.offer(command);assert(f.last().type=="claim")
        f.execute(command);assert(f.calls[1]==command and f.last().ack.status=="succeeded")
        -- No new idle ping was needed to receive/execute the command.
        f.advance(500);assert(f.last().type=="ping" and not f.closed)
        f.reply({type="pong",seq=f.last().seq})
        for _=1,3 do
            local n=#f.sent;f.advance(500);assert(#f.sent==n)
            f.advance(500);assert(#f.sent==n+1 and f.last().type=="ping")
            f.reply({type="pong",seq=f.last().seq})
        end
        f.advance(9995);assert(not f.closed and #f.calls==1)
        f.advance(5);assert(f.closed and f.calls[2]=="STOP" and f.state=="IDLE")
    end
end)

test("STOP restores idle heartbeat and fault telemetry does not wait thirty seconds",function()
    local f=fixture();f.config.heartbeat_ms=30000;f.connect();f.offer("FILL");f.execute("FILL")
    f.advance(1000);f.reply({type="pong",seq=f.last().seq})
    f.offer("STOP",string.rep("c",32));f.execute("STOP",8000,string.rep("c",32))
    local n=#f.sent;f.advance(29500);assert(#f.sent==n and f.state=="IDLE")
    f.advance(500);assert(f.last().type=="ping");f.reply({type="pong",seq=f.last().seq})
    f.state="FAULT";f.advance(500);assert(f.last().type=="status")
end)

test("five minute accounting never extends the seventy five second idle deadline",function()
    local f=fixture();f.config.heartbeat_ms=30000;f.config.traffic_interval_s=300;f.connect()
    f.advance(30000);f.advance(30000);f.advance(14995);assert(not f.closed)
    f.advance(5);assert(f.closed and #f.calls==0)
end)

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

test("send budget is bounded independently from the ten second active stop",function()
    assert(require("water_network_config").send_timeout_ms==30000)
    for _,ms in ipairs({5000,30000,60000}) do
        local f=fixture();f.config.send_timeout_ms=ms
        f.connect();f.offer("FILL");f.execute("FILL")
        f.advance(9995);assert(f.state=="FILLING" and not f.closed)
        f.advance(5);assert(f.state=="IDLE" and f.calls[2]=="STOP" and f.closed)
    end
    for _,ms in ipairs({0,30,4999,5500,61000,"30000",false,0/0,math.huge}) do
        local f=fixture();f.config.send_timeout_ms=ms
        local ok,reason=f.start();assert(not ok and reason=="network_send_timeout_invalid" and not f.started)
    end
end)

test("transport metadata identifies auth heartbeat claim and ack without inspecting JSON",function()
    local f=fixture();f.connect();assert(f.kinds[1]=="auth")
    f.advance(1000);assert(f.kinds[#f.kinds]=="ping")
    f.offer("FILL");assert(f.kinds[#f.kinds]=="claim")
    f.execute("FILL");assert(f.kinds[#f.kinds]=="ack")
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
test("authentication budget tolerates delayed ready but is still bounded",function()
    local f=fixture();assert(f.start());f.callbacks.open()
    f.advance(15000);assert(not f.closed)
    f.reply({type="ready",session=string.rep("a",32)})
    f.offer("FILL");f.execute("FILL");f.advance(10000)
    assert(f.closed and f.calls[2]=="STOP")
    local g=fixture();assert(g.start());g.callbacks.open();g.advance(29995);assert(not g.closed)
    g.advance(5);assert(g.closed and #g.calls==0)
    for _,value in ipairs({false,0,"30000",5500,61000,math.huge,0/0}) do
        local h=fixture();h.config.auth_timeout_ms=value
        local ok,reason=h.start();assert(not ok and reason=="network_auth_timeout_invalid")
    end
end)

local function metered_fixture()
    local f=fixture();f.config.traffic_enabled=true;f.config.traffic_interval_s=60
    f.deps.sys.subscribe=function(event,callback) assert(event=="LIB_IP_STATIS_RPT");f.report_flow=callback end
    f.deps.socket={setIpStatis=function(interval) f.accounting_interval=interval;f.accounting_started=true end}
    function f.traffic_messages()
        local result={}
        for _,token in ipairs(f.sent) do
            local item=f.messages[token]
            if type(item)=="table" and item.type=="traffic" then result[#result+1]=item end
        end
        return result
    end
    return f
end

test("five minute accounting samples and reports once while heartbeats keep running",function()
    local f=metered_fixture();f.config.traffic_interval_s=nil;f.config.heartbeat_ms=30000;f.connect()
    assert(f.accounting_interval==300)
    for _=1,10 do
        f.advance(30000);f.reply({type="pong",seq=f.last().seq})
        assert(#f.traffic_messages()==0)
    end
    f.report_flow(2400);f.step();local samples=f.traffic_messages()
    assert(#samples==1 and samples[1].interval_seconds==300 and samples[1].total_bytes==2400)
    for _=1,10 do f.advance(30000);f.reply({type="pong",seq=f.last().seq}) end
    assert(#f.traffic_messages()==1)
    f.report_flow(2300);f.step();samples=f.traffic_messages()
    assert(#samples==2 and samples[2].interval_seconds==300 and samples[2].total_bytes==4700)
    assert(not f.closed and #f.calls==0)
end)

test("accounting interval rejects invalid values before any network I/O",function()
    for _,seconds in ipairs({false,0,59,300.5,3601,"300",math.huge,0/0}) do
        local f=metered_fixture();f.config.traffic_interval_s=seconds
        local ok,reason=f.start();assert(not ok and reason=="network_traffic_interval_invalid")
        assert(not f.started and not f.accounting_started)
    end
    for _,seconds in ipairs({60,300,3600}) do
        local f=metered_fixture();f.config.traffic_interval_s=seconds;f.connect()
        assert(f.accounting_interval==seconds)
    end
end)

test("IP accounting reports cumulative bytes only after a sample and authenticated ready",function()
    local f=metered_fixture();f.connect();assert(f.accounting_started)
    f.advance(59000);assert(#f.traffic_messages()==0)
    f.advance(1000);f.report_flow(1024);f.step()
    local samples=f.traffic_messages();assert(#samples==1)
    assert(samples[1].total_bytes==1024 and samples[1].interval_bytes==1024 and samples[1].interval_seconds==60)
    for _=1,10 do f.step() end
    assert(#f.traffic_messages()==1 and #f.calls==0)
end)

test("accounting survives reconnect and offline samples without resetting meter id",function()
    local f=metered_fixture();f.connect();f.advance(60000);f.report_flow(1024);f.step()
    f.callbacks.close();f.closed=false
    f.advance(60000);f.report_flow(2048);f.step()
    assert(#f.traffic_messages()==1)
    f.callbacks.open();f.reply({type="ready",session=string.rep("c",32)});f.step()
    local samples=f.traffic_messages();assert(#samples==2)
    assert(samples[2].meter==samples[1].meter and samples[2].total_bytes==3072 and samples[2].interval_seconds==60)
end)

test("unavailable accounting never prevents network start and invalid samples are ignored",function()
    local f=metered_fixture();f.deps.socket={};f.connect();assert(f.started and not f.accounting_started)
    local g=metered_fixture();g.connect()
    for _,bytes in ipairs({-1,false,"100",0/0,math.huge,1.5}) do g.report_flow(bytes) end
    g.step();assert(#g.traffic_messages()==0)
end)

test("healthy heartbeats keep one authenticated connection open for ten minutes",function()
    local f=fixture();f.connect()
    for _=1,600 do f.advance(1000);f.reply({type="pong",seq=f.last().seq}) end
    assert(not f.closed and #f.calls==0)
    local auths=0;for _,kind in ipairs(f.kinds) do if kind=="auth" then auths=auths+1 end end
    assert(auths==1)
end)

test("reconnect auth carries the previous server session without replaying a command",function()
    local f=fixture();f.connect()
    assert(not f.sent[1]:find('"previous_session"',1,true))
    f.offer("FILL");f.execute("FILL");f.callbacks.close();assert(f.calls[2]=="STOP")
    f.callbacks.open()
    local auth=f.sent[#f.sent];assert(auth:find('"previous_session"',1,true))
    local token=auth:match('"previous_session":([^}]+)')
    assert(f.messages[token]==string.rep("a",32))
    f.reply({type="ready",session=string.rep("c",32)})
    f.callbacks.close();f.callbacks.open()
    token=f.sent[#f.sent]:match('"previous_session":([^}]+)')
    assert(f.messages[token]==string.rep("c",32) and #f.calls==2)
end)

test("simultaneous outputs keep active heartbeats and partial OFF does not release remote ownership",function()
    for _, stop_one in ipairs({"FILL_OFF","DRAIN_OFF"}) do
        local f=fixture();f.config.heartbeat_ms=30000
        local fill,drain=false,false
        local function state() f.state=fill and drain and "EXCHANGING" or (fill and "FILLING" or (drain and "DRAINING" or "IDLE")) end
        f.controller.fill=function() fill=true;state();return true,"fill_started" end
        f.controller.drain=function() drain=true;state();return true,"drain_started" end
        f.controller.fill_off=function() fill=false;state();return true,"fill_stopped" end
        f.controller.drain_off=function() drain=false;state();return true,"drain_stopped" end
        f.connect();f.offer("FILL");f.execute("FILL")
        f.offer("DRAIN",string.rep("c",32));f.execute("DRAIN",8000,string.rep("c",32))
        assert(f.state=="EXCHANGING")
        for _=1,12 do f.advance(1000);assert(f.last().type=="ping");f.reply({type="pong",seq=f.last().seq}) end
        assert(not f.closed)
        f.offer(stop_one,string.rep("d",32));f.execute(stop_one,8000,string.rep("d",32))
        assert(f.state==(stop_one=="FILL_OFF" and "DRAINING" or "FILLING"))
        f.advance(9995);assert(not f.closed)
        f.advance(5);assert(f.closed and f.calls[1]=="STOP")
    end
end)

test("partial OFF invalidates only its pending ON and leaves the other command deliverable",function()
    local f=fixture();f.controller.fill_off=function() return true,"fill_stopped" end;f.connect()
    f.offer("FILL");f.offer("DRAIN",string.rep("c",32));f.offer("FILL_OFF",string.rep("d",32))
    f.execute("FILL_OFF",8000,string.rep("d",32));f.execute("FILL")
    assert(#f.calls==0)
    f.execute("DRAIN",8000,string.rep("c",32));assert(f.calls[1]=="DRAIN")
    f.callbacks.close();assert(f.calls[2]=="STOP")
    f.callbacks.open();f.reply({type="ready",session=string.rep("e",32)})
    f.execute("FILL");f.execute("DRAIN",8000,string.rep("c",32));assert(#f.calls==2)
end)

test("LED observers get authenticated connection and fresh heartbeat replies only",function()
    local f=fixture();local beats,connections=0,{}
    f.deps.on_heartbeat=function() beats=beats+1 end
    f.deps.on_connection=function(value) connections[#connections+1]=value end
    assert(f.start());f.callbacks.open();assert(#connections==0)
    f.reply({type="ready",session=string.rep("a",32)});assert(connections[1]==true)
    f.reply({type="received"});assert(beats==0)
    f.advance(1000);local seq=f.last().seq;assert(beats==0)
    f.reply({type="pong",seq=-1});assert(beats==0)
    f.reply({type="pong",seq=seq});f.reply({type="pong",seq=seq});assert(beats==1)
    f.controller.stop();assert(connections[2]==false)
    f.callbacks.open();f.reply({type="ready",session=string.rep("c",32)})
    f.reply({type="pong",seq=seq});assert(beats==1)
    f.deps.on_heartbeat=function() error("LED unavailable") end
    f.advance(1000);f.reply({type="pong",seq=f.last().seq});f.closed=false
    f.advance(1000);assert(not f.closed)
    f.deps.on_connection=function() error("LED unavailable") end
    f.callbacks.close();assert(f.closed)
end)

print("water_network: "..count.." tests passed")
