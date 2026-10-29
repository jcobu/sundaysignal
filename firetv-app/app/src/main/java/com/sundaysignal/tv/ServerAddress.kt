package com.sundaysignal.tv

import java.net.URI
import java.util.Locale

/** Public origins keep standard HTTP(S) ports; bare LAN addresses use 8765. */
internal fun normalizeServerUrl(raw: String): String? {
    val input = raw.trim()
    if (input.isEmpty() || input.any { it.isWhitespace() || it.code < 32 }) return null
    val explicitScheme = input.contains("://")
    val parsed = try { URI(if (explicitScheme) input else "http://$input") } catch (_: Exception) { return null }
    val host = parsed.host?.lowercase(Locale.ROOT) ?: return null
    if (parsed.rawUserInfo != null || parsed.port < -1 || parsed.port == 0 || parsed.port > 65535) return null
    val local = host == "localhost" || host.endsWith(".local") || !host.contains('.') ||
        host.contains(':') || Regex("[0-9]{1,3}(\\.[0-9]{1,3}){3}").matches(host)
    val scheme = if (explicitScheme) parsed.scheme.lowercase(Locale.ROOT)
        else if (local || (parsed.port != -1 && parsed.port != 443)) "http" else "https"
    if (scheme != "http" && scheme != "https") return null
    val port = if (parsed.port != -1) parsed.port else if (!explicitScheme && local) 8765 else -1
    val authority = host + if (port == -1) "" else ":$port"
    return "$scheme://$authority${parsed.rawPath.orEmpty().trimEnd('/')}"
}
