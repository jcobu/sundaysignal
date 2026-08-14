sub init()
    m.top.setFocus(true)
    m.rail = m.top.findNode("streamRail")
    m.video = m.top.findNode("video")
    m.apiStatus = m.top.findNode("apiStatus")
    m.streamCount = m.top.findNode("streamCount")
    m.playOverlay = m.top.findNode("playOverlay")
    m.emptyState = m.top.findNode("emptyState")
    m.emptyTitle = m.top.findNode("emptyTitle")
    m.emptyDetail = m.top.findNode("emptyDetail")
    m.playerContent = m.top.findNode("playerContent")
    m.selected = 0
    m.playing = false
    m.streams = []
    if m.video <> invalid then m.video.observeField("state", "onVideoState")
    renderRail()
    showEmptyState("Finding SundaySignal", "Searching this network on port 8765…")
    ' A URI may still be supplied by a deep link or development harness.
    if m.top.catalogUri <> invalid and m.top.catalogUri <> "" then
        loadCatalog()
    else
        discoverServer()
    end if
end sub

sub discoverServer()
    if m.apiStatus <> invalid then m.apiStatus.text = "SEARCHING LOCAL NETWORK…"
    m.streams = []
    renderRail()
    showEmptyState("Finding SundaySignal", "Searching this network on port 8765…")

    if m.discoveryTask <> invalid then
        m.discoveryTask.unobserveField("state")
        m.discoveryTask.control = "STOP"
    end if
    task = CreateObject("roSGNode", "ServerDiscoveryTask")
    task.observeField("state", "onDiscoveryState")
    m.discoveryTask = task
    task.control = "RUN"
end sub

sub onDiscoveryState()
    task = m.discoveryTask
    if task = invalid then return
    st = task.state
    if st = "RUN" then return
    if st <> "DONE" and st <> "STOP" then return

    if task.resultUri <> invalid and task.resultUri <> "" then
        if m.apiStatus <> invalid then m.apiStatus.text = "SERVER FOUND · CONNECTING…"
        m.top.catalogUri = task.resultUri
        return
    end if

    detail = "Make sure Docker is running and both devices use the same Wi-Fi. Press * to search again."
    if task.error <> invalid and task.error <> "" then detail = task.error + ". Press * to retry."
    if m.apiStatus <> invalid then m.apiStatus.text = "SERVER NOT FOUND · PRESS *"
    showEmptyState("SundaySignal not found", detail)
end sub

sub loadCatalog()
    if m.top.catalogUri = invalid or m.top.catalogUri = "" then return
    if m.apiStatus <> invalid then
        m.apiStatus.text = "FETCHING CATALOG..."
    end if

    if m.catalogTask <> invalid then
        m.catalogTask.unobserveField("state")
        m.catalogTask.control = "STOP"
    end if

    task = CreateObject("roSGNode", "StreamCatalogTask")
    task.catalogUri = m.top.catalogUri
    task.observeField("state", "onCatalogState")
    m.catalogTask = task
    task.control = "RUN"
end sub

sub onCatalogState()
    task = m.catalogTask
    if task = invalid then return
    st = task.state
    if st = "RUN" then return
    if st <> "DONE" and st <> "STOP" then return

    err = task.error
    result = task.result

    if err <> invalid and len(err) > 0 then
        if m.apiStatus <> invalid then
            m.apiStatus.text = "CONNECTION LOST · PRESS * TO SEARCH"
        end if
        m.streams = []
        renderRail()
        showEmptyState("Server unavailable", "SundaySignal could not load the catalog. Press * to search again.")
        return
    end if

    if result <> invalid and result.Count() > 0 then
        m.streams = result
        m.selected = 0
        m.playing = false
        stopVideo()
        renderRail()
        renderSelection()
        if m.apiStatus <> invalid then
            m.apiStatus.text = "API CONNECTED · " + result.Count().ToStr() + " STREAMS"
        end if
        showPlayerContent()
    else
        if m.apiStatus <> invalid then
            m.apiStatus.text = "CONNECTED · NO STREAMS"
        end if
        m.streams = []
        renderRail()
        showEmptyState("No playable streams", "The server is connected. Rescrape from the web app, then press * to refresh.")
    end if
end sub

sub showEmptyState(title as String, detail as String)
    if m.playerContent <> invalid then m.playerContent.visible = false
    if m.emptyState <> invalid then m.emptyState.visible = true
    if m.emptyTitle <> invalid then m.emptyTitle.text = title
    if m.emptyDetail <> invalid then m.emptyDetail.text = detail
    setLabel("crumb", "SUNDAY SIGNAL  /  NETWORK")
end sub

sub showPlayerContent()
    if m.emptyState <> invalid then m.emptyState.visible = false
    if m.playerContent <> invalid then m.playerContent.visible = true
end sub

sub renderRail()
    m.rail.removeChildrenIndex(0, m.rail.getChildCount())
    count = m.streams.Count()
    if m.streamCount <> invalid then
        label = count.ToStr()
        if count < 10 then label = "0" + label
        m.streamCount.text = label + " STREAMS"
    end if

    for i = 0 to count - 1
        item = m.streams[i]
        row = CreateObject("roSGNode", "Group")
        row.translation = [0, i * 57]

        back = row.createChild("Rectangle")
        back.width = 386
        back.height = 52
        back.color = "0x00000000"

        art = row.createChild("Rectangle")
        art.translation = [10, 6]
        art.width = 62
        art.height = 40
        if item.color <> invalid then
            art.color = item.color
        else
            art.color = "0x355B53FF"
        end if

        name = row.createChild("Label")
        name.translation = [86, 8]
        name.width = 265
        name.height = 24
        name.text = item.title
        name.font = "font:SmallBoldSystemFont"
        name.color = "0xF0F2EEFF"

        meta = row.createChild("Label")
        meta.translation = [86, 29]
        meta.width = 265
        meta.height = 20
        metaText = ""
        if item.meta <> invalid then metaText = item.meta
        meta.text = metaText
        meta.font = "font:SmallestSystemFont"
        meta.color = "0x999C98FF"

        if item.live = true then
            dot = row.createChild("Rectangle")
            dot.translation = [365, 23]
            dot.width = 6
            dot.height = 6
            dot.color = "0xF1FF73FF"
        end if

        m.rail.appendChild(row)
    end for
end sub

sub renderSelection()
    if m.streams.Count() = 0 then return
    if m.selected < 0 then m.selected = 0
    if m.selected > m.streams.Count() - 1 then m.selected = m.streams.Count() - 1

    item = m.streams[m.selected]

    for i = 0 to m.rail.getChildCount() - 1
        row = m.rail.getChild(i)
        if row <> invalid and row.getChildCount() > 0 then
            back = row.getChild(0)
            if i = m.selected then
                back.color = "0x2A2D29FF"
            else
                back.color = "0x00000000"
            end if
        end if
    end for

    cat = "NFL"
    if item.category <> invalid then cat = item.category
    setLabel("crumb", "LIBRARY  /  " + cat)
    setLabel("titleLabel", item.title)
    metaLine = "NFL  ·  Stream " + (m.selected + 1).ToStr()
    if item.meta <> invalid then metaLine = metaLine + "  ·  " + item.meta
    setLabel("metaLabel", metaLine)

    if item.live = true then
        setLabel("watchLabel", "●  WATCHING LIVE")
    else
        setLabel("watchLabel", "●  ON DEMAND")
    end if

    setLabel("videoTitle", item.title)
    loc = "PROXIED HLS"
    if item.streamName <> invalid then loc = UCase(item.streamName)
    setLabel("videoLocation", loc)

    setLabel("detailTitle", item.title)
    desc = ""
    if item.description <> invalid then desc = item.description
    setLabel("detailText", desc)

    prog = m.top.findNode("progress")
    if prog <> invalid then prog.width = 236

    m.playing = false
    updatePlaybackChrome()
end sub

sub setLabel(id as String, text as String)
    n = m.top.findNode(id)
    if n <> invalid then n.text = text
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false

    if key = "options" or key = "replay" or key = "info" then
        discoverServer()
        return true
    end if

    if key = "back" then
        if m.playing = true or (m.video <> invalid and m.video.visible = true) then
            stopVideo()
            m.playing = false
            updatePlaybackChrome()
            return true
        end if
        return false
    end if

    if key = "down" then
        if m.selected < m.streams.Count() - 1 then
            m.selected = m.selected + 1
            stopVideo()
            renderSelection()
        end if
        return true
    end if

    if key = "up" then
        if m.selected > 0 then
            m.selected = m.selected - 1
            stopVideo()
            renderSelection()
        end if
        return true
    end if

    if key = "OK" then
        togglePlayback()
        return true
    end if

    if key = "left" then
        prog = m.top.findNode("progress")
        if prog <> invalid then
            width = prog.width - 50
            if width < 0 then width = 0
            prog.width = width
        end if
        return true
    end if

    if key = "right" then
        prog = m.top.findNode("progress")
        if prog <> invalid then
            width = prog.width + 50
            if width > 495 then width = 495
            prog.width = width
        end if
        return true
    end if

    return false
end function

sub togglePlayback()
    if m.streams.Count() = 0 then return
    item = m.streams[m.selected]

    if item.url = invalid or item.url = "" then
        if m.apiStatus <> invalid then
            m.apiStatus.text = "NO PLAYABLE URL · REFRESH OR CHECK PROXY"
        end if
        return
    end if

    if m.playing = true then
        m.video.control = "pause"
        m.playing = false
        updatePlaybackChrome()
        return
    end if

    content = CreateObject("roSGNode", "ContentNode")
    content.url = item.url
    fmt = "hls"
    if item.format <> invalid and item.format <> "" then fmt = item.format
    content.streamFormat = fmt
    content.title = item.title

    m.video.content = content
    m.video.visible = true
    if m.playOverlay <> invalid then m.playOverlay.visible = false
    m.video.control = "play"
    m.playing = false
    updatePlaybackChrome()
    if m.apiStatus <> invalid then
        m.apiStatus.text = "OPENING STREAM…"
    end if
end sub

sub onVideoState()
    if m.video = invalid then return
    state = m.video.state
    if state = "playing" then
        m.playing = true
        if m.apiStatus <> invalid then m.apiStatus.text = "PLAYING · " + m.streams[m.selected].title
    else if state = "buffering" then
        m.playing = false
        if m.apiStatus <> invalid then m.apiStatus.text = "BUFFERING STREAM…"
    else if state = "error" then
        m.playing = false
        message = "PLAYBACK ERROR"
        if m.video.errorCode <> invalid then message = message + " " + m.video.errorCode.ToStr()
        if m.video.errorMsg <> invalid and m.video.errorMsg <> "" then message = message + " · " + m.video.errorMsg
        if m.apiStatus <> invalid then m.apiStatus.text = message
    else if state = "finished" or state = "stopped" then
        m.playing = false
    end if
    updatePlaybackChrome()
end sub

sub stopVideo()
    if m.video <> invalid then
        m.video.control = "stop"
        m.video.visible = false
    end if
    if m.playOverlay <> invalid then m.playOverlay.visible = true
end sub

sub updatePlaybackChrome()
    if m.playing then
        glyph = "Ⅱ"
    else
        glyph = "▶"
    end if
    setLabel("playGlyph", glyph)
    setLabel("controlGlyph", glyph)
    if m.playOverlay <> invalid then
        if m.playing then
            m.playOverlay.visible = false
        else
            m.playOverlay.visible = true
        end if
    end if
end sub
