# SundaySignal for Fire TV

Native remote-first Fire OS / Android TV client for a SundaySignal server on the
same local network.

Version 2 uses Kotlin, Compose for TV, TV Material focus components, and a
Media3 media session while preserving compatibility with the existing server.

## Features

- Discovers `/api/health` on port 8765 across the Fire TV's local `/24` network
- Remembers the last working server
- Lists one focused card per playable game
- Shows every available provider stream for the selected game in a second focus column
- Bundles all 32 NFL team icons so matchup art works without internet image requests
- Uses explicit left/right focus paths and restores the previously selected source
- Keeps all important controls inside the TV overscan-safe area
- Opens the proxied HLS feed in a dedicated edge-to-edge Media3/ExoPlayer view
- Back returns to the same focused game in the library
- Menu button or **Find Server** triggers discovery again
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

The Fire TV and SundaySignal Docker host must be reachable on the same `/24`
LAN. Guest-network or client-isolation settings will prevent discovery and
playback.
