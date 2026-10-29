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
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
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
import androidx.tv.material3.darkColorScheme
import androidx.tv.material3.Text
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

private val SurfaceRaised = Color(0xFF25211C)
private val Background = Color(0xFF100E0C)
private val Panel = Color(0xFF1B1815)
private val SurfaceCard = Color(0xFF1B1815)
private val SelectedSurface = Color(0xFF33271D)
private val Accent = Color(0xFFFF8A3D)
private val TextPrimary = Color(0xFFF4EFE6)
private val TextMuted = Color(0xFFC3B9AA)
private val LiveRed = Color(0xFFFF6078)

/** Thrown for a 401 from a Device-gated endpoint, so callers can start the
 * login flow instead of treating it as an unreachable/broken server. */
private class AuthRequiredException : Exception()

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
    private var connectDialog by mutableStateOf(ConnectDialogState())
    // Bumped on every reconnect so a poll loop left over from a previous
    // server/PIN can tell it's stale and stop, instead of racing a new one.
    private var devicePollGeneration = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        useTvFullscreenUi()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(
                primary = Accent, onPrimary = Background, background = Background,
                onBackground = TextPrimary, surface = Panel, onSurface = TextPrimary,
            )) {
                SundaySignalApp(
                    state = catalogState,
                    playback = playbackSelection,
                    playbackStatus = playbackStatus,
                    player = player,
                    restoreFocusToken = restoreFocusToken,
                    connectDialog = connectDialog,
                    onReconnect = ::refreshCatalog,
                    onOpenConnectDialog = ::openConnectDialog,
                    onCloseConnectDialog = ::closeConnectDialog,
                    onSubmitConnectDialog = ::connectToUrl,
                    onRetryDeviceLogin = ::retryDeviceLogin,
                    onPlay = ::play,
                    onClosePlayer = ::closePlayer,
                )
            }
        }
        discoverServer()
    }

    /** No more LAN subnet scanning — the server can be a public domain now
     * that login/token gating exists, which a same-subnet scan can't find
     * anyway. First launch (nothing saved yet) goes straight to the address
     * dialog instead; once entered, connectToUrl() caches it and every
     * later launch just re-probes that saved address. */
    private fun discoverServer() {
        closePlayer()
        val saved = getPreferences(MODE_PRIVATE).getString("serverBase", null)
        if (saved == null) {
            catalogState = CatalogState.Searching
            openConnectDialog()
            return
        }
        catalogState = CatalogState.Searching
        worker.execute {
            val connectionError = serverConnectionError(saved)
            if (connectionError == null) {
                onServerFound(saved)
            } else {
                runOnUiThread {
                    catalogState = CatalogState.Error(
                        "Can't reach $saved",
                        connectionError,
                    )
                }
            }
        }
    }

    private fun refreshCatalog() {
        val base = serverBase
        if (base == null) discoverServer() else loadCatalog(base, preserveCurrent = true)
    }

    private fun onServerFound(base: String) {
        devicePollGeneration++ // any (re)connect invalidates an in-flight Device poll loop
        serverBase = base
        loadCatalog(base)
    }

    private fun openConnectDialog() {
        val prefill = serverBase ?: ""
        connectDialog = ConnectDialogState(visible = true, initialValue = prefill)
    }

    private fun closeConnectDialog() {
        connectDialog = ConnectDialogState()
    }

    private fun connectToUrl(raw: String) {
        val normalized = normalizeServerUrl(raw)
        if (normalized == null) {
            connectDialog = connectDialog.copy(error = "Enter an address, e.g. 192.168.1.50:8765")
            return
        }
        connectDialog = connectDialog.copy(busy = true, error = null)
        worker.execute {
            // A public domain can be slower to resolve/connect than a LAN
            // probe's fast default timeout accounts for.
            val connectionError = serverConnectionError(normalized)
            runOnUiThread {
                if (connectionError == null) {
                    getPreferences(MODE_PRIVATE).edit().putString("serverBase", normalized).apply()
                    connectDialog = ConnectDialogState()
                    onServerFound(normalized)
                } else {
                    connectDialog = connectDialog.copy(
                        busy = false,
                        error = connectionError,
                    )
                }
            }
        }
    }

    private fun loadCatalog(base: String, preserveCurrent: Boolean = false) {
        worker.execute {
            try {
                val root = getJson("$base/api/streams", 10000, base)
                val sourceGames = root.optJSONArray("games")
                val loaded = mutableListOf<Game>()
                if (sourceGames != null) {
                    for (index in 0 until sourceGames.length()) {
                        val source = sourceGames.optJSONObject(index) ?: continue
                        val sourceStreams = source.optJSONArray("streams") ?: JSONArray()
                        val streams = mutableListOf<StreamSource>()
                        for (streamIndex in 0 until sourceStreams.length()) {
                            val stream = sourceStreams.optJSONObject(streamIndex) ?: continue
                            val relative = stream.optString("play_url", "")
                            if (relative.isEmpty()) continue
                            streams += StreamSource(
                                name = stream.optString("name", "Stream ${streamIndex + 1}"),
                                badges = badgeSummary(stream.optJSONArray("badges")),
                                sourceType = stream.optString("source_type", "HLS"),
                                verified = !stream.optString("health_checked_at", "").isBlank(),
                                stale = stream.optBoolean("stale", false),
                                playUrl = if (relative.startsWith("http")) relative else base + relative,
                            )
                        }
                        loaded += Game(
                            id = source.optString("uid", source.optString("id", source.optString("title"))),
                            title = source.optString("title", "Game"),
                            // display_title/display_left_*/is_matchup account for
                            // non-matchup listings (RedZone, NFL Network, etc.) that
                            // aren't two teams playing; fall back to the older fields
                            // for a server predating those.
                            displayTitle = source.optString("display_title", source.optString("title", "Game")),
                            isMatchup = source.optBoolean("is_matchup", true),
                            kickoff = source.optString("kickoff_local", "").takeUnless { it == "null" }.orEmpty(),
                            status = source.optString("status_state", "Available"),
                            leftTeam = source.optString("display_left_team", source.optString("away_team", "")),
                            rightTeam = source.optString("display_right_team", source.optString("home_team", "")),
                            leftAbbr = source.optString("display_left_abbr", source.optString("away_abbr", "")),
                            rightAbbr = source.optString("display_right_abbr", source.optString("home_abbr", "")),
                            streams = streams,
                            leftScore = if (source.isNull("away_score")) null else source.optInt("away_score"),
                            rightScore = if (source.isNull("home_score")) null else source.optInt("home_score"),
                            broadcast = source.optString("broadcast", ""),
                            venue = source.optString("venue", ""),
                            statusDetail = source.optString("status_detail", ""),
                            period = source.optInt("period", 0),
                            clock = source.optString("display_clock", "").takeUnless { it == "null" }.orEmpty(),
                        )
                    }
                }
                runOnUiThread {
                    catalogState = if (loaded.isEmpty()) {
                        CatalogState.Error(
                            "No games listed yet",
                            "The schedule is currently unavailable. Try refreshing again shortly.",
                        )
                    } else {
                        CatalogState.Ready(base, mergeCatalogChannels(loaded))
                    }
                }
            } catch (authRequired: AuthRequiredException) {
                runOnUiThread { startDeviceLogin(base) }
            } catch (error: Exception) {
                if (preserveCurrent && catalogState is CatalogState.Ready) return@execute
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

    private fun serverConnectionError(base: String): String? = try {
        val health = getJson("$base/api/health", 10000)
        if (health.optBoolean("ok") && health.optString("service") == "SundaySignal") null
        else "That address responded, but it did not identify itself as SundaySignal."
    } catch (_: javax.net.ssl.SSLException) {
        "Couldn't establish a secure connection. Check the server's HTTPS certificate."
    } catch (_: java.net.UnknownHostException) {
        "Couldn't resolve that server name. Check the address and network connection."
    } catch (_: java.net.SocketTimeoutException) {
        "The server took too long to respond. Check the address and try again."
    } catch (error: Exception) {
        "Couldn't connect to $base. ${error.message ?: "Check the server and network connection."}"
    }

    /** Health stays public; a catalog 401 starts device approval. */
    private fun getJson(address: String, timeoutMs: Int, base: String? = null): JSONObject =
        httpJson(address, "GET", timeoutMs, base)

    private fun postJson(address: String, timeoutMs: Int, base: String? = null): JSONObject =
        httpJson(address, "POST", timeoutMs, base)

    private fun httpJson(address: String, method: String, timeoutMs: Int, base: String?): JSONObject {
        val connection = URL(address).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.useCaches = false
        connection.setRequestProperty("Accept", "application/json")
        if (base != null) sessionCookie(base)?.let { connection.setRequestProperty("Cookie", it) }
        if (method == "POST") {
            connection.doOutput = true
            connection.setRequestProperty("Content-Length", "0")
        }
        return try {
            if (method == "POST") connection.outputStream.close()
            val code = connection.responseCode
            if (base != null) setCookieHeader(connection)?.let { saveSessionCookie(base, it) }
            if (code == 401) throw AuthRequiredException()
            if (code !in 200..299) error("HTTP $code")
            JSONObject(readAll(connection.inputStream))
        } finally {
            connection.disconnect()
        }
    }

    private fun setCookieHeader(connection: HttpURLConnection): String? {
        for ((key, values) in connection.headerFields) {
            if (key != null && key.equals("Set-Cookie", ignoreCase = true)) return values.firstOrNull()
        }
        return null
    }

    private fun cookiePrefsKey(base: String) = "deviceCookie::$base"

    private fun sessionCookie(base: String): String? =
        getPreferences(MODE_PRIVATE).getString(cookiePrefsKey(base), null)

    /** Keep only "name=value" — Path/HttpOnly/Max-Age etc. are attributes
     * for a browser's cookie jar, not something we replay on the next
     * request's Cookie header. */
    private fun saveSessionCookie(base: String, setCookieHeader: String) {
        val pair = setCookieHeader.substringBefore(';').trim()
        if (pair.isNotEmpty()) {
            getPreferences(MODE_PRIVATE).edit().putString(cookiePrefsKey(base), pair).apply()
        }
    }

    private fun clearSessionCookie(base: String) {
        getPreferences(MODE_PRIVATE).edit().remove(cookiePrefsKey(base)).apply()
    }

    /** Ask a signed-in SundaySignal user to approve this TV. */
    private fun startDeviceLogin(base: String) {
        clearSessionCookie(base)
        catalogState = CatalogState.DeviceLogin(base)
        val generation = ++devicePollGeneration
        worker.execute {
            try {
                val pin = postJson("$base/auth/device/pin", 5000, base)
                if (!pin.optBoolean("ok")) error(pin.optString("error", "Could not start Device login"))
                val pinId = pin.getInt("id")
                val code = pin.getString("code")
                runOnUiThread {
                    if (generation == devicePollGeneration) {
                        catalogState = CatalogState.DeviceLogin(
                            base = base,
                            code = code,
                            status = "Waiting for you to finish approving this TV…",
                        )
                    }
                }
                pollDeviceLogin(base, pinId, generation)
            } catch (error: Exception) {
                runOnUiThread {
                    if (generation == devicePollGeneration) {
                        catalogState = CatalogState.DeviceLogin(base, error = "Couldn't reach the server. Try again.")
                    }
                }
            }
        }
    }

    /** Runs on a worker thread; blocks it between polls the same way
     * discoverServer() already blocks a worker thread on completion.poll(). */
    private fun pollDeviceLogin(base: String, pinId: Int, generation: Int) {
        // Device codes expire after ten minutes; this client-side cap
        // keeps a forgotten screen from polling forever.
        repeat(300) {
            if (generation != devicePollGeneration) return
            Thread.sleep(2000)
            if (generation != devicePollGeneration) return
            try {
                val poll = getJson("$base/auth/device/poll/$pinId", 5000, base)
                if (!poll.optBoolean("ok")) {
                    runOnUiThread {
                        if (generation == devicePollGeneration) {
                            catalogState = CatalogState.DeviceLogin(
                                base, error = poll.optString("error", "Something went wrong."),
                            )
                        }
                    }
                    return
                }
                if (poll.optBoolean("denied")) {
                    runOnUiThread {
                        if (generation == devicePollGeneration) {
                            catalogState = CatalogState.DeviceLogin(
                                base,
                                error = poll.optString(
                                    "error", "This Device account doesn't have access to this server.",
                                ),
                            )
                        }
                    }
                    return
                }
                if (poll.optBoolean("authenticated")) {
                    runOnUiThread { if (generation == devicePollGeneration) onServerFound(base) }
                    return
                }
                // else: not yet — keep polling
            } catch (_: Exception) {
                // transient network hiccup while polling — keep trying silently
            }
        }
        runOnUiThread {
            if (generation == devicePollGeneration) {
                catalogState = CatalogState.DeviceLogin(base, error = "That code expired. Get a new one to try again.")
            }
        }
    }

    private fun retryDeviceLogin() {
        (catalogState as? CatalogState.DeviceLogin)?.let { startDeviceLogin(it.base) }
    }

    private fun readAll(input: InputStream): String =
        BufferedReader(InputStreamReader(input, StandardCharsets.UTF_8)).use { reader ->
            buildString {
                while (true) appendLine(reader.readLine() ?: break)
            }
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
    connectDialog: ConnectDialogState,
    onReconnect: () -> Unit,
    onOpenConnectDialog: () -> Unit,
    onCloseConnectDialog: () -> Unit,
    onSubmitConnectDialog: (String) -> Unit,
    onRetryDeviceLogin: () -> Unit,
    onPlay: (PlaybackSelection) -> Unit,
    onClosePlayer: () -> Unit,
) {
    Box(Modifier.fillMaxSize().background(Background)) {
        when (state) {
            CatalogState.Searching -> MessageScreen(
                title = "SundaySignal",
                body = "Connecting…",
                action = null,
                onEnterAddress = onOpenConnectDialog,
            )
            is CatalogState.Error -> MessageScreen(state.title, state.body, onReconnect, onOpenConnectDialog)
            is CatalogState.Ready -> BrowserScreen(state, restoreFocusToken, onReconnect, onOpenConnectDialog, onPlay)
            is CatalogState.DeviceLogin -> DeviceLoginScreen(state, onRetryDeviceLogin, onOpenConnectDialog)
        }
        if (playback != null && player != null) {
            PlayerScreen(player, playbackStatus)
            BackHandler(onBack = onClosePlayer)
        }
        if (connectDialog.visible) {
            ConnectDialog(
                initialValue = connectDialog.initialValue,
                busy = connectDialog.busy,
                error = connectDialog.error,
                onDismiss = onCloseConnectDialog,
                onSubmit = onSubmitConnectDialog,
            )
        }
    }
}

@Composable
private fun MessageScreen(title: String, body: String, action: (() -> Unit)?, onEnterAddress: () -> Unit) {
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
        Spacer(Modifier.height(24.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (action != null) Button(onClick = action) { Text("Reconnect") }
            Button(onClick = onEnterAddress) { Text("Enter address") }
        }
    }
}

@Composable
private fun DeviceLoginScreen(state: CatalogState.DeviceLogin, onRetry: () -> Unit, onEnterAddress: () -> Unit) {
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
        Text("Sign in to SundaySignal", color = TextPrimary, fontSize = 28.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(10.dp))
        Text(
            "Approve this TV using your SundaySignal account.",
            color = TextMuted,
            fontSize = 16.sp,
        )
        Spacer(Modifier.height(28.dp))
        if (state.code != null) {
            Text("On your phone or computer, go to", color = TextMuted, fontSize = 16.sp)
            Text("${state.base}/device", color = Accent, fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(18.dp))
            Text("and enter this code", color = TextMuted, fontSize = 14.sp)
            Spacer(Modifier.height(8.dp))
            Text(
                // Spaced out by hand rather than a letterSpacing param, since
                // this codebase otherwise sticks to Text's color/fontSize/
                // fontWeight surface and this reads just as clearly.
                state.code.toCharArray().joinToString("  "),
                color = TextPrimary,
                fontSize = 40.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(24.dp))
        }
        if (state.error != null) {
            Text(state.error, color = LiveRed, fontSize = 15.sp)
        } else {
            Text(state.status, color = TextMuted, fontSize = 14.sp)
        }
        Spacer(Modifier.height(24.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (state.error != null) Button(onClick = onRetry) { Text("Get a new code") }
            Button(onClick = onEnterAddress) { Text("Change server") }
        }
    }
}

@Composable
private fun BrowserScreen(
    state: CatalogState.Ready,
    restoreFocusToken: Int,
    onReconnect: () -> Unit,
    onOpenConnectDialog: () -> Unit,
    onPlay: (PlaybackSelection) -> Unit,
) {
    var selectedGameId by remember { mutableStateOf(state.games.first().id) }
    val selectedGameIndex = state.games.indexOfFirst { it.id == selectedGameId }.coerceAtLeast(0)
    var selectedStreamIndex by remember { mutableIntStateOf(0) }
    var moveToStreams by remember { mutableStateOf(false) }
    val gameRequesters = remember(state.games.map { it.id }) { List(state.games.size) { FocusRequester() } }
    val selectedGame = state.games[selectedGameIndex.coerceIn(state.games.indices)]
    val streamRequesters = remember(selectedGame.id, selectedGame.streams.size) { List(selectedGame.streams.size) { FocusRequester() } }
    val firstStreamRequester = streamRequesters.firstOrNull()

    LaunchedEffect(Unit) {
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
            (streamRequesters.getOrNull(selectedStreamIndex) ?: gameRequesters.getOrNull(selectedGameIndex))?.requestFocus()
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            delay(30000)
            onReconnect()
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 48.dp, vertical = 27.dp),
    ) {
        Header(state, onReconnect, onOpenConnectDialog)
        Spacer(Modifier.height(18.dp))
        Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(24.dp)) {
            Column(Modifier.fillMaxHeight().weight(0.62f)) {
                MatchupHero(selectedGame)
                Spacer(Modifier.height(18.dp))
                if (selectedGame.streams.isEmpty()) {
                    Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(4.dp)).padding(18.dp)) {
                        Text("Stream not available yet", color = TextPrimary, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(6.dp))
                        Text(if (selectedGame.status == "post") "No stream was found before this game ended."
                             else "Check back closer to kickoff. Refresh to check for new streams.", color = TextMuted, fontSize = 14.sp)
                    }
                } else SectionLabel("SOURCES", "${selectedGame.streams.size}")
                Spacer(Modifier.height(10.dp))
                LazyColumn(
                    modifier = Modifier.weight(1f),
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
                                .focusProperties { right = gameRequesters[selectedGameIndex] },
                            onFocused = { selectedStreamIndex = index },
                            onClick = { onPlay(PlaybackSelection(selectedGame, stream)) },
                        )
                    }
                }
                Text(
                    if (selectedGame.streams.isEmpty()) "Choose another game or refresh the schedule" else "Select a source to watch full screen",
                    color = TextMuted,
                    fontSize = 14.sp,
                    modifier = Modifier.padding(start = 5.dp),
                )
            }
            Column(Modifier.fillMaxHeight().weight(0.38f)) {
                SectionLabel("GAMES", "${state.games.size}")
                Spacer(Modifier.height(10.dp))
                LazyColumn(
                    contentPadding = PaddingValues(vertical = 6.dp, horizontal = 5.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    itemsIndexed(state.games, key = { _, game -> game.id }) { index, game ->
                        GameCard(
                            game = game,
                            selected = index == selectedGameIndex,
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(106.dp)
                                .focusRequester(gameRequesters[index])
                                .focusProperties { if (firstStreamRequester != null) left = firstStreamRequester },
                            onFocused = {
                                if (selectedGameIndex != index) {
                                    selectedGameId = game.id
                                    selectedStreamIndex = 0
                                }
                            },
                            onClick = {
                                selectedGameId = game.id
                                selectedStreamIndex = 0
                                moveToStreams = true
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun Header(state: CatalogState.Ready, onReconnect: () -> Unit, onChangeServer: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().height(46.dp)) {
        Image(
            painter = painterResource(R.drawable.sundaysignal_icon),
            contentDescription = null,
            modifier = Modifier.size(42.dp),
            contentScale = ContentScale.Crop,
        )
        Spacer(Modifier.width(12.dp))
        Text("SUNDAYSIGNAL", color = TextPrimary, fontSize = 21.sp, fontWeight = FontWeight.Black)
        Spacer(Modifier.weight(1f))
        Box(Modifier.size(8.dp).background(Color(0xFF6ED89A), RoundedCornerShape(50)))
        Spacer(Modifier.width(8.dp))
        Text(
            "Connected · ${state.games.count { it.streams.isNotEmpty() }} watchable",
            color = TextMuted,
            fontSize = 14.sp,
        )
        Spacer(Modifier.width(18.dp))
        Button(onClick = onChangeServer, contentPadding = PaddingValues(horizontal = 18.dp, vertical = 8.dp)) {
            Text("Change server", fontSize = 14.sp)
        }
        Spacer(Modifier.width(10.dp))
        Button(onClick = onReconnect, contentPadding = PaddingValues(horizontal = 18.dp, vertical = 8.dp)) {
            Text("Refresh", fontSize = 14.sp)
        }
    }
}

@Composable
private fun ConnectDialog(
    initialValue: String,
    busy: Boolean,
    error: String?,
    onDismiss: () -> Unit,
    onSubmit: (String) -> Unit,
) {
    var text by remember {
        mutableStateOf(TextFieldValue(initialValue, selection = TextRange(initialValue.length, initialValue.length)))
    }
    val focusRequester = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        delay(80)
        focusRequester.requestFocus()
    }

    Box(
        modifier = Modifier.fillMaxSize().background(Color(0xCC100E0C)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .width(560.dp)
                .background(Panel, RoundedCornerShape(16.dp))
                .padding(28.dp),
        ) {
            Text("Connect to a server", color = TextPrimary, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            Text(
                "Enter your SundaySignal server's address — a LAN IP (192.168.1.50:8765) " +
                    "or a public domain (sundaysignal.example.com).",
                color = TextMuted,
                fontSize = 14.sp,
            )
            Spacer(Modifier.height(18.dp))
            BasicTextField(
                value = text,
                onValueChange = { text = it },
                singleLine = true,
                textStyle = TextStyle(color = TextPrimary, fontSize = 18.sp),
                cursorBrush = SolidColor(Accent),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
                keyboardActions = KeyboardActions(onGo = { onSubmit(text.text) }),
                modifier = Modifier
                    .fillMaxWidth()
                    .focusRequester(focusRequester)
                    .background(Background, RoundedCornerShape(10.dp))
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            )
            if (error != null) {
                Spacer(Modifier.height(10.dp))
                Text(error, color = LiveRed, fontSize = 14.sp)
            }
            Spacer(Modifier.height(22.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = { onSubmit(text.text) }, enabled = !busy) {
                    Text(if (busy) "Connecting…" else "Connect")
                }
                Button(onClick = onDismiss, enabled = !busy) { Text("Cancel") }
            }
        }
    }
    BackHandler(onBack = onDismiss)
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
    game: Game, selected: Boolean, modifier: Modifier,
    onFocused: () -> Unit, onClick: () -> Unit,
) {
    Card(
        onClick = onClick,
        modifier = modifier.then(Modifier.onTvFocus(onFocused)),
        colors = CardDefaults.colors(containerColor = if (selected) SelectedSurface else SurfaceCard,
                                     focusedContainerColor = Color(0xFF3B2A1D)),
        scale = CardDefaults.scale(focusedScale = 1.02f),
        border = CardDefaults.border(focusedBorder = Border(border = BorderStroke(2.dp, Accent), shape = RoundedCornerShape(4.dp))),
    ) {
        Column(Modifier.fillMaxSize().padding(horizontal = 14.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            if (game.isMatchup) {
                CatalogTeam(game.leftTeam, game.leftAbbr, game.leftScore, 14)
                CatalogTeam(game.rightTeam, game.rightAbbr, game.rightScore, 14)
            } else Text(game.displayTitle, color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(game.statusLabel(), color = Accent, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(6.dp))
                Text(if (game.streams.isEmpty()) "Not available" else "Watch · ${game.streams.size}",
                     color = if (game.streams.isEmpty()) TextMuted else Color(0xFF8FD694), fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun CatalogTeam(name: String, abbr: String, score: Int?, size: Int) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        TeamIcon(abbr, if (size > 18) 36 else 23)
        Spacer(Modifier.width(9.dp))
        Text(name, color = TextPrimary, fontSize = size.sp, fontWeight = FontWeight.Bold,
             maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (score != null) {
            Spacer(Modifier.width(8.dp))
            Text(score.toString(), color = TextPrimary, fontSize = (size + 5).sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun MatchupHero(game: Game) {
    Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(4.dp)).padding(18.dp),
           verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(if (game.isMatchup) game.statusLabel() else "24/7 CHANNEL", color = Accent, fontSize = 13.sp, fontWeight = FontWeight.Bold)
        if (game.isMatchup) {
            CatalogTeam(game.leftTeam, game.leftAbbr, game.leftScore, 21)
            CatalogTeam(game.rightTeam, game.rightAbbr, game.rightScore, 21)
        } else Text(game.displayTitle, color = TextPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold)
        val info = listOf(game.broadcast, game.venue).filter { it.isNotBlank() && it != "null" }.joinToString(" · ")
        if (info.isNotEmpty()) Text(info, color = TextMuted, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
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
        colors = CardDefaults.colors(containerColor = SurfaceRaised, focusedContainerColor = Color(0xFF3B2A1D)),
        scale = CardDefaults.scale(focusedScale = 1.02f),
        border = CardDefaults.border(
            focusedBorder = Border(
                border = BorderStroke(2.dp, Accent),
                shape = RoundedCornerShape(4.dp),
            ),
        ),
    ) {
        Row(
            modifier = Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    "SOURCE ${index + 1}  ·  ${stream.name}",
                    color = TextPrimary,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(5.dp))
                Text(stream.displayMeta(), color = TextMuted, fontSize = 12.sp, maxLines = 1)
            }
            Text("▶", color = Accent, fontSize = 18.sp)
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
                    .background(Color(0xE6100E0C), RoundedCornerShape(12.dp))
                    .padding(horizontal = 32.dp, vertical = 20.dp),
            )
        }
    }
}

private sealed interface CatalogState {
    data object Searching : CatalogState
    data class Error(val title: String, val body: String) : CatalogState
    data class Ready(val base: String, val games: List<Game>) : CatalogState
    data class DeviceLogin(
        val base: String,
        val code: String? = null,
        val status: String = "Starting sign-in…",
        val error: String? = null,
    ) : CatalogState
}

private data class ConnectDialogState(
    val visible: Boolean = false,
    val busy: Boolean = false,
    val error: String? = null,
    val initialValue: String = "",
)

private data class PlaybackSelection(val game: Game, val stream: StreamSource)
