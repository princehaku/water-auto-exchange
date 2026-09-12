local root=(arg and arg[1]) or "."
package.path=root.."/src/?.lua;"..package.path
local ws=require "water_ws_transport"
local count=0
local function test(name,fn) fn();count=count+1;print("PASS "..name) end
local function frame(value,opcode,fin)
    local header=string.char((fin==false and 0 or 128)+(opcode or 1))
    if #value<126 then return header..string.char(#value)..value end
    return header..string.char(126,math.floor(#value/256),#value%256)..value
end
test("split header and payload preserved at every boundary",function()
    local value=frame(string.rep("x",127))
    for at=1,#value-1 do
        local parse=ws.parser();assert(#parse(value:sub(1,at))==0)
        local events=parse(value:sub(at+1));assert(#events==1 and events[1].data==string.rep("x",127))
    end
end)
test("126 byte payload decoded correctly",function() assert(ws.parser()(frame(string.rep("a",126)))[1].data==string.rep("a",126)) end)
test("coalesced messages all delivered",function() local e=ws.parser()(frame("one")..frame("two"));assert(#e==2 and e[2].data=="two") end)
test("continuation survives interleaved ping",function() local p=ws.parser();local e=p(frame("a",1,false)..frame("p",9)..frame("b",0));assert(#e==2 and e[1].opcode==9 and e[2].data=="ab") end)
test("masked server frame rejected",function() assert(not pcall(ws.parser(),string.char(129,128))) end)
test("large frames rejected",function() assert(not pcall(ws.parser(),string.char(129,127)));assert(not pcall(ws.parser(),string.char(129,126,33,0))) end)
test("RSV and invalid opcode rejected",function() assert(not pcall(ws.parser(),string.char(193,0)));assert(not pcall(ws.parser(),frame("",3))) end)
test("unexpected continuation rejected",function() assert(not pcall(ws.parser(),frame("x",0))) end)
test("oversized fragmented message rejected",function() local p=ws.parser();p(frame(string.rep("a",5000),1,false));assert(not pcall(p,frame(string.rep("b",5000),0))) end)
test("outgoing frame is masked at length boundaries",function()
    for _,size in ipairs({0,1,125,126,127,8192}) do
        local value=ws.frame(string.rep("x",size),1,string.char(0,0,0,0))
        local offset=size<126 and 6 or 8
        assert(value:byte(2)>=128 and value:sub(offset+1)==string.rep("x",size))
    end
end)
test("socket operations may yield in the owner coroutine",function()
    _G.rtos={tick=function() return 0 end}
    local co,sent,opened,received,closed=nil,{},false,false,false
    local io={
        connect=function() coroutine.yield("CONNECT");return true end,
        send=function(_,value) sent[#sent+1]=value;return coroutine.yield("SEND") end,
        recv=function() return coroutine.yield("RECV") end,
        close=function() coroutine.yield("CLOSE") end}
    local sys={subscribe=function() end,taskInit=function(fn) co=coroutine.create(fn);return co end,publish=function() end,wait=function(ms) coroutine.yield("WAIT",ms) end}
    local client
    client=ws.new({url="wss://example.test/water/api/device/ws",device_key=string.rep("k",32),ca_cert="water-ca.crt"},{
        open=function() opened=true;assert(client:send("queued")) end,
        message=function(value) received=value end,close=function() closed=true end},{sys=sys,
        link={shut=function() end},net={getState=function() return "REGISTERED" end},ril={request=function() end},
        socket={isReady=function() return true end,tcp=function(_,cert) assert(cert.insist==0 and cert.hostNameFlag==1);return io end},
        crypto={sha1=function() return string.rep("0",40) end,base64_encode=function(_,size) return "base64_"..size end}})
    local function resume(expected,...)
        local ok,value=coroutine.resume(co,...);assert(ok,tostring(value));assert(value==expected,tostring(value))
    end
    client:start();resume("CONNECT");resume("SEND");resume("RECV",true)
    resume("RECV",true,"HTTP/1.1 101 Switching Protocols\r\nUpgrade: web")
    resume("SEND",true,"socket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: base64_20\r\n\r\n"..frame("hello"))
    assert(opened and received=="hello" and #sent==2)
    resume("RECV",true);client:close();resume("CLOSE",false,"WATER_WS_WAKE");resume("WAIT")
    assert(closed)
    io.connect=function() error("injected owner failure") end
    local ok=coroutine.resume(co);assert(not ok and client:failed())
end)
local function timed_fixture(options)
    options=options or {}
    local f={tick=options.tick or 0,opened=false,ready=options.ready~=false,registered=options.registered or "REGISTERED",recoveries={},closed=0,queries={},subscriptions={}}
    _G.rtos={tick=function() return f.tick end}
    local io={connect=function() return coroutine.yield("CONNECT") end,
        send=function() return coroutine.yield("SEND") end,
        recv=function() return coroutine.yield("RECV") end,
        close=function() f.socket_closed=true;coroutine.yield("CLOSE") end}
    local sys={taskInit=function(fn) f.co=coroutine.create(fn);return f.co end,
        subscribe=function(event,fn) f.subscriptions[event]=fn end,
        publish=function() end,wait=function(ms) coroutine.yield("WAIT",ms) end}
    f.client=ws.new({url="wss://example.test/water/api/device/ws",device_key=string.rep("k",32),ca_cert="water-ca.crt"},
        {open=function() f.opened=true end,close=function() f.closed=f.closed+1 end},{sys=sys,
        link={shut=function()
            assert(not f.client.connected and #f.client.queue==0)
            if f.opened then assert(f.closed>0 and f.socket_closed) end
            f.recoveries[#f.recoveries+1]=f.tick
            f.ready=false;f.subscriptions.IP_ERROR_IND()
        end},
        net={getState=function() return f.registered end},
        ril={request=function(cmd,_,cb) f.queries[#f.queries+1]=cmd;if cb then f.diagnostic_done=cb end end},
        socket={isReady=function() return f.ready end,tcp=function() if not options.no_socket then return io end end},
        crypto={sha1=function() return string.rep("0",40) end,base64_encode=function(_,size) return "base64_"..size end}})
    function f.resume(expected,...)
        local ok,phase,value=coroutine.resume(f.co,...)
        assert(ok,tostring(phase));assert(phase==expected,tostring(phase));return value
    end
    function f.upgrade()
        f.resume("SEND",true);f.resume("RECV",true)
        f.resume("RECV",true,"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: base64_20\r\n\r\n")
        assert(f.opened)
    end
    f.client:start();f.resume((options.ready==false or options.no_socket) and "WAIT" or "CONNECT")
    return f
end

test("HTTP upgrade deadline is 2000 Air724 ticks",function()
    local f=timed_fixture();f.resume("SEND",true);f.resume("RECV",true)
    f.tick=1999;f.resume("RECV",false,"timeout")
    f.tick=2000;f.resume("CLOSE",false,"timeout")
    assert(f.resume("WAIT")==1000 and not f.opened)
end)

test("backoff resets only after 60 real seconds online",function()
    for _,ticks in ipairs({11999,12000}) do
        local f=timed_fixture();f.resume("CLOSE",false)
        assert(f.resume("WAIT")==1000);f.resume("CONNECT");f.upgrade()
        f.tick=ticks;f.resume("CLOSE",false,"closed")
        assert(f.resume("WAIT")==(ticks==12000 and 1000 or 2000))
    end
end)
test("retries indefinitely with capped exponential delays and throttled PDP recovery",function()
    local f=timed_fixture()
    for attempt,delay in ipairs({1000,2000,4000,8000,16000,32000,60000,60000,60000,60000,60000,60000,60000}) do
        f.resume("CLOSE",false);assert(f.closed==attempt)
        assert(f.resume("WAIT")==delay)
        if attempt==6 then assert(#f.recoveries==1) end
        if attempt==11 then assert(#f.recoveries==1) end
        if attempt==12 then assert(#f.recoveries==2) end
        if attempt==13 then assert(#f.recoveries==2) end
        f.tick=f.tick+delay/5;f.ready=true;f.resume("CONNECT")
    end
end)

test("no IP for 120 seconds recovers bearer once and enforces five minute cooldown",function()
    local f=timed_fixture({ready=false})
    f.tick=23999;f.resume("WAIT");assert(#f.recoveries==0)
    f.tick=24000;f.resume("WAIT");assert(#f.recoveries==1)
    f.tick=83999;f.resume("WAIT");assert(#f.recoveries==1)
    f.tick=84000;f.resume("WAIT");assert(#f.recoveries==2)
    f.ready=true;f.resume("CONNECT");f.upgrade()
end)

test("unregistered network waits without resetting radio or creating sockets",function()
    local f=timed_fixture({ready=false,registered="UNREGISTER"})
    for i=1,10 do f.tick=i*120000;assert(f.resume("WAIT")==1000) end
    assert(#f.recoveries==0 and not f.opened)
    f.registered="REGISTERED";f.resume("WAIT");assert(#f.recoveries==1)
    f.ready=true;f.resume("CONNECT");f.upgrade()
end)

test("diagnostic queries are read only rate limited and cannot pile up on a stalled AT channel",function()
    local f=timed_fixture({ready=false,registered="UNREGISTER"})
    assert(table.concat(f.queries,",")=="AT+CPIN?,AT+CREG?,AT+CGREG?,AT+CEREG?,AT+CSQ")
    for i=1,10 do f.tick=i*12000;f.resume("WAIT") end
    assert(#f.queries==5)
    f.diagnostic_done(nil,true,nil,"+CSQ: 11,99")
    for _=1,5 do f.resume("WAIT") end
    assert(#f.queries==10)
    f.diagnostic_done(nil,false,nil,nil)
    f.tick=f.tick+11999
    for _=1,5 do f.resume("WAIT") end
    assert(#f.queries==10)
    f.tick=f.tick+1
    for _=1,5 do f.resume("WAIT") end
    assert(#f.queries==15)
end)

test("IP loss immediately closes application session before blocking socket cleanup",function()
    local f=timed_fixture();f.upgrade();assert(f.client:send("old-command"))
    f.ready=false;f.subscriptions.IP_ERROR_IND()
    assert(f.closed==1 and not f.client.connected and #f.client.queue==0 and not f.socket_closed)
    f.resume("CLOSE",false,"closed");f.resume("WAIT")
    f.ready=true;f.resume("CONNECT");f.upgrade();assert(#f.client.queue==0)
end)

test("IP loss resets backoff only if the connection was already stable at loss",function()
    for _,ticks in ipairs({11999,12000}) do
        local f=timed_fixture();f.resume("CLOSE",false);f.resume("WAIT")
        f.resume("CONNECT");f.upgrade();f.tick=ticks
        f.subscriptions.IP_ERROR_IND();f.tick=f.tick+1000
        f.resume("CLOSE",false,"closed")
        assert(f.resume("WAIT")== (ticks==12000 and 1000 or 2000))
    end
end)

test("permanent stop never retries or recovers after pending connect returns",function()
    local f=timed_fixture();f.client:close(true);f.resume("CLOSE",false);f.resume(nil)
    assert(coroutine.status(f.co)=="dead" and not f.client:failed() and #f.recoveries==0)
end)

test("socket allocation failure follows retry policy instead of terminating task",function()
    local f=timed_fixture({no_socket=true})
    assert(f.closed==1);assert(f.resume("WAIT")==2000);assert(f.closed==2 and not f.client:failed())
end)

test("PDP timeout and recovery cooldown handle raw tick wrap",function()
    local f=timed_fixture({ready=false,tick=4294967000})
    f.tick=(f.tick+24000)%4294967296;f.resume("WAIT");assert(#f.recoveries==1)
    f.tick=(f.tick+59999)%4294967296;f.resume("WAIT");assert(#f.recoveries==1)
    f.tick=f.tick+1;f.resume("WAIT");assert(#f.recoveries==2)
end)

print("water_ws_transport: "..count.." tests passed")
