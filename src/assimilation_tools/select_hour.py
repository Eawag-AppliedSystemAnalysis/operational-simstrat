"""Choose a lake's analysis hour from the operational free run and its raw record.

    python src/assimilation_tools/select_hour.py upperlugano                 # print
    python src/assimilation_tools/select_hour.py upperlugano --write         # -> observations.schedule
    python src/assimilation_tools/select_hour.py aegeri --window 23-6        # candidate hours (wraps)

The hour is frozen into the assimilated series: changing it changes the series, so refit sigma
afterwards (fit_sigma.py).
"""
import common

from assimilator.fitting.hour import select_hour, CRITERIA


def main():
    ap = common.parser(__doc__.split("\n")[0])
    ap.add_argument("--raw", help="raw sub-daily CSV (default: the run's raw store, else fetched)")
    ap.add_argument("--free-run", help="free-run T_out.dat (default: runs/<lake>/Results/T_out.dat)")
    ap.add_argument("--window", default="0-6", help="candidate hours LO-HI (inclusive, may wrap), or 'all'")
    ap.add_argument("--criterion", default="balanced", choices=CRITERIA)
    ap.add_argument("--max-offset", type=float, default=None,
                    help="hours a day's instant may be from the hour (default 1.5)")
    cli = ap.parse_args()
    common.setup_logging()

    window = None if cli.window in ("all", "any") else tuple(int(x) for x in cli.window.split("-"))
    cfg = common.lake_config(cli.lake)
    result = select_hour(common.raw_observations(cli.lake, cfg, cli.raw),
                         common.free_run(cli.lake, cli.free_run), window=window, criterion=cli.criterion)
    common.show(result)
    if cli.write:
        schedule = {"hour": result["hour"]}
        if cli.max_offset is not None:
            schedule["max_offset_h"] = cli.max_offset
        common.write_section(cli.lake, ["observations", "schedule"], schedule)


if __name__ == "__main__":
    main()
