# Browser smoke tests

Playwright (Python) checks of the real UI in Chromium, Firefox and WebKit, each
at desktop size (1280x800) and on emulated iPhone 15 and Pixel 7 devices. They
are separate from `python manage.py test`: this directory has no `__init__.py`
and its files are named `smoke_*.py`, so Django's test discovery skips them.

## Run

Needs Docker only.

```bash
browser_tests/run.sh                     # all engines and devices
browser_tests/run.sh -k widget           # extra arguments go to pytest
browser_tests/run.sh -k "pipeline and firefox" -x
BROWSERS=webkit browser_tests/run.sh     # a subset of engines
```

`run.sh`:

1. builds the app image from the project `Dockerfile`;
2. starts it on a private Docker network as `http://travel-os:8000` with
   `DEBUG=False`, demo data (`seed_demo`), `NUM_PROXIES=0`, high throttle
   limits and the rule-based AI (`AI_ENABLED=false`). It runs
   `manage.py runserver --insecure`, which serves `/static/` with DEBUG off
   (production uses nginx for that; whitenoise is not installed);
3. points the seeded "Scared Travel" website's domain at
   `partner-site.test:8080`, reads its widget key from the database, and
   serves a test page at `http://www.partner-site.test:8080` that embeds
   `widget.js`, so the widget makes genuine cross-origin, CORS-checked calls;
4. runs pytest in `mcr.microsoft.com/playwright/python:v1.63.0-noble`;
5. removes every container, network and image it created (the Playwright
   image is kept for the next run).

A full run takes about five minutes.

## What is covered

| Scenario | Devices |
|---|---|
| Staff login (bad then good password) | all |
| Dashboard styled by `app.css` (computed styles, not just text) | all |
| Inbox picks up a new widget chat without a page reload | all |
| Pipeline: drag a card to another column, persisted across reload | desktop |
| Pipeline: move a card with its status menu, persisted across reload | phones |
| Widget on the partner site: chat, recommendation cards, *Book this*, inline form, pay link, simulated payment, confirmation back in the chat | all |
| Widget layout: floating panel on desktop, full screen on phones | all |
| Public pay page fits the screen with no horizontal scroll | all |

Firefox has no `isMobile` emulation, so on Firefox the phone profiles use the
device's viewport, pixel ratio, touch and user agent only.

## Output

`browser_tests/artifacts/` (git-ignored) gets screenshots named
`<engine>-<device>-<scenario>-<nn>-<step>.png`, `junit.xml` and the app's log
(`app.log`).
