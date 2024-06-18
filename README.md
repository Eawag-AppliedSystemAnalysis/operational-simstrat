# Simstrat Operational

[Simstrat](https://github.com/Eawag-AppliedSystemAnalysis/Simstrat) is a one-dimensional physical lake model for simulation of stratification and mixing in deep stratified lakes. 
The aquatic research institute [Eawag](https://eawag.ch) uses Simstrat to operationally simulate lake conditions for a number of lakes across Switzerland.

The model is coupled with [AED2](https://github.com/AquaticEcoDynamics/libaed2) for biogeochemical simulations.

This repository automates the production of model inputs, running the simulations and publishing the outputs. 

## Installation

- Clone the repository to your local machine using the command: 

 `git clone git@github.com:Eawag-AppliedSystemAnalysis/operational-simstrat.git`
 
 Note that the repository will be copied to your current working directory.

- Use Python 3 and install the requirements with:

 `pip install -r requirements.txt`

If you want to run the simulations as part of the pipeline you need to install docker.

- Install [docker engine](https://docs.docker.com/desktop/)

## Quick start

> [!WARNING]  
> The pipeline uses meteorological and hydraulic data from third party providers (MeteoSwiss, BAFU). This data can only 
> be accessed while connected to the Eawag network.

To run the pipeline you must pass the name of an arguments file from the `args/` folder to `src/main.py`

e.g. to run arguments file `args/quickstart.json`

```bash
python src/main.py quickstart
```
Arguments can be overwritten on the command line by passing them as key=value pairs.

```bash
python src/main.py quickstart run=false
```

A full list of the arguments can be seen in `src/configuration.py`

## Add lakes

In order to add additional lakes their parameters must be added to `static/lake_parameter.json` in the format of the
lakes already there.

```JSON
 {
    "key": "greifensee",
    "name": "Greifensee",
    "elevation": 435.0,
    "surface_area": 8.5,
    "type": "Natural",
    "volume": 0.15,
    "max_depth": 32.0,
    "average_depth": 18.0,
    "residence_time": 400.0,
    "mixing_regime": "Monomictic",
    "geothermal_flux": 0.09,
    "latitude": 47.35,
    "longitude": 8.678,
    "trophic_state": "Eutrophic",
    "bathymetry_datalakes_id": 3,
    "sediment_oxygen_uptake_rate": -32.5,
    "forcing": [
      {
        "id": "SMA",
        "type": "meteoswiss_meteostation"
      }
    ],
    "forcing_forecast": {
      "source": "MeteoSwiss",
      "model": "COSMO",
      "days": 5
    },
    "a_seiche": 0.0056684225,
    "f_wind": 0.672030072,
    "p_lw": 0.952833972,
    "snow_temp": 2.02759986
  }
```

## Run Simulation

### Docker

The Simstrat docker image is available [here.](https://hub.docker.com/r/eawag/simstrat)

```bash
cd {{ run_folder }}
docker run -v $(pwd):/simstrat/run eawag/simstrat:3.0.4 Settings.par
```

### Locally compiled

See the instructions [here](https://github.com/Eawag-AppliedSystemAnalysis/Simstrat) for details.