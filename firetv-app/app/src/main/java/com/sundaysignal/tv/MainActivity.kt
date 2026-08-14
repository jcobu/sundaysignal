package com.sundaysignal.tv

import android.graphics.Color as AndroidColor
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.ui.PlayerView
import androidx.tv.material3.Border
import androidx.tv.material3.Button
import androidx.tv.material3.Card
import androidx.tv.material3.CardDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.Collections
import java.util.LinkedHashSet
import java.util.Locale
import java.util.concurrent.ExecutorCompletionService
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

private val Navy = Color(0xFF112852)
private val Background = Color(0xFF071226)
private val Panel = Color(0xFF0C1D3C)
private val CardBlue = Color(0xFF132E5E)
private val SelectedBlue = Color(0xFF183A77)
private val FocusBlue = Color(0xFF8AB8FF)
private val TextPrimary = Color(0xFFF7F9FF)
private val TextMuted = Color(0xFFAFC2E6)
private val LiveRed = Color(0xFFFF6078)

class MainActivity : ComponentActivity() {
    private val worker: ExecutorService = Executors.newCachedThreadPool()
    private var catalogState by mutableStateOf<CatalogState>(CatalogState.Searching)
    private var playbackSelection by mutableStateOf<PlaybackSelection?>(null)
    private var playbackStatus by mutableStateOf<String?>(null)
    private var restoreFocusToken by mutableIntStateOf(0)
    private var serverBase: String? = null
    private var player by mutableStateOf<ExoPlayer?>(null)
    private var mediaSession: MediaSession? = null
    private var resumeAfterPause = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        useTvFullscreenUi()
        setContent {
            MaterialTheme {
                SundaySignalApp(
                    state = catalogState,
                    playback = playbackSelection,
                    playbackStatus = playbackStatus,
                    player = player,
                    restoreFocusToken = restoreFocusToken,
                    onReconnect = ::discoverServer,
                    onPlay = ::play,
                    onClosePlayer = ::closePlayer,
                )
            }
        }
        discoverServer()
    }

    private fun discoverServer() {
        closePlayer()
        catalogState = CatalogState.Searching
        worker.execute {
            try {
                val saved = getPreferences(MODE_PRIVATE).getString("serverBase", null)
                if (saved != null && probe(saved)) {
                    onServerFound(saved)
                    return@execute
                }
                val prefix = findIpv4Prefix() ?: error("No local IPv4 network found")
                val candidates = LinkedHashSet<String>()
                intArrayOf(1, 2, 10, 20, 25, 50, 100, 150, 200, 207, 250, 254).forEach {
                    candidates += "http://$prefix$it:8765"
                }
                (1..254).forEach { candidates += "http://$prefix$it:8765" }

                val scanner = Executors.newFixedThreadPool(24)
                val completion = ExecutorCompletionService<String?>(scanner)
                candidates.forEach { candidate ->
                    completion.submit(java.util.concurrent.Callable { if (probe(candidate)) candidate else null })
                }
                var found: String? = null
                for (index in candidates.indices) {
                    val result = completion.poll(8, TimeUnit.SECONDS) ?: break
                    val candidate = result.get()
                    if (candidate != null) {
                        found = candidate
                        break
                    }
                }
                scanner.shutdownNow()
                val resolved = found ?: error("No SundaySignal server found on ${prefix}0/24")
                getPreferences(MODE_PRIVATE).edit().putString("serverBase", resolved).apply()
                onServerFound(resolved)
            } catch (error: Exception) {
                runOnUiThread {
                    catalogState = CatalogState.Error(
                        "SundaySignal wasn’t found",
                        "Make sure Docker and Fire TV are on the same network, then try again.",
                    )
                }
            }
        }
    }

    private fun onServerFound(base: String) {
        serverBase = base
        loadCatalog(base)
    }

    private fun loadCatalog(base: String) {
        worker.execute {
            try {
                val root = getJson("$base/api/streams", 3500)
                val sourceGames = root.optJSONArray("games")
                val loaded = mutableListOf<Game>()
                if (sourceGames != null) {
                    for (index in 0 until sourceGames.length()) {
                        val source = sourceGames.optJSONObject(index) ?: continue
                        val sourceStreams = source.optJSONArray("streams") ?: continue
                        val streams = mutableListOf<StreamSource>()
                        for (streamIndex in 0 until sourceStreams.length()) {
                            val stream = sourceStreams.optJSONObject(streamIndex) ?: continue
                            val relative = stream.optString("play_url", "")
                            if (relative.isEmpty()) continue
                            streams += StreamSource(
                                name = stream.optString("name", "Stream ${streamIndex + 1}"),
                                badges = badgeSummary(stream.optJSONArray("badges")),
                                sourceType = stream.optString("source_type", "HLS"),
                                playUrl = if (relative.startsWith("http")) relative else base + relative,
                            )
                        }
                        if (streams.isEmpty()) continue
                        loaded += Game(
                            title = source.optString("title", "Game"),
                            kickoff = source.optString("kickoff_local", ""),
                            status = source.optString("status_state", "Available"),
                            leftTeamSource = source.optString("home_team", ""),
                            rightTeamSource = source.optString("away_team", ""),
                            leftAbbr = source.optString("home_abbr", ""),
                            rightAbbr = source.optString("away_abbr", ""),
                            streams = streams,
                        )
                    }
                }
                runOnUiThread {
                    catalogState = if (loaded.isEmpty()) {
                        CatalogState.Error(
                            "No playable games right now",
                            "Rescrape from the SundaySignal web app, then reconnect.",
                        )
                    } else {
                        CatalogState.Ready(base, loaded)
                    }
                }
            } catch (error: Exception) {
                runOnUiThread {
                    catalogState = CatalogState.Error(
                        "The game catalog could not be loaded",
                        "The server is reachable. Try reconnecting to refresh its catalog.",
                    )
                }
            }
        }
    }

    private fun play(selection: PlaybackSelection) {
        playbackSelection = selection
        playbackStatus = null
        val activePlayer = player ?: ExoPlayer.Builder(this).build().also { created ->
            player = created
            mediaSession = MediaSession.Builder(this, created).build()
            created.addListener(object : Player.Listener {
                override fun onPlayerError(error: PlaybackException) {
                    playbackStatus = "This stream could not be played\n${error.errorCodeName}\nPress Back to return"
                }
            })
        }
        val item = MediaItem.Builder()
            .setUri(Uri.parse(selection.stream.playUrl))
            .setMimeType(MimeTypes.APPLICATION_M3U8)
            .setMediaMetadata(MediaMetadata.Builder().setTitle(selection.game.title).build())
            .build()
        activePlayer.setMediaItem(item)
        activePlayer.prepare()
        activePlayer.play()
    }

    private fun closePlayer() {
        if (playbackSelection == null && (player == null || player?.mediaItemCount == 0)) return
        player?.stop()
        player?.clearMediaItems()
        playbackSelection = null
        playbackStatus = null
        restoreFocusToken++
    }

    private fun probe(base: String): Boolean = try {
        val health = getJson("$base/api/health", 550)
        health.optBoolean("ok") && health.optString("service") == "SundaySignal"
    } catch (_: Exception) {
        false
    }

    private fun getJson(address: String, timeoutMs: Int): JSONObject {
        val connection = URL(address).openConnection() as HttpURLConnection
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.useCaches = false
        connection.setRequestProperty("Accept", "application/json")
        return try {
            if (connection.responseCode != 200) error("HTTP ${connection.responseCode}")
            JSONObject(readAll(connection.inputStream))
        } finally {
            connection.disconnect()
        }
    }

    private fun readAll(input: InputStream): String =
        BufferedReader(InputStreamReader(input, StandardCharsets.UTF_8)).use { reader ->
            buildString {
                while (true) appendLine(reader.readLine() ?: break)
            }
        }

    private fun findIpv4Prefix(): String? {
        val interfaces = NetworkInterface.getNetworkInterfaces() ?: return null
        for (network in Collections.list(interfaces)) {
            if (!network.isUp || network.isLoopback) continue
            for (address in Collections.list(network.inetAddresses)) {
                if (address !is Inet4Address || address.isLoopbackAddress || !address.isSiteLocalAddress) continue
                val parts = address.hostAddress?.split('.') ?: continue
                if (parts.size == 4) return "${parts[0]}.${parts[1]}.${parts[2]}."
            }
        }
        return null
    }

    private fun badgeSummary(badges: JSONArray?): String {
        if (badges == null) return ""
        val parts = mutableListOf<String>()
        for (index in 0 until badges.length()) {
            if (parts.size == 3) break
            val badge = badges.optString(index, "").trim()
            if (badge.isEmpty() || badge.matches(Regex("\\d+"))) continue
            parts += badge.uppercase(Locale.ROOT)
        }
        return parts.joinToString("  ·  ")
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_MENU && playbackSelection == null) {
            discoverServer()
            return true
        }
        return super.onKeyUp(keyCode, event)
    }

    override fun onPause() {
        resumeAfterPause = player?.isPlaying == true
        player?.pause()
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        useTvFullscreenUi()
        if (resumeAfterPause && playbackSelection != null) player?.play()
        resumeAfterPause = false
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) useTvFullscreenUi()
    }

    override fun onDestroy() {
        mediaSession?.release()
        player?.release()
        worker.shutdownNow()
        super.onDestroy()
    }

    @Suppress("DEPRECATION")
    private fun useTvFullscreenUi() {
        window.decorView.systemUiVisibility =
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
    }
}

@Composable
private fun SundaySignalApp(
    state: CatalogState,
    playback: PlaybackSelection?,
    playbackStatus: String?,
    player: ExoPlayer?,
    restoreFocusToken: Int,
    onReconnect: () -> Unit,
    onPlay: (PlaybackSelection) -> Unit,
    onClosePlayer: () -> Unit,
) {
    Box(Modifier.fillMaxSize().background(Background)) {
        when (state) {
            CatalogState.Searching -> MessageScreen(
                title = "Finding SundaySignal",
                body = "Searching your network on port 8765…",
                action = null,
            )
            is CatalogState.Error -> MessageScreen(state.title, state.body, onReconnect)
            is CatalogState.Ready -> BrowserScreen(state, restoreFocusToken, onReconnect, onPlay)
        }
        if (playback != null && player != null) {
            PlayerScreen(player, playbackStatus)
            BackHandler(onBack = onClosePlayer)
        }
    }
}

@Composable
private fun MessageScreen(title: String, body: String, action: (() -> Unit)?) {
    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 48.dp, vertical = 27.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Image(
            painter = painterResource(R.drawable.sundaysignal_icon),
            contentDescription = null,
            modifier = Modifier.size(72.dp),
        )
        Spacer(Modifier.height(20.dp))
        Text(title, color = TextPrimary, fontSize = 28.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(10.dp))
        Text(body, color = TextMuted, fontSize = 16.sp)
        if (action != null) {
            Spacer(Modifier.height(24.dp))
            Button(onClick = action) { Text("Reconnect") }
        }
    }
}

@Composable
private fun BrowserScreen(
    state: CatalogState.Ready,
    restoreFocusToken: Int,
    onReconnect: () -> Unit,
    onPlay: (PlaybackSelection) -> Unit,
) {
    var selectedGameIndex by remember(state.games) { mutableIntStateOf(0) }
    var selectedStreamIndex by remember(state.games) { mutableIntStateOf(0) }
    var moveToStreams by remember { mutableStateOf(false) }
    val gameRequesters = remember(state.games) { List(state.games.size) { FocusRequester() } }
    val selectedGame = state.games[selectedGameIndex.coerceIn(state.games.indices)]
    val streamRequesters = remember(selectedGame) { List(selectedGame.streams.size) { FocusRequester() } }
    val firstStreamRequester = streamRequesters.firstOrNull()

    LaunchedEffect(state.games) {
        delay(160)
        gameRequesters.firstOrNull()?.requestFocus()
    }
    LaunchedEffect(moveToStreams, selectedGame) {
        if (moveToStreams) {
            delay(50)
            firstStreamRequester?.requestFocus()
            moveToStreams = false
        }
    }
    LaunchedEffect(restoreFocusToken) {
        if (restoreFocusToken > 0) {
            delay(100)
            streamRequesters.getOrNull(selectedStreamIndex)?.requestFocus()
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 48.dp, vertical = 27.dp),
    ) {
        Header(state, onReconnect)
        Spacer(Modifier.height(18.dp))
        Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(24.dp)) {
            Column(Modifier.fillMaxHeight().weight(0.42f)) {
                SectionLabel("GAMES", "${state.games.size}")
                Spacer(Modifier.height(10.dp))
                LazyColumn(
                    contentPadding = PaddingValues(vertical = 6.dp, horizontal = 5.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    itemsIndexed(state.games, key = { _, game -> game.title }) { index, game ->
                        GameCard(
                            game = game,
                            selected = index == selectedGameIndex,
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(92.dp)
                                .focusRequester(gameRequesters[index])
                                .focusProperties { if (firstStreamRequester != null) right = firstStreamRequester },
                            onFocused = {
                                if (selectedGameIndex != index) {
                                    selectedGameIndex = index
                                    selectedStreamIndex = 0
                                }
                            },
                            onClick = {
                                selectedGameIndex = index
                                selectedStreamIndex = 0
                                moveToStreams = true
                            },
                        )
                    }
                }
            }

            Column(Modifier.fillMaxHeight().weight(0.58f)) {
                MatchupHero(selectedGame)
                Spacer(Modifier.height(18.dp))
                SectionLabel("AVAILABLE STREAMS", "${selectedGame.streams.size}")
                Spacer(Modifier.height(10.dp))
                LazyColumn(
                    contentPadding = PaddingValues(vertical = 6.dp, horizontal = 5.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    itemsIndexed(selectedGame.streams) { index, stream ->
                        StreamCard(
                            index = index,
                            stream = stream,
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(74.dp)
                                .focusRequester(streamRequesters[index])
                                .focusProperties { left = gameRequesters[selectedGameIndex] },
                            onFocused = { selectedStreamIndex = index },
                            onClick = { onPlay(PlaybackSelection(selectedGame, stream)) },
                        )
                    }
                }
                Spacer(Modifier.weight(1f))
                Text(
                    "Select a source to watch full screen",
                    color = TextMuted,
                    fontSize = 14.sp,
                    modifier = Modifier.padding(start = 5.dp),
                )
            }
        }
    }
}

@Composable
private fun Header(state: CatalogState.Ready, onReconnect: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().height(46.dp)) {
        Image(
            painter = painterResource(R.drawable.sundaysignal_icon),
            contentDescription = null,
            modifier = Modifier.size(42.dp),
            contentScale = ContentScale.Crop,
        )
        Spacer(Modifier.width(12.dp))
        Text("SundaySignal", color = TextPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.weight(1f))
        Box(Modifier.size(8.dp).background(Color(0xFF6ED89A), RoundedCornerShape(50)))
        Spacer(Modifier.width(8.dp))
        Text(
            "Connected · ${state.base.removePrefix("http://")}",
            color = TextMuted,
            fontSize = 14.sp,
        )
        Spacer(Modifier.width(18.dp))
        Button(onClick = onReconnect, contentPadding = PaddingValues(horizontal = 18.dp, vertical = 8.dp)) {
            Text("Reconnect", fontSize = 14.sp)
        }
    }
}

@Composable
private fun SectionLabel(title: String, count: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(title, color = TextMuted, fontSize = 14.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.weight(1f))
        Text(count, color = TextMuted, fontSize = 13.sp)
    }
}

@Composable
private fun GameCard(
    game: Game,
    selected: Boolean,
    modifier: Modifier,
    onFocused: () -> Unit,
    onClick: () -> Unit,
) {
    Card(
        onClick = onClick,
        modifier = modifier.then(Modifier.onTvFocus(onFocused)),
        colors = CardDefaults.colors(
            containerColor = if (selected) SelectedBlue else CardBlue,
            focusedContainerColor = Color(0xFF214A91),
        ),
        scale = CardDefaults.scale(focusedScale = 1.04f),
        border = CardDefaults.border(
            focusedBorder = Border(
                border = BorderStroke(3.dp, FocusBlue),
                shape = RoundedCornerShape(12.dp),
            ),
        ),
    ) {
        Row(
            modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TeamIcon(game.leftAbbr, 38)
            Spacer(Modifier.width(5.dp))
            TeamIcon(game.rightAbbr, 38)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                StackedMatchup(game, 14)
                Spacer(Modifier.height(5.dp))
                Text(
                    game.displayMeta(),
                    color = TextMuted,
                    fontSize = 12.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
private fun MatchupHero(game: Game) {
    Row(
        modifier = Modifier.fillMaxWidth().height(88.dp).background(Panel, RoundedCornerShape(14.dp)).padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TeamIcon(game.leftAbbr, 58)
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            StackedMatchup(game, 19)
            Spacer(Modifier.height(5.dp))
            Text(game.displayMeta(), color = TextMuted, fontSize = 13.sp, maxLines = 1)
        }
        Spacer(Modifier.width(16.dp))
        TeamIcon(game.rightAbbr, 58)
    }
}

@Composable
private fun StackedMatchup(game: Game, textSize: Int) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            MatchupText(game.leftCity, Modifier.weight(1f), textSize)
            Text("vs", color = TextMuted, fontSize = (textSize - 2).sp, modifier = Modifier.width(34.dp))
            MatchupText(game.rightCity, Modifier.weight(1f), textSize)
        }
        Row {
            MatchupText(game.leftNickname, Modifier.weight(1f), textSize)
            Spacer(Modifier.width(34.dp))
            MatchupText(game.rightNickname, Modifier.weight(1f), textSize)
        }
    }
}

@Composable
private fun MatchupText(value: String, modifier: Modifier, textSize: Int) {
    Text(
        value,
        color = TextPrimary,
        fontSize = textSize.sp,
        fontWeight = FontWeight.Bold,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier,
    )
}

@Composable
private fun StreamCard(
    index: Int,
    stream: StreamSource,
    modifier: Modifier,
    onFocused: () -> Unit,
    onClick: () -> Unit,
) {
    Card(
        onClick = onClick,
        modifier = modifier.then(Modifier.onTvFocus(onFocused)),
        colors = CardDefaults.colors(containerColor = Navy, focusedContainerColor = Color(0xFF214A91)),
        scale = CardDefaults.scale(focusedScale = 1.04f),
        border = CardDefaults.border(
            focusedBorder = Border(
                border = BorderStroke(3.dp, FocusBlue),
                shape = RoundedCornerShape(12.dp),
            ),
        ),
    ) {
        Row(
            modifier = Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    "STREAM ${index + 1}  ·  ${stream.name}",
                    color = TextPrimary,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(5.dp))
                Text(stream.displayMeta(), color = TextMuted, fontSize = 12.sp, maxLines = 1)
            }
            Text("▶", color = FocusBlue, fontSize = 18.sp)
        }
    }
}

private fun Modifier.onTvFocus(onFocused: () -> Unit): Modifier =
    this.onFocusChanged { if (it.isFocused) onFocused() }

@Composable
private fun TeamIcon(abbreviation: String, size: Int) {
    val resourceId = teamResourceId(abbreviation)
    if (resourceId != 0) {
        Image(
            painter = painterResource(resourceId),
            contentDescription = "${abbreviation.uppercase(Locale.ROOT)} team logo",
            modifier = Modifier.size(size.dp),
            contentScale = ContentScale.Fit,
        )
    } else {
        Spacer(Modifier.size(size.dp))
    }
}

private fun teamResourceId(abbreviation: String): Int = when (abbreviation.lowercase(Locale.ROOT)) {
    "ari" -> R.drawable.team_ari
    "atl" -> R.drawable.team_atl
    "bal" -> R.drawable.team_bal
    "buf" -> R.drawable.team_buf
    "car" -> R.drawable.team_car
    "chi" -> R.drawable.team_chi
    "cin" -> R.drawable.team_cin
    "cle" -> R.drawable.team_cle
    "dal" -> R.drawable.team_dal
    "den" -> R.drawable.team_den
    "det" -> R.drawable.team_det
    "gb" -> R.drawable.team_gb
    "hou" -> R.drawable.team_hou
    "ind" -> R.drawable.team_ind
    "jax" -> R.drawable.team_jax
    "kc" -> R.drawable.team_kc
    "lac" -> R.drawable.team_lac
    "lar" -> R.drawable.team_lar
    "lv" -> R.drawable.team_lv
    "mia" -> R.drawable.team_mia
    "min" -> R.drawable.team_min
    "ne" -> R.drawable.team_ne
    "no" -> R.drawable.team_no
    "nyg" -> R.drawable.team_nyg
    "nyj" -> R.drawable.team_nyj
    "phi" -> R.drawable.team_phi
    "pit" -> R.drawable.team_pit
    "sf" -> R.drawable.team_sf
    "sea" -> R.drawable.team_sea
    "tb" -> R.drawable.team_tb
    "ten" -> R.drawable.team_ten
    "wsh" -> R.drawable.team_wsh
    else -> 0
}

@Composable
private fun PlayerScreen(player: ExoPlayer, status: String?) {
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        AndroidView(
            factory = { context ->
                PlayerView(context).apply {
                    useController = true
                    setBackgroundColor(AndroidColor.BLACK)
                    this.player = player
                    isFocusable = true
                    layoutParams = FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        Gravity.CENTER,
                    )
                    requestFocus()
                }
            },
            update = { it.player = player },
            modifier = Modifier.fillMaxSize(),
        )
        if (status != null) {
            Text(
                status,
                color = TextPrimary,
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier
                    .align(Alignment.Center)
                    .background(Color(0xD9071226), RoundedCornerShape(12.dp))
                    .padding(horizontal = 32.dp, vertical = 20.dp),
            )
        }
    }
}

private sealed interface CatalogState {
    data object Searching : CatalogState
    data class Error(val title: String, val body: String) : CatalogState
    data class Ready(val base: String, val games: List<Game>) : CatalogState
}

private data class PlaybackSelection(val game: Game, val stream: StreamSource)

private data class Game(
    val title: String,
    val kickoff: String,
    val status: String,
    val leftTeamSource: String,
    val rightTeamSource: String,
    val leftAbbr: String,
    val rightAbbr: String,
    val streams: List<StreamSource>,
) {
    private val titleTeams = title.split(Regex("(?i)\\s+vs\\.?\\s+"), limit = 2)
    private val leftTeam = leftTeamSource.ifBlank { titleTeams.firstOrNull().orEmpty() }
    private val rightTeam = rightTeamSource.ifBlank { titleTeams.getOrNull(1).orEmpty() }
    private val leftParts = teamParts(leftTeam)
    private val rightParts = teamParts(rightTeam)
    val leftCity = leftParts.first
    val leftNickname = leftParts.second
    val rightCity = rightParts.first
    val rightNickname = rightParts.second

    fun displayMeta(): String {
        val state = status.ifBlank { "AVAILABLE" }.uppercase(Locale.ROOT)
        return if (kickoff.isBlank()) state else "$state  ·  $kickoff"
    }

    private fun teamParts(team: String): Pair<String, String> {
        val clean = team.trim()
        val split = clean.lastIndexOf(' ')
        return if (split < 0) clean to "" else clean.substring(0, split) to clean.substring(split + 1)
    }
}

private data class StreamSource(
    val name: String,
    val badges: String,
    val sourceType: String,
    val playUrl: String,
) {
    fun displayMeta(): String = badges.ifBlank {
        sourceType.ifBlank { "HLS STREAM" }.replace('_', ' ').uppercase(Locale.ROOT)
    }
}
