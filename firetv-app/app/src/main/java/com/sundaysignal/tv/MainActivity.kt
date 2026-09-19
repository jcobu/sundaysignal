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

/** Thrown for a 401 from a Plex-gated endpoint, so callers can start the
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
    private var plexPollGeneration = 0

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
                    connectDialog = connectDialog,
                    onReconnect = ::discoverServer,
                    onOpenConnectDialog = ::openConnectDialog,
                    onCloseConnectDialog = ::closeConnectDialog,
                    onSubmitConnectDialog = ::connectToUrl,
                    onRetryPlexLogin = ::retryPlexLogin,
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
        plexPollGeneration++ // any (re)connect invalidates an in-flight Plex poll loop
        serverBase = base
        loadCatalog(base)
    }

    private fun openConnectDialog() {
        val prefill = (serverBase ?: "").removePrefix("http://").removePrefix("https://")
        connectDialog = ConnectDialogState(visible = true, initialValue = prefill)
    }

    private fun closeConnectDialog() {
        connectDialog = ConnectDialogState()
    }

    /** Directed connect to a user-typed address, instead of scanning the subnet. */
    private fun connectToUrl(raw: String) {
        val normalized = normalizeServerUrl(raw)
        if (normalized == null) {
            connectDialog = connectDialog.copy(error = "Enter an address, e.g. 192.168.1.50:8765")
            return
        }
        connectDialog = connectDialog.copy(busy = true, error = null)
        worker.execute {
            // A directed, user-typed connection isn't racing 254 other probes
            // like the subnet scan, so it can afford to wait longer for a
            // real round trip instead of the scan's fast 550ms timeout.
            val reachable = probe(normalized, timeoutMs = 3000)
            runOnUiThread {
                if (reachable) {
                    getPreferences(MODE_PRIVATE).edit().putString("serverBase", normalized).apply()
                    connectDialog = ConnectDialogState()
                    onServerFound(normalized)
                } else {
                    connectDialog = connectDialog.copy(
                        busy = false,
                        error = "Couldn't reach that address. Check it and that the server is running.",
                    )
                }
            }
        }
    }

    private fun normalizeServerUrl(raw: String): String? {
        var value = raw.trim()
        if (value.isEmpty()) return null
        if (!value.contains("://")) value = "http://$value"
        val uri = try {
            Uri.parse(value)
        } catch (_: Exception) {
            return null
        }
        val host = uri.host?.takeIf { it.isNotBlank() } ?: return null
        val scheme = uri.scheme?.lowercase(Locale.ROOT) ?: "http"
        if (scheme != "http" && scheme != "https") return null
        val port = if (uri.port != -1) uri.port else 8765
        return "$scheme://$host:$port"
    }

    private fun loadCatalog(base: String) {
        worker.execute {
            try {
                val root = getJson("$base/api/streams", 3500, base)
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
                            // display_title/display_left_*/is_matchup account for
                            // non-matchup listings (RedZone, NFL Network, etc.) that
                            // aren't two teams playing; fall back to the older fields
                            // for a server predating those.
                            displayTitle = source.optString("display_title", source.optString("title", "Game")),
                            isMatchup = source.optBoolean("is_matchup", true),
                            kickoff = source.optString("kickoff_local", ""),
                            status = source.optString("status_state", "Available"),
                            leftTeam = source.optString("display_left_team", source.optString("away_team", "")),
                            rightTeam = source.optString("display_right_team", source.optString("home_team", "")),
                            leftAbbr = source.optString("display_left_abbr", source.optString("away_abbr", "")),
                            rightAbbr = source.optString("display_right_abbr", source.optString("home_abbr", "")),
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
            } catch (authRequired: AuthRequiredException) {
                runOnUiThread { startPlexLogin(base) }
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

    private fun probe(base: String, timeoutMs: Int = 550): Boolean = try {
        val health = getJson("$base/api/health", timeoutMs)
        health.optBoolean("ok") && health.optString("service") == "SundaySignal"
    } catch (_: Exception) {
        false
    }

    /** /api/health and /api/streams stay reachable without a session; a
     * gated endpoint 401s instead, which callers catch specifically to
     * start the Plex login flow rather than treating it as a dead server. */
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

    private fun cookiePrefsKey(base: String) = "plexCookie::$base"

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

    /** Plex sign-in: same PIN as the web UI's popup flow, but shown as a
     * code to redeem at plex.tv/link from any other device — a Fire TV has
     * no browser or cookie jar to do the popup dance in. */
    private fun startPlexLogin(base: String) {
        clearSessionCookie(base)
        catalogState = CatalogState.PlexLogin(base)
        val generation = ++plexPollGeneration
        worker.execute {
            try {
                val pin = postJson("$base/auth/plex/pin", 5000, base)
                if (!pin.optBoolean("ok")) error(pin.optString("error", "Could not start Plex login"))
                val pinId = pin.getInt("id")
                val code = pin.getString("code")
                runOnUiThread {
                    if (generation == plexPollGeneration) {
                        catalogState = CatalogState.PlexLogin(
                            base = base,
                            code = code,
                            status = "Waiting for you to finish signing in on Plex…",
                        )
                    }
                }
                pollPlexLogin(base, pinId, generation)
            } catch (error: Exception) {
                runOnUiThread {
                    if (generation == plexPollGeneration) {
                        catalogState = CatalogState.PlexLogin(base, error = "Couldn't reach the server. Try again.")
                    }
                }
            }
        }
    }

    /** Runs on a worker thread; blocks it between polls the same way
     * discoverServer() already blocks a worker thread on completion.poll(). */
    private fun pollPlexLogin(base: String, pinId: Int, generation: Int) {
        // Plex's own PINs expire well before this; a client-side cap just
        // keeps a forgotten screen from polling forever.
        repeat(300) {
            if (generation != plexPollGeneration) return
            Thread.sleep(2000)
            if (generation != plexPollGeneration) return
            try {
                val poll = getJson("$base/auth/plex/poll/$pinId", 5000, base)
                if (!poll.optBoolean("ok")) {
                    runOnUiThread {
                        if (generation == plexPollGeneration) {
                            catalogState = CatalogState.PlexLogin(
                                base, error = poll.optString("error", "Something went wrong."),
                            )
                        }
                    }
                    return
                }
                if (poll.optBoolean("denied")) {
                    runOnUiThread {
                        if (generation == plexPollGeneration) {
                            catalogState = CatalogState.PlexLogin(
                                base,
                                error = poll.optString(
                                    "error", "This Plex account doesn't have access to this server.",
                                ),
                            )
                        }
                    }
                    return
                }
                if (poll.optBoolean("authenticated")) {
                    runOnUiThread { if (generation == plexPollGeneration) onServerFound(base) }
                    return
                }
                // else: not yet — keep polling
            } catch (_: Exception) {
                // transient network hiccup while polling — keep trying silently
            }
        }
        runOnUiThread {
            if (generation == plexPollGeneration) {
                catalogState = CatalogState.PlexLogin(base, error = "That code expired. Get a new one to try again.")
            }
        }
    }

    private fun retryPlexLogin() {
        (catalogState as? CatalogState.PlexLogin)?.let { startPlexLogin(it.base) }
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
    connectDialog: ConnectDialogState,
    onReconnect: () -> Unit,
    onOpenConnectDialog: () -> Unit,
    onCloseConnectDialog: () -> Unit,
    onSubmitConnectDialog: (String) -> Unit,
    onRetryPlexLogin: () -> Unit,
    onPlay: (PlaybackSelection) -> Unit,
    onClosePlayer: () -> Unit,
) {
    Box(Modifier.fillMaxSize().background(Background)) {
        when (state) {
            CatalogState.Searching -> MessageScreen(
                title = "Finding SundaySignal",
                body = "Searching your network on port 8765…",
                action = null,
                onEnterAddress = onOpenConnectDialog,
            )
            is CatalogState.Error -> MessageScreen(state.title, state.body, onReconnect, onOpenConnectDialog)
            is CatalogState.Ready -> BrowserScreen(state, restoreFocusToken, onReconnect, onOpenConnectDialog, onPlay)
            is CatalogState.PlexLogin -> PlexLoginScreen(state, onRetryPlexLogin, onOpenConnectDialog)
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
private fun PlexLoginScreen(state: CatalogState.PlexLogin, onRetry: () -> Unit, onEnterAddress: () -> Unit) {
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
        Text("Sign in with Plex", color = TextPrimary, fontSize = 28.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(10.dp))
        Text(
            "This server requires signing in with the Plex account it's shared with.",
            color = TextMuted,
            fontSize = 16.sp,
        )
        Spacer(Modifier.height(28.dp))
        if (state.code != null) {
            Text("On your phone or computer, go to", color = TextMuted, fontSize = 16.sp)
            Text("plex.tv/link", color = FocusBlue, fontSize = 20.sp, fontWeight = FontWeight.Bold)
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
        Header(state, onReconnect, onOpenConnectDialog)
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
private fun Header(state: CatalogState.Ready, onReconnect: () -> Unit, onChangeServer: () -> Unit) {
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
        Button(onClick = onChangeServer, contentPadding = PaddingValues(horizontal = 18.dp, vertical = 8.dp)) {
            Text("Change server", fontSize = 14.sp)
        }
        Spacer(Modifier.width(10.dp))
        Button(onClick = onReconnect, contentPadding = PaddingValues(horizontal = 18.dp, vertical = 8.dp)) {
            Text("Reconnect", fontSize = 14.sp)
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
        modifier = Modifier.fillMaxSize().background(Color(0xCC030916)),
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
                "Enter the SundaySignal address, e.g. 192.168.1.50:8765",
                color = TextMuted,
                fontSize = 14.sp,
            )
            Spacer(Modifier.height(18.dp))
            BasicTextField(
                value = text,
                onValueChange = { text = it },
                singleLine = true,
                textStyle = TextStyle(color = TextPrimary, fontSize = 18.sp),
                cursorBrush = SolidColor(FocusBlue),
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
            if (game.isMatchup) {
                TeamIcon(game.leftAbbr, 38)
                Spacer(Modifier.width(5.dp))
                TeamIcon(game.rightAbbr, 38)
                Spacer(Modifier.width(14.dp))
            }
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
        if (game.isMatchup) TeamIcon(game.leftAbbr, 58)
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            StackedMatchup(game, 19)
            Spacer(Modifier.height(5.dp))
            Text(game.displayMeta(), color = TextMuted, fontSize = 13.sp, maxLines = 1)
        }
        Spacer(Modifier.width(16.dp))
        if (game.isMatchup) TeamIcon(game.rightAbbr, 58)
    }
}

@Composable
private fun StackedMatchup(game: Game, textSize: Int) {
    if (!game.isMatchup) {
        Text(
            game.displayTitle,
            color = TextPrimary,
            fontSize = textSize.sp,
            fontWeight = FontWeight.Bold,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        return
    }
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
    data class PlexLogin(
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

private data class Game(
    val title: String,
    // Drops the "vs" wording for a listing that isn't really two teams
    // playing (RedZone, NFL Network, etc. still arrive through the same
    // "<a>-vs-<b>" URL shape the source sites use for real games) — see
    // isMatchup below.
    val displayTitle: String,
    val isMatchup: Boolean,
    val kickoff: String,
    val status: String,
    val leftTeam: String,
    val rightTeam: String,
    val leftAbbr: String,
    val rightAbbr: String,
    val streams: List<StreamSource>,
) {
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
