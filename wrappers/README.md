# CRAB wrappers

Shared wrappers for [CRAB](https://github.com/HLC-Lab/CRAB). A wrapper tells CRAB how to launch
one application and how to turn its output into measurements. There is one wrapper per
application, kept here so that everyone who runs that application parses its output the same
way.

## Using them

CRAB looks for a config's wrapper `path` (for example `blink/a2a_b.py`) in these folders, in
this order:

1. `local/wrappers/` inside your CRAB checkout, for private or in-progress wrappers (git-ignored);
2. each folder listed in `CRAB_PATH_WRAPPERS` (separate several with `:`), or the checkout's
   `wrappers/` folder when that variable is not set.

Clone this repository to one of those places, for example as `wrappers/` inside your CRAB
checkout. `crab wrappers list` shows what CRAB finds and where each binary comes from.

## Layout

```
<app>/
  <wrapper>.py          one or more wrappers for the application
  _<helper>.py          shared code for that app's wrappers (not a wrapper itself)
  README.md             what it measures, how to install the app, where the samples come from
  samples/<case>/       real output and the values the wrapper must produce
    case.json           {"wrapper": "<wrapper>.py", "args": "...", "set": {...}}
    stdout.txt          the application's output (plus stderr.txt and any files it wrote)
    expected.json       the rows the wrapper must return for that output
```

## Verified and unverified wrappers

A wrapper is verified when at least one sample case covers it: `crab wrappers test <app>` runs
the wrapper on the saved output and compares the result with `expected.json`. The wrappers
listed in `unverified.txt` came from CRAB before this repository existed and do not have sample
cases yet. They work as before, but nothing checks their parsing yet.

| App | Wrappers | Status |
|---|---|---|
| `blink` | 26 | unverified |
| `graph500` | 1 | unverified |
| `quantum-espresso` | 4 (pw and ph, v6 and v7) | unverified |
| `others` | 40 | unverified |

## Contributing

New wrappers and fixes come in as pull requests; see [CONTRIBUTING.md](CONTRIBUTING.md). A
pull request is merged when its sample cases pass in CI.

## License

MIT, see [LICENSE](LICENSE).
