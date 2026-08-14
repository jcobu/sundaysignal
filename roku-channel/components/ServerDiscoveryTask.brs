sub init()
    m.top.functionName = "discoverServer"
end sub

sub discoverServer()
    m.top.resultUri = ""
    m.top.serverBase = ""
    m.top.error = ""
    m.top.scanned = 0

    candidates = []
    seen = {}

    ' The last successful server is almost always the fastest path.
    registry = CreateObject("roRegistrySection", "SundaySignal")
    if registry <> invalid and registry.Exists("serverBase") then
        addCandidate(candidates, seen, registry.Read("serverBase"))
    end if

    ' Friendly names are cheap to try before scanning the subnet.
    addCandidate(candidates, seen, "http://sundaysignal.local:8765")

    prefix = localIpv4Prefix()
    if prefix = "" then
        m.top.error = "Could not determine this Roku's LAN address"
        return
    end if

    ' Try common host addresses first, then cover the rest of the /24.
    preferred = [1, 2, 10, 20, 25, 50, 100, 150, 200, 207, 250, 254]
    for each host in preferred
        addCandidate(candidates, seen, "http://" + prefix + host.ToStr() + ":8765")
    end for
    for host = 1 to 254
        addCandidate(candidates, seen, "http://" + prefix + host.ToStr() + ":8765")
    end for

    found = scanCandidates(candidates)
    if found = "" then
        m.top.error = "No SundaySignal server found on " + prefix + "0/24"
        return
    end if

    if registry <> invalid then
        registry.Write("serverBase", found)
        registry.Flush()
    end if
    m.top.serverBase = found
    m.top.resultUri = found + "/api/streams"
end sub

sub addCandidate(candidates as Object, seen as Object, base as String)
    if base = invalid or base = "" then return
    key = LCase(base)
    if seen.DoesExist(key) then return
    seen[key] = true
    candidates.Push(base)
end sub

function localIpv4Prefix() as String
    info = CreateObject("roDeviceInfo")
    if info = invalid then return ""
    addresses = info.GetIPAddrs()
    if addresses = invalid then return ""
    for each interfaceName in addresses
        address = addresses[interfaceName]
        if address <> invalid and Instr(1, address, ".") > 0 and Left(address, 4) <> "127." then
            parts = address.Tokenize(".")
            if parts.Count() = 4 then
                return parts[0] + "." + parts[1] + "." + parts[2] + "."
            end if
        end if
    end for
    return ""
end function

function scanCandidates(candidates as Object) as String
    port = CreateObject("roMessagePort")
    batchSize = 24
    batchStart = 0

    while batchStart < candidates.Count()
        requests = {}
        batchEnd = batchStart + batchSize - 1
        if batchEnd >= candidates.Count() then batchEnd = candidates.Count() - 1

        for i = batchStart to batchEnd
            base = candidates[i]
            xfer = CreateObject("roUrlTransfer")
            if xfer <> invalid then
                xfer.SetMessagePort(port)
                xfer.SetUrl(base + "/api/health")
                xfer.EnableFreshConnection(true)
                xfer.RetainBodyOnError(true)
                xfer.AddHeader("Accept", "application/json")
                xfer.AddHeader("Connection", "close")
                if xfer.AsyncGetToString() then
                    requests[xfer.GetIdentity().ToStr()] = { transfer: xfer, base: base }
                    m.top.scanned = m.top.scanned + 1
                end if
            end if
        end for

        timer = CreateObject("roTimespan")
        timer.Mark()
        while timer.TotalMilliseconds() < 1200 and requests.Count() > 0
            msg = wait(50, port)
            if msg <> invalid and type(msg) = "roUrlEvent" then
                key = msg.GetSourceIdentity().ToStr()
                if requests.DoesExist(key) then
                    requestInfo = requests[key]
                    requests.Delete(key)
                    if msg.GetResponseCode() = 200 and isSundaySignal(msg.GetString()) then
                        return requestInfo.base
                    end if
                end if
            end if
        end while
        batchStart = batchEnd + 1
    end while
    return ""
end function

function isSundaySignal(body as String) as Boolean
    if body = invalid or body = "" then return false
    payload = ParseJson(body)
    if payload = invalid then return false
    if payload.service <> invalid and payload.service = "SundaySignal" then return true
    ' Compatibility with a server built before discovery_version was added.
    if payload.ok <> invalid and payload.playlist <> invalid then
        return payload.ok = true and payload.playlist = "/playlist.m3u"
    end if
    return false
end function
