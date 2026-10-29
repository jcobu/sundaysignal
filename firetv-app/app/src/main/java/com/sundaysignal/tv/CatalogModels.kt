package com.sundaysignal.tv

internal data class Game(
    val title: String,
    val id: String = title,
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
    val leftScore: Int? = null,
    val rightScore: Int? = null,
    val broadcast: String = "",
    val venue: String = "",
    val statusDetail: String = "",
    val period: Int = 0,
    val clock: String = "",
) {
    fun statusLabel(): String = when {
        !isMatchup -> if (status == "in") "LIVE · Games in progress" else "Off air · Available during games"
        status == "in" -> if (period > 0 && clock.isNotBlank()) "${if (period > 4) "OT" else "Q$period"} $clock · In progress" else "In progress"
        status == "post" -> "Final"
        kickoff.isNotBlank() -> kickoff
        else -> "Kickoff TBD"
    }

    fun displayMeta(): String = statusLabel()
}

internal data class StreamSource(
    val name: String,
    val badges: String,
    val sourceType: String,
    val playUrl: String,
    val verified: Boolean = false,
    val stale: Boolean = false,
) {
    fun displayMeta(): String = when {
        stale -> "Previously checked · Try another source if unavailable"
        verified -> "Checked by server · Ready to try"
        else -> "Ready to try"
    }
}


/** One entry per channel even when an older server returns multiple providers. */
internal fun mergeCatalogChannels(games: List<Game>): List<Game> {
    val result = mutableListOf<Game>()
    val positions = mutableMapOf<String, Int>()
    for (game in games) {
        val title = game.displayTitle.replace(Regex("\\s+vs\\.?\\s+live$", RegexOption.IGNORE_CASE), "").trim()
        val key = title.lowercase().replace(" ", "")
        if (game.isMatchup || key !in listOf("nflredzone", "nflnetwork")) {
            result += game
            continue
        }
        val label = if (key == "nflredzone") "NFL RedZone" else "NFL Network"
        val existing = positions[key]
        if (existing == null) {
            positions[key] = result.size
            result += game.copy(id = "channel:$key", displayTitle = label)
        } else {
            val first = result[existing]
            result[existing] = first.copy(streams = (first.streams + game.streams).distinctBy { it.playUrl })
        }
    }
    return result
}
