# Kiran's Paper

A Kindle that does two quiet things. Every morning it gets a small personal newspaper, built overnight on a computer at home. And when it's asleep, the sleep screen is a one-page dashboard for the day: date, weather, reminders, work reminders, and a quote. E-ink holds that image at zero power, so it sits on the desk like a printed page.

<p align="center">
  <img src="media/photo-hero.jpg" alt="The morning dashboard on a Kindle's sleep screen, on a charging stand" width="440">
</p>

> Photos show the real device with demo content on screen. Every other image is rendered from made-up data.

The device is a 2021 Kindle Paperwhite running [KOReader](https://koreader.rocks). The server side is about 3,000 lines of standard-library Python on a Mac mini. Nothing is exposed to the internet.

<p align="center">
  <img src="media/photo-desk.jpg" alt="The Kindle on its stand on a desk" width="360">
  <img src="media/photo-home.jpg" alt="KOReader home folder with Books, Comics, Kids, Manga, Newspaper and Homepage" width="300">
</p>

Asleep, it's a desk display. Awake, it's an ordinary e-reader, with one folder per kind of reading plus the newspaper and the dashboard.

## The newspaper

<img src="media/paper-front.png" alt="Front page of a demo edition" width="300" align="right">

Each edition is an EPUB. The front page works as a homepage: one sentence per story, and tapping it opens the full article.

- **World:** the one story that several major outlets are all leading with.
- **Local:** the most significant civic story for wherever the owner's phone is that day.
- **Markets:** indexes, rates and commodities, plus a short note on why things moved. Advice is filtered out after the model runs.
- **Science:** one new finding.
- **Sports:** scores, and a "worth watching" box for close or dramatic games. Those scores are hidden on a separate page, so a replay isn't spoiled.

Summaries come from a local language model on a home GPU server. Every step has a plain fallback, so the paper still arrives when the model is busy. Sections with nothing to report are left out.

<br clear="right">

<p align="center">
  <img src="media/paper-sports.png" alt="Sports page with a flagged game's score hidden" width="280">
  <img src="media/paper-markets.png" alt="Markets page" width="280">
</p>

## How it works

<p align="center">
  <img src="media/architecture.svg" alt="Phone location and public sources feed the builders on a Mac mini; a small server hands the results to the Kindle over Tailscale" width="900">
</p>

| Time (Eastern) | What happens |
|---|---|
| 05:15 | The mini builds the day's newspaper and keeps the last seven. |
| 05:30 | Headless Chrome renders the dashboard at the Kindle's exact resolution, in grayscale. |
| 05:45 | The Kindle wakes on its own alarm, downloads both, and goes back to sleep. |

- **Location follows a phone.** Home Assistant reports where the phone is. The weather, local news and even the Kindle's clock follow it.
- **Reminders come from Apple Reminders.** One list fills the left column and a Work list fills the right.
- **No dependencies.** Python's standard library plus tools macOS ships. The EPUB is assembled by hand and passes `epubcheck`.
- **Private by default.** The server only answers private addresses with a token, and the Kindle reaches it over Tailscale.

## The morning wake

A sleeping Kindle runs nothing, so a simple hourly refresh just pauses overnight. Instead, a small script on the reader sets a hardware wake alarm each time it goes to sleep. When the alarm fires, the Kindle comes back half awake with WiFi off. The script wakes it fully, turns WiFi on, syncs, sends its log home, and puts it back to sleep with the new dashboard showing. It takes under a minute, and it leaves the device alone if someone picks it up and presses the button instead. Details are in [docs/power.md](docs/power.md).

## A few things that broke

- **The model stopped answering for a day.** Another app on the shared GPU was holding most of its memory. The fallbacks kept the paper coming, which is why they exist.
- **The dashboard was always a day old.** Timers pause while the device sleeps. That led to the wake alarm above.
- **WiFi wouldn't come up after the alarm.** The reader switches it off at sleep and won't run it in the half-awake state, so the script now wakes the device fully first.
- **The clock was on UTC.** The device ships without time zone data. The server now sends the right zone file for the phone's location.

## Try it

The demo needs only Python 3.9 or newer:

```bash
python3 server/paper.py build --demo
```

It builds a sample edition from made-up stories into `server/out/demo/`. For a real setup, start from `config/brain-api.env.example` and [docs/architecture.md](docs/architecture.md). The dashboard render currently needs macOS.

## More

- [docs/architecture.md](docs/architecture.md): files, daily timeline, endpoints
- [docs/decisions.md](docs/decisions.md): why it's built this way
- [docs/power.md](docs/power.md): the morning wake in detail

MIT license.
