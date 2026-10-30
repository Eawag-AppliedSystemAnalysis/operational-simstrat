"""QA plots for fit_perturbation.py --icon --check (ported from the submodule's former
notebooks/check_perturbations.py).

    check.png       grid points inside the lake outline + the ICON lake-mean series
    check_fit.png   per perturbed channel: residual ACF vs the fitted phi^k, residual distribution,
                    and a preview perturbed ensemble
"""
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                         # noqa: E402
import matplotlib.gridspec as gridspec                                  # noqa: E402

from assimilator.perturbate import _simulate_ar1                       # noqa: E402
from assimilator.fitting.perturbation import acf, PERTURB_VARS          # noqa: E402

LABELS = {"T_2M": "T_2M [K]", "U": "U [m/s]", "V": "V [m/s]", "GLOB": "GLOB [W/m²]"}


def _acquisition(lake, out_dir, geometry, flat, mean):
    from fit_perturbation import inside, _polygons
    pts = flat[["lat", "lon"]].drop_duplicates()
    m = inside(geometry, pts["lon"].values, pts["lat"].values)
    variables = [v for v in LABELS if v in mean.columns]
    fig = plt.figure(figsize=(14, 3 + 2.5 * len(variables)))
    gs = gridspec.GridSpec(len(variables), 2, figure=fig, width_ratios=[1, 2], hspace=0.5, wspace=0.35)
    ax = fig.add_subplot(gs[:, 0])
    for rings in _polygons(geometry):
        ring = np.asarray(rings[0])
        ax.fill(ring[:, 0], ring[:, 1], facecolor="lightskyblue", edgecolor="steelblue", alpha=0.4)
    ax.scatter(pts["lon"][~m], pts["lat"][~m], s=18, color="lightgrey", label="outside ({})".format((~m).sum()))
    ax.scatter(pts["lon"][m], pts["lat"][m], s=22, color="steelblue", label="inside ({})".format(m.sum()))
    ax.set_title("{} — grid point selection".format(lake), fontsize=10)
    ax.legend(fontsize=8)
    for row, var in enumerate(variables):
        a = fig.add_subplot(gs[row, 1])
        a.plot(mean["time"], mean[var], lw=0.8, color="steelblue")
        a.set_ylabel(LABELS[var], fontsize=8)
        a.tick_params(labelsize=7)
    path = os.path.join(out_dir, "check.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def _fit(lake, out_dir, fit_df, params, n_members=20, seed=42):
    n_prev = int(min(200, len(fit_df)))
    rng = np.random.default_rng(seed)
    fig = plt.figure(figsize=(15, 3.2 * len(PERTURB_VARS)))
    gs = gridspec.GridSpec(len(PERTURB_VARS), 3, figure=fig, hspace=0.5, wspace=0.3)
    for r, var in enumerate(PERTURB_VARS):
        phi, sigma = params[var]["phi"], params[var]["sigma"]
        resid = (fit_df["{}_icon".format(var)] - fit_df[var]).dropna().values
        std_r = float(resid.std())
        lags = np.arange(49)
        a = fig.add_subplot(gs[r, 0])
        a.bar(lags, acf(resid, 48), width=0.8, color="steelblue", alpha=0.7, label="residual ACF")
        a.plot(lags, phi ** lags, color="crimson", lw=1.5, label="AR(1) phi^k (phi={:.2f})".format(phi))
        a.set_title("{} — residual ACF".format(var), fontsize=10)
        a.legend(fontsize=7)
        a = fig.add_subplot(gs[r, 1])
        a.hist(resid, bins=60, density=True, color="steelblue", alpha=0.6)
        xs = np.linspace(resid.min(), resid.max(), 200)
        a.plot(xs, np.exp(-xs ** 2 / (2 * std_r ** 2)) / (std_r * np.sqrt(2 * np.pi)), color="crimson")
        a.set_title("{} — residual distribution".format(var), fontsize=10)
        a = fig.add_subplot(gs[r, 2])
        base = fit_df[var].values[:n_prev].astype(float)
        pert = _simulate_ar1(phi, sigma, n_prev, n_members, rng)
        ens = base[:, None] + pert
        if var == "GLOB":
            ens[base < 1.0, :] = 0.0
            ens = np.clip(ens, 0.0, None)
        a.plot(ens, color="steelblue", lw=0.3, alpha=0.35)
        a.plot(base, color="black", lw=1.2, label="control")
        a.set_title("{} — perturbed ensemble preview".format(var), fontsize=10)
        a.legend(fontsize=7)
    path = os.path.join(out_dir, "check_fit.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def check(lake, out_dir, geometry, flat, mean, fit_df, params):
    os.makedirs(out_dir, exist_ok=True)
    paths = [_acquisition(lake, out_dir, geometry, flat, mean), _fit(lake, out_dir, fit_df, params)]
    for p in paths:
        print("QA plot ->", p)
    return paths
