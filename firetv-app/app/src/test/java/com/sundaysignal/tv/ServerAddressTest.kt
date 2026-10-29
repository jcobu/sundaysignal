package com.sundaysignal.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerAddressTest {
    @Test fun reverseProxyUsesStandardHttpsPort() {
        assertEquals("https://sundaysignal.example.com", normalizeServerUrl("https://sundaysignal.example.com"))
        assertEquals("https://sundaysignal.example.com", normalizeServerUrl("sundaysignal.example.com/"))
        assertEquals("http://example.com", normalizeServerUrl("http://example.com"))
    }
    @Test fun explicitPortsArePreserved() {
        assertEquals("https://example.com:8443", normalizeServerUrl("https://example.com:8443"))
        assertEquals("http://192.168.1.50:9000", normalizeServerUrl("192.168.1.50:9000"))
        assertEquals("https://example.com:443", normalizeServerUrl("example.com:443"))
    }
    @Test fun bareLocalAddressesRetainLanDefault() {
        assertEquals("http://192.168.1.50:8765", normalizeServerUrl("192.168.1.50"))
        assertEquals("http://localhost:8765", normalizeServerUrl("localhost"))
        assertEquals("http://server.local:8765", normalizeServerUrl("server.local"))
        assertEquals("http://[::1]:8765", normalizeServerUrl("[::1]"))
    }
    @Test fun explicitSchemesPathsAndIpv6ArePreserved() {
        assertEquals("https://example.com/sundaysignal", normalizeServerUrl(" https://example.com/sundaysignal/#/settings "))
        assertEquals("https://[2001:db8::1]", normalizeServerUrl("https://[2001:db8::1]"))
        assertEquals("https://192.168.1.50", normalizeServerUrl("https://192.168.1.50"))
    }
    @Test fun malformedAddressesRejected() {
        for (address in listOf("", "https://", "ftp://example.com", "https://user:pass@example.com", "https://example.com:70000", "https://example.com:0", "https://bad host.com")) {
            assertNull(address, normalizeServerUrl(address))
        }
    }
}
