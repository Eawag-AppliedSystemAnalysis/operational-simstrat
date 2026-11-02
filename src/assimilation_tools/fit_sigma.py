"""Fit a lake's observation error sigma_obs on the series its runs assimilate.

    python src/assimilation_tools/fit_sigma.py upperlugano                   # print
    python src/assimilation_tools/fit_sigma.py upperlugano --write           # -> sigma_obs

The series is made from the raw record with the lake's current "observations" settings and the
same function the operational pipeline uses, so the fit always matches what is assimilated.
Refit after any change to the filter, the schedule or the depth grid.
"""
import common

from assimilator.fitting.sigma import fit_sigma


def main():
    ap = common.parser(__doc__.split("\n")[0])
    ap.add_argument("--raw", help="raw sub-daily CSV (default: the run's raw store, else fetched)")
    ap.add_argument("--free-run", help="hourly free-run T_out.dat (default: the one make_free_run.py writes, "
                                       "runs/assimilation_tools/<lake>/free_run/Results/T_out.dat)")
    ap.add_argument("--year", type=int, help="restrict to one calendar year")
    ap.add_argument("--max-depth", type=float, help="ignore observations below this depth (m)")
    ap.add_argument("--annual", action="store_true",
                    help="fit one annual value instead of the {mixed, stratified} pair")
    cli = ap.parse_args()
    common.setup_logging()

    cfg = common.lake_config(cli.lake)
    series = common.assimilated_series(common.raw_observations(cli.lake, cfg, cli.raw), cfg)
    result = fit_sigma(series, common.free_run(cli.lake, cli.free_run), year=cli.year,
                       max_depth=cli.max_depth, seasonal=not cli.annual)
    common.show(result)
    if cli.write:
        common.write_section(cli.lake, ["sigma_obs"], result["sigma_obs"])


if __name__ == "__main__":
    main()
