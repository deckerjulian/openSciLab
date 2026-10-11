# openSciLab Bridge (Android)

The app that runs on the Rigol DHO800/900 oscilloscope (Android inside). It implements the
bridge of `docs/protocols.md` ("Bridge", protocol 1):

* **TCP 5560**: SCPI lines. `:BRIDge:*` is answered by the app; everything else goes to the
  instrument's own SCPI server on `127.0.0.1:5555`, and its answers (lines or `#` blocks) back.
* **UDP 5561**: every 2 s a broadcast `OPENSCILAB_BRIDGE <version> <tcp port> <model> <serial>`
  (model and serial from `*IDN?`).
* **Snapshot cache**: `:BRIDge:SNAPshot` reads the channels asked for of the stopped acquisition
  block by block (`:WAVeform:DATA?`, RAW, BYTE: the top 8 of the 12 ADC bits) into files, builds
  the min/max (analog) and AND/OR (digital) pyramid, and serves `:OVERview?` and compressed
  `:TILE?` from there. Each channel's own preamble is read; channels sampled at different rates
  or with the trigger at another sample are refused (`ERR`), since a snapshot has one rate.

Kotlin without third-party libraries. `minSdk` 25 (the DHO900 runs Android 7.1.2, measured on a
DHO924S), `targetSdk` 34.

## Layout

| Path | Content |
| --- | --- |
| `core/` | Plain Kotlin/JVM, no Android: codec, SCPI blocks and messages, command parser, pyramid, cache, waveform reader, TCP server, beacon. Tested with `./gradlew :core:test`. |
| `core/src/test/` | Unit tests, including the shared vectors `tests/fixtures/bridge_codec.json` (copied in by Gradle) and an end-to-end test against a fake DHO SCPI server. |
| `app/` | Android: foreground service, status activity, boot receiver. |

## Build

You need JDK 17 and the Android SDK (platform 34, build-tools 34). For example, on macOS:

```bash
brew install openjdk@17
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
# Android command line tools in ~/Library/Android/sdk/cmdline-tools/latest, then:
sdkmanager "platforms;android-34" "build-tools;34.0.0" "platform-tools"
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties   # or set ANDROID_HOME
```

```bash
./gradlew test            # JVM unit tests (core)
./gradlew assembleDebug   # app/build/outputs/apk/debug/app-debug.apk
../build_all.sh --bridge  # release build -> firmware/apk/openSciLab-bridge.apk
```

The release build is signed with the debug key, so it installs without a keystore. Each machine
(and each CI run) has its own debug key: when you switch between builds from different machines,
uninstall the old app first (`adb uninstall org.openscilab.bridge`).

## Install

Turn on adb on the oscilloscope (on the DHO it is reachable over the LAN, port 55555; 5555 is
its SCPI server), then:

```bash
adb connect <scope-ip>:55555
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n org.openscilab.bridge/.MainActivity
```

## First run

1. Open *openSciLab Bridge* (the `am start` above, or from the launcher) and press **Start**
   (without a touch on the screen: `adb shell am start -n org.openscilab.bridge/.MainActivity --ez start true`;
   openSciLab does this when it installs the app). The
   status shows the addresses, whether the instrument's SCPI server answers, model and serial,
   clients, beacons and the cache.
2. Once started, the bridge comes back by itself after the oscilloscope reboots. **Stop** turns
   that off.
3. Check it from the PC:

   ```bash
   printf ':BRIDge:VERSion?\n*IDN?\n' | nc <scope-ip> 5560
   # OPENSCILAB_BRIDGE,0.1.0,1
   # RIGOL TECHNOLOGIES,DHO924S,...
   ```

4. Measure the local read rate: `:BRIDge:BENCH? 10000000` (bytes per second; needs a stopped
   acquisition with data on the current `:WAVeform:SOURce`).

Snapshots are kept in `Android/data/org.openscilab.bridge/files/snapshots/` on the shared
storage. The default limit is 1 GiB (`:BRIDge:CACHe:LIMit <bytes>`).

## Beyond the protocol

* An error is answered with one line `ERR <message>` (unknown `:BRIDge:` command, bad
  arguments, missing snapshot or channel, failing instrument), so the client never waits forever.
  A passed-through query that fails because the instrument cannot be reached is answered the same
  way (`ERR instrument: ...`).
* A block answer (`:OVERview?`, `:TILE?`) is checked first - a tile of up to 2M samples is encoded
  in memory before its header goes out - so a failure is still an `ERR` line. Should a block
  break off after its header all the same, the connection is closed: an `ERR` line there would
  be read as data.
* The cache always keeps the newest snapshot, even when it alone is larger than the limit.
