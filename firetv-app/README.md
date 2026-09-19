# SundaySignal for Fire TV

Native remote-first Fire OS / Android TV client for a SundaySignal server,
local or remote.

Version 2 uses Kotlin, Compose for TV, TV Material focus components, and a
Media3 media session while preserving compatibility with the existing server.

## Features

- On first launch, prompts for the server's address — a LAN IP or a public
  domain — and remembers it afterward; every later launch just reconnects to
  that saved address. **Change server** (when connected) or **Enter address**
  (on an error) reopens the same dialog to switch servers
- If Plex login or an admin token is configured on the server, signs in with
  the same short-code flow the web UI's `/login` page uses
- Lists one focused card per playable game, matching the server's
  matchup/non-matchup distinction (RedZone, NFL Network, etc. show their own
  title instead of a "vs" between two blank team slots)
- Shows every available provider stream for the selected game in a second focus column
- Bundles all 32 NFL team icons so matchup art works without internet image requests
- Uses explicit left/right focus paths and restores the previously selected source
- Keeps all important controls inside the TV overscan-safe area
- Opens the proxied HLS feed in a dedicated edge-to-edge Media3/ExoPlayer view
- Back returns to the same focused game in the library
- Menu button or **Reconnect** re-checks the saved server
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

Uninstalling clears the app's saved server address, so you'll be prompted for
it again after reinstalling.

## Network requirement

There's no LAN scanning — the app always connects to a specific address you
type in once, then remembers. The Fire TV still needs an actual network path
to that address (same LAN, a public domain reachable over the internet, a
VPN, etc.); guest-network or client-isolation settings that block the
connection outright aren't something the app can work around.
