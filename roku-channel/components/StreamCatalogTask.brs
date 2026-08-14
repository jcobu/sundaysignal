sub init()
    m.top.functionName = "getCatalog"
end sub

sub getCatalog()
    m.top.error = ""
    m.top.statusCode = 0
    m.top.result = []

    url = m.top.catalogUri
    if url = invalid or len(url) = 0 then
        m.top.error = "No catalog URL"
        return
    end if

    xfer = CreateObject("roUrlTransfer")
    if xfer = invalid then
        m.top.error = "roUrlTransfer unavailable"
        return
    end if

    xfer.SetUrl(url)
    xfer.EnableFreshConnection(true)
    xfer.RetainBodyOnError(true)
    xfer.SetRequest("GET")
    xfer.AddHeader("Accept", "application/json, text/plain, */*")
    xfer.AddHeader("Connection", "close")

    port = CreateObject("roMessagePort")
    xfer.SetMessagePort(port)

    if not xfer.AsyncGetToString() then
        m.top.error = "Could not start HTTP request"
        return
    end if

    timedOut = true
    body = ""
    code = 0
    reason = ""
    for i = 1 to 10
        msg = wait(1000, port)
        if msg <> invalid and type(msg) = "roUrlEvent" then
            timedOut = false
            code = msg.GetResponseCode()
            body = msg.GetString()
            reason = msg.GetFailureReason()
            m.top.statusCode = code
            exit for
        end if
    end for

    if timedOut then
        m.top.error = "Timeout reaching SundaySignal"
        return
    end if

    if code <= 0 then
        if reason = invalid or reason = "" then reason = "connection failed"
        m.top.error = "Network error: " + reason
        return
    end if

    if code <> 200 or body = invalid or len(body) < 2 then
        m.top.error = "HTTP " + code.ToStr()
        return
    end if

    payload = ParseJson(body)
    if payload = invalid then
        m.top.error = "Invalid JSON"
        return
    end if

    m.top.result = transformPayload(payload, url)
end sub

function transformPayload(payload as Object, catalogUrl as String) as Object
    out = []
    colors = ["0x355B53FF", "0x70413CFF", "0x876642FF", "0x255064FF", "0x6E6746FF", "0x684D68FF", "0x3D4A6BFF", "0x5A3A4AFF"]
    colorIdx = 0
    base = serverBaseFrom(catalogUrl)

    if payload.games <> invalid then
        for each g in payload.games
            gameTitle = "Game"
            if g.title <> invalid then gameTitle = g.title
            if g.streams <> invalid then
                for each s in g.streams
                    playUrl = absolutePlayUrl(s, base)
                    if playUrl <> "" then
                        name = "Stream"
                        if s.name <> invalid then name = s.name
                        meta = "HLS · LIVE"
                        if s.badges <> invalid and s.badges.Count() > 0 then
                            meta = "HLS · " + s.badges[0]
                        end if
                        entry = {}
                        entry.title = gameTitle
                        entry.category = "NFL"
                        entry.meta = meta
                        entry.description = name + " — " + gameTitle
                        entry.color = colors[colorIdx mod colors.Count()]
                        entry.live = true
                        entry.url = playUrl
                        entry.format = "hls"
                        entry.streamName = name
                        out.Push(entry)
                        colorIdx = colorIdx + 1
                    end if
                end for
            end if
        end for
        return out
    end if

    if payload.streams <> invalid then
        for each item in payload.streams
            out.Push(item)
        end for
    end if
    return out
end function

function serverBaseFrom(catalogUrl as String) as String
    if left(catalogUrl, 7) = "http://" then
        rest = mid(catalogUrl, 8)
        slash = Instr(1, rest, "/")
        if slash = 0 then return catalogUrl
        return "http://" + left(rest, slash - 1)
    else if left(catalogUrl, 8) = "https://" then
        rest = mid(catalogUrl, 9)
        slash = Instr(1, rest, "/")
        if slash = 0 then return catalogUrl
        return "https://" + left(rest, slash - 1)
    end if
    return ""
end function

function absolutePlayUrl(s as Object, base as String) as String
    if s.play_url <> invalid and s.play_url <> "" then
        pu = s.play_url
        if left(pu, 4) = "http" then return pu
        if left(pu, 1) = "/" then return base + pu
        return base + "/" + pu
    end if
    if s.media_url <> invalid and s.media_url <> "" then
        return base + "/proxy?url=" + encodeUriComponent(s.media_url)
    end if
    if s.url <> invalid and s.url <> "" then
        return s.url
    end if
    return ""
end function

function encodeUriComponent(s as String) as String
    out = ""
    hexChars = "0123456789ABCDEF"
    for i = 0 to len(s) - 1
        ch = mid(s, i + 1, 1)
        code = asc(ch)
        if (code >= 48 and code <= 57) or (code >= 65 and code <= 90) or (code >= 97 and code <= 122) or ch = "-" or ch = "_" or ch = "." or ch = "~" then
            out = out + ch
        else if ch = " " then
            out = out + "%20"
        else
            hi = int(code / 16)
            lo = code - (hi * 16)
            out = out + "%" + mid(hexChars, hi + 1, 1) + mid(hexChars, lo + 1, 1)
        end if
    end for
    return out
end function
