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
    local sys={taskInit=function(fn) co=coroutine.create(fn);return co end,publish=function() end,wait=function(ms) coroutine.yield("WAIT",ms) end}
    local client
    client=ws.new({url="wss://example.test/water/api/device/ws",device_key=string.rep("k",32),ca_cert="water-ca.crt"},{
        open=function() opened=true;assert(client:send("queued")) end,
        message=function(value) received=value end,close=function() closed=true end},{sys=sys,
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
print("water_ws_transport: "..count.." tests passed")
