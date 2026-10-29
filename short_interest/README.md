# What's getting shorted more? (FINRA data)

`finra_short.py` pulls FINRA's public short-selling data and ranks stocks by how
much shorting has grown over roughly the last month.

## Run it

```bash
pip install -r requirements.txt
python finra_short.py
```

It prints a report and writes to `output/`:

| File | What's in it |
| --- | --- |
| `report.md` | Top-N tables (below) |
| `short_interest_change.csv` | Every stock that passed the filters, with short interest at each report |
| `short_volume_trend.csv` | Every stock's daily short-volume ratio, early vs late in the window |

Raw downloads are cached in `data/`, so re-running is fast. Delete that folder to re-download.

## The two data sources

1. **Short interest** (FINRA API, `consolidatedShortInterest`): the number of shares
   held short, reported twice a month (around the 15th and month-end). FINRA
   publishes each report about 8 business days after its settlement date, so the
   newest figure is roughly two weeks old. By default the script compares the
   3 latest reports, which covers about one month.
2. **Daily short sale volume** (Reg SHO files on `cdn.finra.org`): how many of each
   day's traded shares were sold short. It's daily, but noisy, because market
   makers short constantly just to fill buy orders. Use it to back up the short
   interest numbers, not as a count of short positions.

## The tables in the report

- **Biggest % increase in short interest**: growth from the first to the last report.
- **Biggest increase in shares short**: same, in raw shares. This favors large companies.
- **Short interest up at every report**: rose steadily rather than in one jump.
- **Biggest rise in daily short-volume ratio**: % of volume sold short, second half of the window vs first half.
- **Rising on both signals**: short interest up *and* short-volume ratio up.

## Filters (change with flags)

- `--min-short-shares 1000000`: at least 1M shares short at the latest report
- `--min-avg-volume 250000`: at least 250k shares traded per day
- `--exclude-markets OTC`: drop over-the-counter names. The script prints the
  market codes it sees so you can adjust this.
- Stocks with a split during the window are always dropped, because a split
  inflates the share count.

Other flags: `--reports 5` (look back further), `--as-of 2026-10-01`, `--top 50`,
`--volume-days 0` (skip the daily volume download). Run `python finra_short.py --help`
for the full list.

## Caveats

- FINRA doesn't publish float or shares outstanding, so this measures *change* in
  shorting, not "% of float short". A 200% jump from a tiny base can matter less
  than a 20% jump in a heavily shorted name; check `days_to_cover` too.
- Public API access needs no account. If you get rate-limited, create a free
  account at developer.finra.org and set `FINRA_API_KEY` / `FINRA_API_SECRET`.

## Tests

```bash
python -m unittest test_finra_short.py
```
