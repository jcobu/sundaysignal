package com.sundaysignal.tv

import org.junit.Assert.*
import org.junit.Test

class CatalogModelsTest {
    private fun game(title: String = "Panthers vs Packers", matchup: Boolean = true,
                     streams: List<StreamSource> = emptyList()) = Game(
        title = title, displayTitle = title, isMatchup = matchup, kickoff = "Thu · 5:15 PM",
        status = "pre", leftTeam = "Carolina Panthers", rightTeam = "Green Bay Packers",
        leftAbbr = "CAR", rightAbbr = "GB", streams = streams,
    )
    @Test fun upcomingWithoutStreamsRemainsScheduled() {
        val g = game()
        assertEquals("Thu · 5:15 PM", g.statusLabel())
        assertEquals("Kickoff TBD", g.copy(kickoff = "").statusLabel())
        assertEquals(1, mergeCatalogChannels(listOf(g)).size)
    }
    @Test fun humanReadableLiveAndFinalStatus() {
        assertEquals("Q3 4:12 · In progress", game().copy(status = "in", period = 3, clock = "4:12").statusLabel())
        assertEquals("OT 2:10 · In progress", game().copy(status = "in", period = 5, clock = "2:10").statusLabel())
        assertEquals("Final", game().copy(status = "post").statusLabel())
        assertEquals("24/7 CHANNEL", game("NFL Network", false).statusLabel())
    }
    @Test fun duplicateChannelsKeepEveryUniqueAlternative() {
        val a = StreamSource("A", "GOLD", "hls", "https://example.com/a")
        val b = StreamSource("B", "HD", "hls", "https://example.com/b")
        val merged = mergeCatalogChannels(listOf(game("NFL NETWORK", false, listOf(a)),
            game("NFL Network vs Live", false, listOf(a,b)), game()))
        assertEquals(2, merged.size)
        assertEquals("NFL Network", merged[0].displayTitle)
        assertEquals(listOf(a,b), merged[0].streams)
    }
    @Test fun providerBadgesAreNotPresentedAsVerification() {
        val stream = StreamSource("A", "GOLD · HD", "hls", "https://example.com/a")
        assertEquals("Ready to try", stream.displayMeta())
        assertTrue(stream.copy(verified = true).displayMeta().startsWith("Checked by server"))
        assertTrue(stream.copy(stale = true).displayMeta().startsWith("Previously checked"))
    }
}
