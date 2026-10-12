# The daily wake

The reader should show today's dashboard on its sleep screen by breakfast, and it should spend the rest of the day asleep. This page is about how [`device/power-events.sh`](../device/power-events.sh) gets there, and the wrong turns on the way.

## Why it's needed

The first version had a loop in `boot.sh`: run `sync.sh`, `sleep 3600`, repeat. It worked while the device was awake. Once it suspended, nothing ran, including the countdown inside `sleep`, which just picked up where it left off on the next wake. So the sleep screen showed whatever was current when someone last put the Kindle down. Usually yesterday.

The device needs to wake itself. The Kindle's power daemon, `powerd`, can do that: setting its `rtcWakeup` property arms the real-time clock to wake the device a given number of seconds later. Power state is all exposed through `lipc`, the Kindle's small IPC tool (`lipc-get-prop`, `lipc-set-prop`, `lipc-wait-event`), so the whole thing can be a shell script.

## The event loop

The script runs one long `lipc-wait-event` on powerd and reacts to three events:

| Event | What the script does |
|---|---|
| `readyToSuspend` | If a refresh is running, `deferSuspend 20` to hold off sleep. Otherwise set `rtcWakeup` to the seconds until the next 05:45 and record when that is. |
| `wakeupFromSuspend` | Start a refresh in the background. |
| `outOfScreenSaver` | Someone woke it by hand: sync in the background. |

`readyToSuspend` doesn't fire once. powerd sends it every few seconds for as long as it is waiting to suspend. The first listener treated it as a one-off: it re-armed the alarm on every tick and logged each one, which buried everything else in the log. Setting the same alarm again is harmless, so the fix was mostly about intent: a busy flag file decides whether a tick means "not yet" (`deferSuspend`) or "here's tomorrow's alarm", and the log only gets a line when the alarm value changes.

The wake time is computed in a fixed POSIX zone (`EST5EDT,M3.2.0,M11.1.0` by default), not the device's local zone. The local zone follows the owner around (see below), and a 05:45 that moves with it would drift relative to the server's 05:15 build.

## The refresh

After an alarm wake the device isn't really awake. It comes back in powerd's `screenSaver` state with the sleep screen still showing, and in that state WiFi won't associate. The first alarm test woke on time, waited, and never got online. The fix is three properties:

```sh
lipc-set-prop com.lab126.powerd wakeUp 1          # leave the half-awake state
lipc-set-prop com.lab126.cmd wirelessEnable 1     # KOReader turns WiFi off when it sleeps
lipc-set-prop com.lab126.wifid enable 1
```

Then it polls the server's `/health` every 3 seconds, for up to two minutes. Once online it runs `sync.sh`, posts the last 40 lines of its log to `POST /kindle/log` so each morning can be checked from the server while the device sleeps, and finally:

```sh
lipc-set-prop com.lab126.powerd powerButton 1
```

That's a software press of the power button. KOReader handles it the same way as a real press: it draws the sleep screen, which is now the new dashboard, and the device suspends. On the way down, `readyToSuspend` arms tomorrow's alarm.

A watchdog clears the busy flag after five minutes no matter what, so a hung download can't keep the device awake all day.

## Telling an alarm from a person

A wake from the alarm and a wake from someone picking the device up both arrive as `wakeupFromSuspend`. Pressing the power button on someone who just woke the Kindle to read would be a terrible bug, so the script checks two things before it treats a wake as its own:

- the state is still `screenSaver` (a person pressing the button gets the reader UI, not the screensaver), and
- the time is within 90 seconds of the alarm it set.

Anything else is a manual wake: sync quietly in the background, never press the button.

## The clock

The Kindle ships without a zoneinfo database. Its `/etc/localtime` points at `/var/local/system/tz`, which on this device didn't exist, so the clock ran on UTC. The server already knows the zone for the phone's location (Open-Meteo returns it alongside the forecast), so `/paper/editions.json` includes `tz`, and `/paper/tzfile` serves the compiled zoneinfo file from the Mac's own `/usr/share/zoneinfo`. `sync.sh` downloads it whenever the zone name changes and moves it into place.

## Testing it

Waiting for 05:45 to test a change gets old quickly. While a file named `test-wake` exists in the script's folder, every alarm is two minutes out; the next real wake deletes the file. One test run from the log: alarm set, device asleep, woke on the alarm, online about nine seconds later, synced, reported, back to sleep.

## Two scripting lessons

- **Replace running scripts atomically.** `sh` reads a script as it executes it. Overwriting `power-events.sh` in place while the listener was running handed the shell a mix of the old and new file. Deploys now write to a temp file and `mv` it over the original, so the running shell keeps its old copy and the next start gets the new one.
- **Don't pattern-kill over SSH.** `ssh reader 'pkill -f power-events.sh'` matches the remote shell running that very command line and kills the session before the restart happens. [`restart-listener.sh`](../device/restart-listener.sh) walks `/proc`, compares each process's exact command line, kills its children first, and then starts exactly one new listener with `setsid`.

## Battery

The device is awake for under a minute a day for the refresh, plus whatever time someone spends reading. The paper costs nothing extra because both downloads share the one WiFi session. When it's docked it stays charged anyway; off the dock, a day of light reading plus one short wake should leave the battery lasting weeks.
