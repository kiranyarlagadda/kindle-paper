# Kiran's Paper

[![CI](https://github.com/kiranyarlagadda/kindle-paper/actions/workflows/ci.yml/badge.svg)](https://github.com/kiranyarlagadda/kindle-paper/actions/workflows/ci.yml)

A daily dashboard, and an e-reader that goes anywhere.

The goal was a low-power e-ink dashboard that could do more than one thing. A Kindle was the best bet: cheap, a lot of bang for the buck, and easy to find. It sits on the desk showing the useful stuff for the day at a glance, and picked up, it's a normal e-reader.

<p align="center">
  <img src="media/photo-hero.jpg" alt="The morning dashboard on a Kindle's sleep screen, on a charging stand" width="440">
</p>

> The photos are the real device, with demo content on the screen. The other images are rendered from made-up data.

It's a 2021 Kindle Paperwhite running [KOReader](https://koreader.rocks). A Mac mini at home builds everything it shows, using plain Python.

## The lock screen

The favorite part. E-ink holds an image without using any power, so the lock screen doubles as a dashboard. Every morning it shows:

- the date, and the weather for wherever the phone is
- reminders and work reminders, pulled from Apple Reminders
- a quote, rotating through philosophers and writers

One glance at the desk shows the day's action items and the weather.

<p align="center">
  <img src="media/photo-desk.jpg" alt="The Kindle on its stand on a desk" width="360">
  <img src="media/photo-home.jpg" alt="KOReader home folder with Books, Comics, Kids, Manga, Newspaper and Homepage" width="300">
</p>

Awake, it has a folder for each kind of reading, plus a daily newspaper.

## The newspaper

<img src="media/paper-front.png" alt="Front page of a demo edition" width="300" align="right">

Each morning the Kindle also gets a small newspaper as an EPUB. The front page has one line per story, and tapping a line opens the full article. It has a world story, a local story for the current location, markets, a science story, and sports.

The sports page has a "worth watching" box for close games. Those scores are hidden, so a replay isn't spoiled.

A local AI model on a home server writes the summaries. If it's busy, the paper still comes out, just plainer.

<br clear="right">

## How it works

<p align="center">
  <img src="media/architecture.svg" alt="Phone location and public sources feed the builders on a Mac mini; a small server hands the results to the Kindle over Tailscale" width="900">
</p>

Every morning:

1. **5:15:** the Mac mini builds the newspaper.
2. **5:30:** it renders the dashboard as an image the exact size of the Kindle's screen.
3. **5:45:** the Kindle wakes itself up, downloads both, and goes back to sleep. The whole thing takes about a minute.

The weather, the local news and the Kindle's clock all follow the phone's location, so nothing needs changing when traveling. The Kindle talks to the Mac mini over Tailscale, so it works away from home too.

## The hard part

Getting SSH and Tailscale working on the Kindle was the most annoying part. At first it couldn't be reached at all, even on the same network. It turned out the router kept WiFi devices from talking to each other. Tailscale got around that, because the Kindle connects out instead of waiting for connections.

The morning wake-up was the other tricky bit. A sleeping Kindle runs nothing, and after its alarm it comes back half awake with WiFi off. The script has to wake it fully before it can sync. More in [docs/power.md](docs/power.md).

## How I built it

I came up with the idea and the design, and decided what goes on the dashboard. I built it with [Claude Code](https://claude.com/claude-code), which helped diagnose problems and write a lot of the scripts.

## Try it

The demo only needs Python 3.9 or newer:

```bash
python3 server/paper.py build --demo
```

That builds a sample newspaper from made-up stories into `server/out/demo/`. The tests run the same way, with nothing to install:

```bash
python3 -m unittest discover -s tests
```

 For a real setup, start with `config/brain-api.env.example` and [docs/architecture.md](docs/architecture.md). The dashboard render currently needs macOS.

## More

- [docs/architecture.md](docs/architecture.md): the files and how they fit together
- [docs/decisions.md](docs/decisions.md): why it's built this way
- [docs/power.md](docs/power.md): the morning wake-up in detail

MIT license.
