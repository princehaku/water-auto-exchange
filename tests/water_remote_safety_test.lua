-- Real controller + WSS protocol + yielding socket transport, simulated GPIO.
local root=(arg and arg[1]) or "."
package.path=root.."/src/?.lua;"..package.path
local water,network,transport=require "water_control",require "water_network",require "water_ws_transport"
local count=0
local function test(name,fn) fn();count=count+1;print("PASS "..name) end
local function frame(value) assert(#value<126);return string.char(129,#value)..value end
local function fixture()
    local h={tick=0,timers={},next_timer=0,levels={},opened={},messages={},serial=0,sent={}}
    local control_sys={
        timerStart=function(callback,delay)
            h.next_timer=h.next_timer+1
            h.timers[h.next_timer]={callback=callback,due=h.tick+delay/5}
            return h.next_timer
        end,
        timerStop=function(id)
            for key,timer in pairs(h.timers) do if key==id or timer.callback==id then h.timers[key]=nil end end
        end
    }
    local cfg=dofile(root.."/src/water_config.lua")
    h.controller=water.new(cfg,{sys=control_sys,tick=function() return h.tick end,emit=function() end,
        pins={setup=function(gpio,value) h.opened[gpio]=true;h.levels[gpio]=value;return function() end end,
            close=function(gpio) h.opened[gpio]=false;h.levels[gpio]=nil end},
        pio={pin={setval=function(value,gpio) assert(h.opened[gpio]);h.levels[gpio]=value end,
            getval=function() error("manual mode must not read sensors") end}}})
    function h.run(ms)
        local target=h.tick+ms/5
        while true do
            local key,due
            for id,timer in pairs(h.timers) do
                if timer.due<=target and (not due or timer.due<due) then key,due=id,timer.due end
            end
            if not key then break end
            h.tick=due;local timer=h.timers[key];h.timers[key]=nil;timer.callback()
        end
        h.tick=target
    end
    assert(h.controller.init());h.run(500);assert(h.controller.status().ready)
    local netcfg=dofile(root.."/src/water_network_config.lua")
    netcfg.enabled,netcfg.device_key,netcfg.traffic_enabled=true,string.rep("k",32),false
    netcfg.url="wss://example.test/water/api/device/ws"
    local sys={timerLoopStart=function(callback,ms) assert(ms==500);h.step=callback;return 999 end,
        subscribe=function() end,publish=function() end,
        taskInit=function(callback) h.co=coroutine.create(callback);return h.co end,
        wait=function(ms) return coroutine.yield("WAIT",ms) end}
    _G.rtos={tick=function() return h.tick end}
    _G.PROJECT,_G.VERSION="water_auto_exchange","0.8.3"
    local function encode(value)
        h.serial=h.serial+1;local key="m"..h.serial;h.messages[key]=value;return key
    end
    local socket_io={
        connect=function() return coroutine.yield("CONNECT") end,
        send=function(_,value,seconds) h.sent[#h.sent+1]=value;h.send_seconds=seconds;return coroutine.yield("SEND") end,
        recv=function() return coroutine.yield("RECV") end,
        close=function() return coroutine.yield("CLOSE") end
    }
    assert(network.start(h.controller,netcfg,{sys=sys,tick=function() return h.tick end,
        json={encode=encode,decode=function(value) return h.messages[value] end},
        transport={new=function(config,callbacks)
            h.callbacks=callbacks
            h.client=transport.new(config,callbacks,{sys=sys,link={shut=function() end},
                net={getState=function() return "REGISTERED" end},ril={request=function() end},
                socket={isReady=function() return true end,tcp=function() return socket_io end},
                crypto={sha1=function() return string.rep("0",40) end,base64_encode=function(_,size) return "base64_"..size end}})
            return h.client
        end}}))
    function h.resume(expected,...)
        local ok,phase=coroutine.resume(h.co,...);assert(ok,tostring(phase));assert(phase==expected,tostring(phase))
    end
    function h.deliver(value,expected) h.resume(expected or "RECV",true,frame(encode(value))) end
    function h.command(command,id)
        id=id or string.rep("b",32)
        h.deliver({type="offer",command=command,id=id},"SEND");h.resume("RECV",true)
        h.deliver({type="execute",command=command,id=id,ttl_ms=8000},"SEND")
    end
    function h.pong()
        h.step()
        local seq
        for i=1,h.serial do local value=h.messages["m"..i];if type(value)=="table" and value.type=="ping" then seq=value.seq end end
        assert(seq);h.deliver({type="pong",seq=seq},"SEND");h.resume("RECV",true)
    end
    h.resume("CONNECT");h.resume("SEND",true);h.resume("RECV",true)
    h.resume("SEND",true,"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: base64_20\r\n\r\n")
    h.resume("RECV",true)
    h.deliver({type="ready",session=string.rep("a",32),soft_limits={version=1,fill_seconds=180,drain_seconds=300,watchdog_ms=5000}})
    return h
end

test("yielding thirty second socket send cannot postpone five second physical OFF",function()
    for _,command in ipairs({"FILL","DRAIN"}) do
        local h=fixture();h.command(command)
        assert(h.send_seconds==30 and coroutine.status(h.co)=="suspended")
        h.run(4995);assert(h.controller.status()[command:lower()])
        h.run(5)
        local s=h.controller.status();assert(s.state=="FAULT" and s.reason=="communication_timeout")
        assert(s.outputs_known and not s.fill and not s.drain and not h.opened[23] and not h.opened[5])
        -- The socket reports a late success; the control fault remains latched.
        h.run(25000);h.resume("RECV",true);h.step()
        assert(h.controller.status().state=="FAULT")
        assert(not h.controller.remote_command(command:lower()))
    end
end)

test("real network and controller accept normal server timeout then immediately start a fresh run",function()
    for _,timeout in ipairs({"FILL_TIMEOUT","DRAIN_TIMEOUT"}) do
        local h=fixture();h.command("FILL");h.resume("RECV",true)
        h.command("DRAIN",string.rep("c",32));h.resume("RECV",true)
        for _=1,301 do h.run(1000);h.pong();assert(h.controller.status().state=="EXCHANGING") end
        h.command(timeout,string.rep("d",32));h.resume("RECV",true)
        local s=h.controller.status();assert(s.state=="IDLE" and s.reason==timeout:lower())
        assert(s.outputs_known and not s.fill and not s.drain)
        h.command("FILL",string.rep("e",32));h.resume("RECV",true)
        assert(h.controller.status().state=="FILLING")
        h.run(1000);h.pong();assert(h.controller.status().fill)
    end
end)

test("real disconnect releases both outputs immediately and old connection cannot restore them",function()
    local h=fixture();h.command("FILL");h.resume("RECV",true)
    h.command("DRAIN",string.rep("c",32));h.resume("RECV",true)
    local stale=string.rep("e",32)
    h.callbacks.message((function()
        h.serial=h.serial+1;local key="m"..h.serial;h.messages[key]={type="offer",command="FILL",id=stale};return key
    end)())
    h.callbacks.close()
    local s=h.controller.status();assert(s.outputs_known and not s.fill and not s.drain)
    h.serial=h.serial+1;local key="m"..h.serial
    h.messages[key]={type="execute",command="FILL",id=stale,ttl_ms=8000};h.callbacks.message(key)
    h.run(5000);assert(not h.controller.status().fill and not h.controller.status().drain)
end)

test("network callback winning the five second timer race still latches communication fault",function()
    local h=fixture();h.command("FILL");h.resume("RECV",true)
    -- Advance the clock without dispatching the controller's due poll first.
    h.tick=h.tick+1000;h.step()
    local s=h.controller.status()
    assert(s.state=="FAULT" and s.reason=="communication_timeout")
    assert(s.outputs_known and not s.fill and not s.drain and not h.opened[23] and not h.opened[5])
    h.run(1000);assert(h.controller.status().state=="FAULT")
end)

test("a consumed timeout execute cannot close the next run and an existing true fault remains latched",function()
    local h=fixture();h.command("FILL");h.resume("RECV",true)
    local timeout_id=string.rep("c",32)
    h.command("FILL_TIMEOUT",timeout_id);h.resume("RECV",true)
    assert(h.controller.status().state=="IDLE")
    h.command("DRAIN",string.rep("d",32));h.resume("RECV",true)
    h.deliver({type="execute",command="FILL_TIMEOUT",id=timeout_id,ttl_ms=8000})
    assert(h.controller.status().state=="DRAINING")
    h.run(5000);assert(h.controller.status().reason=="communication_timeout")
    h.command("DRAIN_TIMEOUT",string.rep("e",32));h.resume("RECV",true)
    local s=h.controller.status();assert(s.state=="FAULT" and s.reason=="communication_timeout")
    assert(not h.controller.remote_command("fill"))
end)

print("water_remote_safety: "..count.." tests passed")
