# Design decisions

Why it ended up the way it is, roughly in the order the questions came up.

## A purpose-built page, not a dashboard screenshot

There was already a live dashboard on the desktop, and the easy move was to screenshot it for the Kindle. Picture it on e-ink, though: dense, built for color and constant refreshes, mostly numbers that are stale by lunch. E-ink wants the opposite. So the Kindle gets one calm page a day, with big serif numerals and newspaper rules, and the live stuff stays on the desktop.

## Not a browser-based dashboard on the device

There are good projects that keep a Home Assistant dashboard open in the Kindle's browser. Keeping a browser awake throws away the deep sleep that makes e-ink worth having, and Home Assistant doesn't hold most of what goes on this page anyway (notes, tasks, news). Home Assistant is still in the picture, but only as the source of the phone's location.

## The sleep screen is the dashboard

Early on the plan was a separate "dashboard mode" that would take over the screen. It turned out KOReader already does the useful half: when the device sleeps, it shows an image of your choosing. Pointing that at the morning page means the reader is a normal e-reader when it's awake and a desk display when it isn't, with no mode switch and no extra battery cost.

## An EPUB instead of more images

The morning page is a single PNG because the reader just has to show it. A newspaper is something you read, tap through and keep for a few days, so it's a real ebook. EPUB internal links are what make the front page work as a homepage: one sentence per story, tap it to read the rest.

## Standard library only

The machine that runs this also runs a dozen other things, and I didn't want a virtualenv to babysit. Everything is Python's standard library plus tools macOS already ships. The system Python on the mini is 3.9, so the code sticks to 3.9 syntax. Building the EPUB by hand with `zipfile` was less work than expected once the `mimetype` ordering rule was right.

## Consensus for the world story

The goal was what happened, not one outlet's take on it. Taking the story that three independent feeds all carry is a crude proxy for importance, and it works better than any single feed. Headlines and summaries are reduced to keyword sets, grouped by overlap, and ranked by how many outlets are in the group, then by how high each outlet placed it. The model writes the result in a flat wire-service tone and the page credits every source.

## The model is optional, everywhere

Every job the model does has a dumb fallback: the first item and its feed summary, a sentence built from the numbers, a line built from the watchability reasons. The GPU is shared with other work, so the paper has to survive the model being slow, busy or off. A shared time budget caps the whole build, and three failures in a row turn the model off for the rest of the run instead of waiting out more timeouts.

## Checks after the model, not just instructions before it

Prompts are suggestions. Two places get a hard check on the model's output: the markets text is matched against an advice pattern (buy, sell, hold, price targets, "you should", forecasts), and the worth-watching lines against a spoiler pattern (won, beat, clinched, scores). Either one failing replaces the text with a templated line. A third check is cosmetic: every printed string goes through a function that rewrites long dashes, because the model loves them and the house style doesn't.

## Spoilers are hidden by structure, not by styling

A flagged game's score isn't hidden behind a tap-to-reveal box on the same page, because e-ink page turns and search would find it. It's on its own page, outside the reading order (`linear="no"`), reachable only from one link. Headlines that name a flagged team are kept out of the news picks. The one known gap: standings tables still update with the result.

## Location from a phone, nothing hardcoded

The owner moves around enough that a configured town would be wrong for weeks at a time, and a config switch is something you forget to flip. The phone already reports its location to Home Assistant, so the server reads that, turns it into a city and county, and the weather, local news and the device's clock follow. Fixed coordinates are only a fallback for when Home Assistant has no answer. Logs name the city, never the coordinates.

## Private by default

None of this is reachable from the internet. The server answers only private and tailnet addresses, and only with a token. Before the phone was pointed at Home Assistant for location, the setup was checked for port forwards, tunnels or cloud relays pointing at it. When the reader is away from home it reaches the server over Tailscale, running in userspace mode on the device so it needs no kernel networking support.

## A wake alarm instead of staying awake

Battery life is the reason to use e-ink at all, so the reader never polls. It sleeps, wakes once on a hardware alarm, does a minute of work and sleeps again. The details are in [power.md](power.md).
