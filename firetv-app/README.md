# SundaySignal for Fire TV

Native remote-first Fire OS / Android TV client for a SundaySignal server on the
same local network.

Version 2 uses Kotlin, Compose for TV, TV Material focus components, and a
Media3 media session while preserving compatibility with the existing server.

## Features

- Discovers `/api/health` on port 8765 across the Fire TV's local `/24` network
- **Enter address** lets you type a server directly (IP, hostname, with or
  without a port — defaults to 8765) instead of waiting on the scan, for a
  server outside the local `/24` or when discovery doesn't find it
- Remembers the last working server; **Change server** (when connected) or
  **Enter address** (while searching or on an error) opens the same dialog
- Lists one focused card per playable game, matching the server's
  matchup/non-matchup distinction (RedZone, NFL Network, etc. show their own
  title instead of a "vs" between two blank team slots)
- Shows every available provider stream for the selected game in a second focus column
- Bundles all 32 NFL team icons so matchup art works without internet image requests
- Uses explicit left/right focus paths and restores the previously selected source
- Keeps all important controls inside the TV overscan-safe area
- Opens the proxied HLS feed in a dedicated edge-to-edge Media3/ExoPlayer view
- Back returns to the same focused game in the library
- Menu button or **Reconnect** triggers discovery again
- Uses the bundled SundaySignal icon and the `#112852` navy theme

## Build

```bash
cd firetv-app
JAVA_HOME=/opt/homebrew/opt/openjdk@17 \
ANDROID_SDK_ROOT=/opt/homebrew/share/android-commandlinetools \
./gradlew assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk`

## Sideload

Enable **ADB Debugging** and **Apps from Unknown Sources** under Fire TV
Developer Options. If Developer Options is hidden, select
**Settings → My Fire TV → About**, highlight the device name, and press Select
seven times.

Find the Fire TV IP under **About → Network**, then:

```bash
/opt/homebrew/share/android-commandlinetools/platform-tools/adb connect <fire-tv-ip>:5555
/opt/homebrew/share/android-commandlinetools/platform-tools/adb install -r ../SundaySignal-FireTV.apk
```

Accept the debugging prompt on the television. The app appears as
**SundaySignal** in the Fire TV app library.

Fire TV may retain launcher artwork when an APK is installed with `-r`. If an
older icon or banner remains after updating, perform one clean reinstall:

```bash
/opt/homebrew/share/android-commandlinetools/platform-tools/adb uninstall com.sundaysignal.tv
/opt/homebrew/share/android-commandlinetools/platform-tools/adb install ../SundaySignal-FireTV.apk
```

Uninstalling clears the app's saved server address, so run **Find Server** after
reinstalling.

## Network requirement

Automatic discovery scans the Fire TV's local `/24` LAN, so the Docker host
needs to be on that same subnet for it to find anything — guest-network or
client-isolation settings will prevent it. **Enter address** sidesteps the
scan itself, but the Fire TV still needs an actual network path to whatever
address you type (same LAN, VPN, etc.) — it isn't a way around isolation
that blocks the connection outright.
