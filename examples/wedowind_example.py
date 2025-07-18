# Dataset citation: Ding, Y. (2021). Turbine Upgrade Dataset [Data set]. Zenodo. https://doi.org/10.5281/zenodo.5516556
# description of challenge https://relight.cloud/doc/turbine-upgrade-dataset-9zw1vl/turbineperformance
# description of challenge text copied below:
#
# Turbine Upgrade Dataset
# This turbine upgrade dataset includes three sets, one corresponding to an actual vortex generator installation and
# two corresponding to an artificial pitch angle adjustment. Two pairs of wind turbines from the same inland
# wind farm, as used in Chapter 5 of the Data Science for Wind Energy book, are chosen to provide the data, each pair
# consisting of two wind turbines, together with a nearby met mast. The turbine that undergoes an upgrade in a pair
# is referred to as the experimental turbine, the reference turbine, or the test turbine, whereas the one that does
# not have the upgrade is referred to as the control turbine. In both pairs, the test turbine and the control turbine
# are practically identical and were put into service at the same time. This wind farm is on a reasonably flat terrain.
#
# The power output, y, is measured on individual turbines, whereas the  environmental variables in x (i.e., the weather
# covariates) are measured  by sensors at the nearby mast. For this dataset, there are five variables in x and they
# are the same as those in the Inland Wind Farm Dataset1. For the vortex generator installation pair, there are 14
# months' worth of data in the period before the upgrade and around eight weeks of data after the upgrade. For the
# pitch angle adjustment pair, there are about eight months of data before the upgrade and eight and a half weeks
# after the upgrade.
#
# Note that the pitch angle adjustment is not physically carried out, but rather simulated on the respective test
# turbine. The following data modification is done to the test turbine data. The actual test turbine data, including
# both power production data and environmental measurements, are taken from the actual turbine pair operation. Then,
# the power production from the designated test turbine on the range of wind speed over 9 m/s is increased by 5%,
# namely multiplied by a factor of 1.05, while all other variables are kept the same. No data modification of any
# kind is done to the data affiliated with the control turbine in the pitch angle adjustment pair.
#
# The third column of a respective dataset is the upgrade status variable, of which a zero means the test turbine is
# not modified yet, while a one means that the test turbine is modified. The upgrade status has no impact on the
# control turbine, as the control turbine remains unmodified throughout. The vortex generator installation takes
# effect on June 20, 2011, and the pitch angle adjustment takes effect on April 25, 2011.

from __future__ import annotations

# =====================================================================================================================
# load pip-installed packages
# =====================================================================================================================

import logging
import math
import sys
import zipfile
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from pydantic import BaseModel, Field

# =====================================================================================================================
# load RES libraries
# =====================================================================================================================

from examples.helpers import format_and_print_results_table
from wind_up.constants import OUTPUT_DIR, PROJECTROOT_DIR, TIMESTAMP_COL, DataColumns
from wind_up.interface import AssessmentInputs # special variable because it's a dataclass
from wind_up.main_analysis import run_wind_up_analysis
from wind_up.models import Asset, PlotConfig, PrePost, Turbine, WindUpConfig
from wind_up.reanalysis_data import ReanalysisDataset
from wind_up.wind_funcs import calc_cp

sys.path.append(str(PROJECTROOT_DIR))
from examples.helpers import download_zenodo_data, setup_logger

if TYPE_CHECKING:
    import datetime as dt
    from pathlib import Path

# =====================================================================================================================
# Add directories
# =====================================================================================================================

CACHE_DIR = PROJECTROOT_DIR / "cache" / "wedowind_example"
ASSESSMENT_NAME = "wedowind_example"
ANALYSIS_OUTPUT_DIR = OUTPUT_DIR / ASSESSMENT_NAME
ZIP_FILENAME = "Turbine_Upgrade_Dataset.zip"

logger = logging.getLogger(__name__)

# =====================================================================================================================
# We Do Wind Specific classes
# =====================================================================================================================

class WeDoWindScadaColumns(Enum):
    Y_CTRL_NORM = "y_ctrl(normalized)"
    Y_TEST_NORM = "y_test(normalized)"
    UPGRADE_STATUS = "upgradestatus"
    WIND_SPEED = "V"
    WIND_DIRECTION = "D"


class WeDoWindTurbineNames(Enum):
    REF = "Ref"
    TEST = "Test"


class KeyDates(NamedTuple):
    analysis_first_dt_utc_start: dt.datetime
    upgrade_first_dt_utc_start: dt.datetime
    analysis_last_dt_utc_start: dt.datetime
    lt_first_dt_utc_start: dt.datetime #### lt = long term
    lt_last_dt_utc_start: dt.datetime
    detrend_first_dt_utc_start: dt.datetime
    detrend_last_dt_utc_start: dt.datetime
    pre_first_dt_utc_start: dt.datetime
    pre_last_dt_utc_start: dt.datetime
    post_first_dt_utc_start: dt.datetime
    post_last_dt_utc_start: dt.datetime


# =====================================================================================================================
# Data unpacking
# this includes reading zip file, construct test and ref turbine dataframes, and panda concatenating
# =====================================================================================================================


class WeDoWindScadaUnpacker:
    def __init__(self, scada_file_name: str, wedowind_zip_file_path: Path = CACHE_DIR / ZIP_FILENAME) -> None:
        self.scada_file_name = scada_file_name
        self.wedowind_zip_file_path = wedowind_zip_file_path
        self.scada_df = None

    def unpack(self, rated_power_kw: float) -> pd.DataFrame:
        if self.scada_df is None:
            raw_df = self._read_raw_df()
            scada_df_test = self._construct_scada_df_test(scada_df_raw=raw_df)
            scada_df_ref = self._construct_scada_df_ref(scada_df_raw=raw_df)
            self.scada_df = self._format_scada_df(
                scada_df=pd.concat([scada_df_test, scada_df_ref]), rated_power_kw=rated_power_kw
            )
        return self.scada_df

    def _read_raw_df(self) -> pd.DataFrame:
        with zipfile.ZipFile(self.wedowind_zip_file_path) as zf:
            raw_df = pd.read_csv(zf.open(self.scada_file_name), parse_dates=[1], index_col=0).drop(
                columns=["VcosD", "VsinD"]
            )
        raw_df.columns = raw_df.columns.str.replace(" ", "")
        return raw_df

    @staticmethod
    def _format_scada_df(scada_df: pd.DataFrame, rated_power_kw: float) -> pd.DataFrame:
        scada_df[DataColumns.active_power_mean] = scada_df["normalized_power"] * rated_power_kw
        # map some mast data to the turbine for convenience
        scada_df[DataColumns.wind_speed_mean] = scada_df[WeDoWindScadaColumns.WIND_SPEED.value]
        scada_df[DataColumns.yaw_angle_mean] = scada_df[WeDoWindScadaColumns.WIND_DIRECTION.value]
        # placeholder values for other required columns
        scada_df[DataColumns.pitch_angle_mean] = 0
        scada_df[DataColumns.gen_rpm_mean] = 1000
        scada_df[DataColumns.shutdown_duration] = 0

        scada_df = scada_df.set_index("time")
        scada_df.index.name = TIMESTAMP_COL
        # make index UTC
        scada_df.index = scada_df.index.tz_localize("UTC")
        return scada_df

    @staticmethod
    def _construct_scada_df_test(scada_df_raw: pd.DataFrame) -> pd.DataFrame:
        return (
            scada_df_raw.drop(columns=[WeDoWindScadaColumns.Y_CTRL_NORM.value])
            .copy()
            .assign(TurbineName=WeDoWindTurbineNames.TEST.value)
            .rename(columns={WeDoWindScadaColumns.Y_TEST_NORM.value: "normalized_power"})
        )

    @staticmethod
    def _construct_scada_df_ref(scada_df_raw: pd.DataFrame) -> pd.DataFrame:
        return (
            scada_df_raw.drop(columns=[WeDoWindScadaColumns.Y_TEST_NORM.value])
            .copy()
            .assign(TurbineName=WeDoWindTurbineNames.REF.value)
            .rename(columns={WeDoWindScadaColumns.Y_CTRL_NORM.value: "normalized_power"})
        )


# =====================================================================================================================
# Setting up pydantic basemodel class "model" (aka onbject) specific to WeDoWind
# =====================================================================================================================

class WeDoWindAnalysisConf(BaseModel):
    scada_file_name: str = Field(description="e.g. 'Turbine Upgrade Dataset(Pitch Angle Pair).csv'")
    unusable_wd_ranges: list[tuple[int, int]] = Field(description="directions to exclude from entire analysis")
    ref_wd_filter: list[int] = Field(
        description="directions to include in power performance analysis", default=[0, 360]
    )
    clip_rated_power_pp: bool
    use_rated_invalid_bins: bool = Field(
        description="use rated power bins which have been extrapolated in uplift calculation", default=False
    )

# =====================================================================================================================
# if unavailable, download data
# =====================================================================================================================

def download_wedowind_data_from_zenodo(cache_dir: Path = CACHE_DIR) -> None:
    logger.info("Downloading example data from Zenodo")
    if not (cache_dir / ZIP_FILENAME).exists():
        # https://zenodo.org/records/5516556
        download_zenodo_data(record_id="5516556", output_dir=cache_dir, filenames={ZIP_FILENAME})

# =====================================================================================================================
# the "we do wind" coordinates... These are faked because the dataset does
# not come with any coordinate information, relative or otherwise
# =====================================================================================================================

def create_fake_wedowind_metadata_df() -> pd.DataFrame:
    # coordinates below are based on Figure 5.6 of Data Science for Wind Energy (Yu Ding 2020)
    coords_df = pd.DataFrame(
        {
            "Name": ["WT1", "WT2", "WT3", "WT4", "MAST1", "MAST2"],
            "X": [500, 2200, 9836, 7571, 0, 9571],
            "Y": [9136, 9436, 0, 2050, 9836, 50],
        }
    )
    assumed_wf_lat = 40
    assumed_wf_lon = -89
    m_per_deglat = 40_075_000 / 360
    coords_df["Latitude"] = assumed_wf_lat + (coords_df["Y"] - coords_df["Y"].mean()) / m_per_deglat
    coords_df["Longitude"] = assumed_wf_lon + (coords_df["X"] - coords_df["X"].mean()) / (
        m_per_deglat * math.cos(assumed_wf_lat * math.pi / 180)
    )
    return coords_df.loc[:, ["Name", "Latitude", "Longitude"]].assign(
        TimeZone="UTC", TimeSpanMinutes=10, TimeFormat="Start"
    )
# =====================================================================================================================
# Unclear the exact purpose...
# Looks like a fake dataframe where for each time there is a mean wind speed and a mean wind direction
# =====================================================================================================================

def create_fake_wedowind_reanalysis_dataset(start_datetime: dt.datetime) -> ReanalysisDataset:
    rng = np.random.default_rng(0)
    rows = 100
    return ReanalysisDataset(
        id="dummy_reanalysis_data",
        data=pd.DataFrame(
            data={
                "100_m_hws_mean_mps": rng.uniform(5, 10, rows),
                "100_m_hwd_mean_deg-n_true": rng.uniform(0, 360, rows),
            },
            index=pd.DatetimeIndex(pd.date_range(start=start_datetime, periods=rows, freq="h", tz="UTC")),
        ),
    )

# =====================================================================================================================
# key dates... clearer explanation in comments below
# =====================================================================================================================

def establish_wedowind_key_dates(scada_df: pd.DataFrame) -> KeyDates:
    """Extracts important dates from the SCADA data. These dates may then be used in the WindUpConfig.

    Args:
        scada_df:

    Returns: tuple of dates that may be passed to the WindUpConfig

    """
    upgrade_first_dt_utc_start = scada_df[scada_df[WeDoWindScadaColumns.UPGRADE_STATUS.value] > 0].index.min()
    analysis_last_dt_utc_start = scada_df[scada_df[WeDoWindScadaColumns.UPGRADE_STATUS.value] > 0].index.max()
    lt_first_dt_utc_start = max(scada_df.index.min(), upgrade_first_dt_utc_start - pd.DateOffset(years=1)) #### whichever came later, the earliest date or one year before upgrade
    lt_last_dt_utc_start = lt_first_dt_utc_start + pd.DateOffset(years=1) - pd.Timedelta(minutes=10)
    detrend_first_dt_utc_start = lt_first_dt_utc_start
    detrend_last_dt_utc_start = (
        scada_df[scada_df[WeDoWindScadaColumns.UPGRADE_STATUS.value] > 0].index.min()
        - pd.DateOffset(weeks=1)
        - pd.Timedelta(minutes=10)
    )
    pre_first_dt_utc_start = lt_first_dt_utc_start
    pre_last_dt_utc_start = (
        pre_first_dt_utc_start
        + (
            scada_df[scada_df[WeDoWindScadaColumns.UPGRADE_STATUS.value] > 0].index.max()
            - scada_df[scada_df[WeDoWindScadaColumns.UPGRADE_STATUS.value] > 0].index.min()
        )
        - pd.Timedelta(minutes=10)
    )
    post_first_dt_utc_start = scada_df[scada_df[WeDoWindScadaColumns.UPGRADE_STATUS.value] > 0].index.min()
    post_last_dt_utc_start = scada_df[scada_df[WeDoWindScadaColumns.UPGRADE_STATUS.value] > 0].index.max()
    analysis_first_dt_utc_start = min(pre_first_dt_utc_start, lt_first_dt_utc_start, detrend_first_dt_utc_start)

    return KeyDates(
        analysis_first_dt_utc_start=analysis_first_dt_utc_start,####   analysis_first =    min(pre_first, lt_first, detrend_first)             = 2010-07-30 22:40
        upgrade_first_dt_utc_start=upgrade_first_dt_utc_start,####     upgrade_first =     min (upgrade > 0)                                   = 2011-04-25 21:50
        analysis_last_dt_utc_start=analysis_last_dt_utc_start,####     analysis_last =     max (upgrade > 0)                                   = 2011-06-25 18:30
        lt_first_dt_utc_start=lt_first_dt_utc_start,####               lt_first =          max ( min(timestamp) or upgrade_first - 1 year)     = 2010-07-30 22:40 i.e. min(timestamp) because we don't have data for april 2010
        lt_last_dt_utc_start=lt_last_dt_utc_start,####                 lt_last =           lt_first + 1 year - 10 min                          = 2011-07-30 22:30  NOTE this goes beyond what we have data for...
        detrend_first_dt_utc_start=detrend_first_dt_utc_start,####     detrend_first =     lt_first                                            = 2010-07-30 22:40
        detrend_last_dt_utc_start=detrend_last_dt_utc_start,####       detrend_last =      upgrade_first - 1 week - 10 minutes                 = 2011-04-18 21:40
        pre_first_dt_utc_start=pre_first_dt_utc_start,####             pre_first =         lt_first                                            = 2010-07-30 22:40
        pre_last_dt_utc_start=pre_last_dt_utc_start,####               pre_last =          pre_first + (analysis_last-upgrade_first) - 10 min  = 2010-09-29 19:10
        post_first_dt_utc_start=post_first_dt_utc_start,####           post_first =        upgrade_first                                       = 2011-04-25 21:50
        post_last_dt_utc_start=post_last_dt_utc_start,####             post_last =         analysis_last                                       = 2011-06-25 18:30
    )

# =====================================================================================================================
# plot all temporal plots, than also some scatter and mean Cp plots
# =====================================================================================================================

def generate_custom_exploratory_plots(
    scada_df: pd.DataFrame, assumed_rated_power_kw: float, rotor_diameter_m: int, out_dir: Path
) -> Path:
    """These custom plots are to help with SCADA data exploration.
    It was created because it was unclear how the SCADA data is related to the metadata so helped in looking for wakes
    in the data.

    Returns: path to directory containing the plots
    """
    custom_plots_dir_root = out_dir / "custom_plots"
    custom_plots_dir_timeseries = custom_plots_dir_root / "timeseries"

    custom_plots_dir_root.mkdir(exist_ok=True, parents=True)
    custom_plots_dir_timeseries.mkdir(exist_ok=True)

    for name, df in scada_df.groupby(DataColumns.turbine_name):
        for col in df.columns:
            plt.figure()
            plt.scatter(df.index, df[col], s=1)
            title = f"{name} {col}"
            plt.xlabel(TIMESTAMP_COL)
            plt.ylabel(col)
            plt.xticks(rotation=90)
            plt.grid()
            plt.tight_layout()
            plt.savefig(custom_plots_dir_timeseries / f"{title}.png")
            plt.close()

    region2_power_margin = 0.2
    region2_df = scada_df[
        (scada_df["normalized_power"] > region2_power_margin)
        & (scada_df["normalized_power"] < (1 - region2_power_margin))
    ]

    binned_by_turbine = {}
    for name, df in region2_df.groupby(DataColumns.turbine_name):
        if name == "Mast":
            continue
        plt.figure()#### ===================== PLOT SCATTERS
        plt.scatter(
            df[WeDoWindScadaColumns.WIND_DIRECTION.value],
            calc_cp(
                power_kw=df["normalized_power"] * assumed_rated_power_kw,
                ws_ms=df[WeDoWindScadaColumns.WIND_SPEED.value],
                air_density_kgpm3=1.2,
                rotor_diameter_m=rotor_diameter_m,
            ),
            s=1,
            alpha=0.7,
        )
        plt.ylim(0, 1.5)
        title = f"{name} Cp vs {WeDoWindScadaColumns.WIND_DIRECTION.value}"
        plt.title(title)
        plt.xlabel(WeDoWindScadaColumns.WIND_DIRECTION.value)
        plt.ylabel("Cp")
        plt.xticks(rotation=90)
        plt.grid()
        plt.tight_layout()
        plt.savefig(custom_plots_dir_root / f"{title}.png")
        plt.close()
        # find mean normalized_power and V binned by D
        _df = df.copy()
        _df[f"{WeDoWindScadaColumns.WIND_DIRECTION.value}_bin"] = pd.cut(
            _df[WeDoWindScadaColumns.WIND_DIRECTION.value], bins=range(0, 361, 5)
        )
        binned = _df.groupby(f"{WeDoWindScadaColumns.WIND_DIRECTION.value}_bin", observed=False)[
            [WeDoWindScadaColumns.WIND_DIRECTION.value, "normalized_power", "V"]
        ].mean()
        binned_by_turbine[name] = binned
        plt.figure()#### ===================== PLOT MEANS
        plt.plot(
            binned[WeDoWindScadaColumns.WIND_DIRECTION.value],
            calc_cp(
                power_kw=binned["normalized_power"] * assumed_rated_power_kw,
                ws_ms=binned[WeDoWindScadaColumns.WIND_SPEED.value],
                air_density_kgpm3=1.2,
                rotor_diameter_m=rotor_diameter_m,
            ),
            marker=".",
        )
        title = f"{name} mean Cp vs {WeDoWindScadaColumns.WIND_DIRECTION.value}"
        plt.title(title)
        plt.xlabel(WeDoWindScadaColumns.WIND_DIRECTION.value)
        plt.ylabel("Cp")
        plt.xticks(rotation=90)
        plt.grid()
        plt.tight_layout()
        plt.savefig(custom_plots_dir_root / f"{title}.png")
        plt.close()

    plt.figure()
    for name, binned in binned_by_turbine.items():
        plt.plot(
            binned[WeDoWindScadaColumns.WIND_DIRECTION.value],
            calc_cp(
                power_kw=binned["normalized_power"] * assumed_rated_power_kw,
                ws_ms=binned[WeDoWindScadaColumns.WIND_SPEED.value],
                air_density_kgpm3=1.2,
                rotor_diameter_m=rotor_diameter_m,
            ),
            label=name,
            marker=".",
        )
    plt.ylim(0.2, 2.5)
    title = f"mean Cp vs {WeDoWindScadaColumns.WIND_DIRECTION.value}"
    plt.title(title)
    plt.xlabel(WeDoWindScadaColumns.WIND_DIRECTION.value)
    plt.ylabel("Cp")
    plt.xticks(rotation=90)
    plt.grid()
    plt.tight_layout()
    plt.savefig(custom_plots_dir_root / f"{title}.png")
    plt.close()

    logger.info("Custom plots saved to directory: %s", custom_plots_dir_root)
    return custom_plots_dir_root

# =====================================================================================================================
# configurations...
# =====================================================================================================================

ANALYSIS_SPECIFIC_CONFIG = {
    "Pitch Angle": WeDoWindAnalysisConf(
        scada_file_name="Turbine Upgrade Dataset(Pitch Angle Pair).csv",
        unusable_wd_ranges=[(70, 140), (150, 180)],  # determined by inspecting custom Cp vs D plots
        ref_wd_filter=[160, 240],  # apparent wake free sector determined by inspecting detrending plots
        clip_rated_power_pp=False,  # rated power is apparently higher after the upgrade
        use_rated_invalid_bins=True,  # use extrapolated rated power results
    ),
    "Vortex Generator": WeDoWindAnalysisConf(
        scada_file_name="Turbine Upgrade Dataset(VG Pair).csv",
        unusable_wd_ranges=[(25, 115), (240, 315)],  # determined by inspecting custom Cp vs D plots
        ref_wd_filter=[115, 240],  # apparent wake free sector determined by inspecting detrending plots
        clip_rated_power_pp=True,  # Vortex Generators are not expected to increase rated power
    ),
}

# =====================================================================================================================
# MAIN analysis, tailored for WeDoWind
# =====================================================================================================================

def main_wedowind_analysis(
    analysis_name: str,
    *,
    generate_custom_plots: bool = True,
    save_plots: bool = True,
    cache_dir: Path = CACHE_DIR,
    analysis_output_dir: Path = ANALYSIS_OUTPUT_DIR,
    bootstrap_runs_override: int | None = None,
) -> pd.DataFrame:
    cache_assessment = cache_dir / analysis_name

    #### checks if the folders exist

    for d in [cache_dir, analysis_output_dir, cache_assessment]:
        d.mkdir(parents=True, exist_ok=True)

    #### check that the input "analysis_name" (the only compulsory input for main_wedowind_analysis) matches what's been specified in "ANALYSIS_SPECIFIC_CONFIG"

    if analysis_name not in ANALYSIS_SPECIFIC_CONFIG:
        msg = f"analysis_name must be one of {list(ANALYSIS_SPECIFIC_CONFIG.keys())}"
        raise ValueError(msg)

    #### extract from "ANALYSIS_SPECIFIC_CONFIG" dictionary the bit that we want ("analysis_name")

    analysis_conf = ANALYSIS_SPECIFIC_CONFIG[analysis_name]


    #### download it (if available)

    download_wedowind_data_from_zenodo(cache_dir=cache_dir)

    # assumptions below are based on Table 5.1 of Data Science for Wind Energy (Yu Ding 2020)
    assumed_rated_power_kw = 1500
    assumed_rotor_diameter_m = 80
    cutout_ws_mps = 20

    logger.info("Unpacking turbine SCADA data")

    #### unpack data

    scada_df = WeDoWindScadaUnpacker(
        scada_file_name=str(analysis_conf.scada_file_name),
        wedowind_zip_file_path=cache_dir / ZIP_FILENAME,
    ).unpack(rated_power_kw=assumed_rated_power_kw)

    #### create fake coordinates metadata

    metadata_df = create_fake_wedowind_metadata_df()

    #### create the timeseries and Cp plots

    if generate_custom_plots:
        generate_custom_exploratory_plots(
            scada_df=scada_df,
            assumed_rated_power_kw=assumed_rated_power_kw,
            rotor_diameter_m=assumed_rotor_diameter_m,
            out_dir=analysis_output_dir / analysis_name,
        )

    # assume WT1 is Test and WT2 is Ref. Since we do not actually know which turbine is which we exclude all waked data.
    metadata_df = metadata_df[metadata_df["Name"].isin(["WT1", "WT2"])]
    metadata_df = metadata_df.replace(
        {
            "Name": {
                "WT1": WeDoWindTurbineNames.TEST.value,
                "WT2": WeDoWindTurbineNames.REF.value,
                "MAST1": "Mast",
            }
        }
    )
    # Construct wind-up Configurations
    wtg_map = {
        x: {
            "name": x,
            "turbine_type": {
                "turbine_type": "unknown turbine type",
                "rotor_diameter_m": assumed_rotor_diameter_m,
                "rated_power_kw": assumed_rated_power_kw,
                "cutout_ws_mps": cutout_ws_mps,
                "normal_operation_pitch_range": (-10.0, 35.0),
                "normal_operation_genrpm_range": (0, 2000.0),
            },
        }
        for x in [WeDoWindTurbineNames.TEST.value, WeDoWindTurbineNames.REF.value]
    }

    key_dates = establish_wedowind_key_dates(scada_df=scada_df)

    # Reanalysis data is required by WindUp but we do now know where this wind farm is
    # therefore create a fake reanalysis object

    reanalysis_dataset = create_fake_wedowind_reanalysis_dataset(start_datetime=key_dates.lt_first_dt_utc_start)

    cfg = WindUpConfig(
        assessment_name=analysis_name,####                                                             for example "Pitch Angle"
        ref_wd_filter=analysis_conf.ref_wd_filter,####                                                 wake free sector determined by inspecting detrending plots
        use_lt_distribution=True,####                                                                  ?
        out_dir=analysis_output_dir / analysis_name,####                                               output file path
        test_wtgs=[Turbine.model_validate(wtg_map[x]) for x in [WeDoWindTurbineNames.TEST.value]],#### Create "Turbine" class object "Test"
        ref_wtgs=[Turbine.model_validate(wtg_map[x]) for x in [WeDoWindTurbineNames.REF.value]],####   Create "Turbine" class object "Ref"
        years_offset_for_pre_period=1,####                                                             OK this is strange and I don't like it. It's decided to do one year offset for pre period. but there isn't the data?
        years_for_lt_distribution=1,####                                                               lt = long term
        years_for_detrend=1,
        ws_bin_width=1.0,
        use_test_wtg_lt_distribution=True,
        analysis_first_dt_utc_start=key_dates.analysis_first_dt_utc_start,
        upgrade_first_dt_utc_start=key_dates.upgrade_first_dt_utc_start,
        analysis_last_dt_utc_start=key_dates.analysis_last_dt_utc_start,
        lt_first_dt_utc_start=key_dates.lt_first_dt_utc_start,
        lt_last_dt_utc_start=key_dates.lt_last_dt_utc_start,
        detrend_first_dt_utc_start=key_dates.detrend_first_dt_utc_start,
        detrend_last_dt_utc_start=key_dates.detrend_last_dt_utc_start,
        asset=Asset.model_validate({"name": "Mystery Wind Farm", "wtgs": list(wtg_map.values())}),
        missing_scada_data_fields=[DataColumns.yaw_angle_min, DataColumns.yaw_angle_max],
        prepost=PrePost.model_validate(
            {
                "pre_first_dt_utc_start": key_dates.pre_first_dt_utc_start,
                "pre_last_dt_utc_start": key_dates.pre_last_dt_utc_start,
                "post_first_dt_utc_start": key_dates.post_first_dt_utc_start,
                "post_last_dt_utc_start": key_dates.post_last_dt_utc_start,
            }
        ),
        optimize_northing_corrections=False,
        clip_rated_power_pp=analysis_conf.clip_rated_power_pp,
        use_rated_invalid_bins=analysis_conf.use_rated_invalid_bins,
        bootstrap_runs_override=bootstrap_runs_override,
    )

    #### show plots changed to True from False

    plot_cfg = PlotConfig(show_plots=True, save_plots=save_plots, plots_dir=cfg.out_dir / "plots")

    wd_ranges_to_exclude = analysis_conf.unusable_wd_ranges

    #### mask (i.e. remove???) excluded yaw angles

    scada_df_for_assessment = scada_df.copy()
    for wdr in wd_ranges_to_exclude:
        logger.info("Filtering out wind directions between %s", wdr)
        mask = (scada_df_for_assessment[DataColumns.yaw_angle_mean] >= wdr[0]) & (
            scada_df_for_assessment[DataColumns.yaw_angle_mean] <= wdr[1]
        )
        scada_df_for_assessment = scada_df_for_assessment.loc[~mask, :]

    assessment_inputs = AssessmentInputs.from_cfg(
        cfg=cfg,
        plot_cfg=plot_cfg,
        scada_df=scada_df_for_assessment,
        metadata_df=metadata_df,
        reanalysis_datasets=[reanalysis_dataset],#### remember, this one is fake. unclear what it is anyway...
        cache_dir=cache_assessment,
    )

    #### Run Analysis

    results_per_test_ref_df = run_wind_up_analysis(assessment_inputs)

    #### Save? Analysis

    results_per_test_ref_df.to_csv(cfg.out_dir / "results_per_test_ref.csv", index=False)
    _ = format_and_print_results_table(results_per_test_ref_df)
    return results_per_test_ref_df

# =====================================================================================================================
# standard Python run statement
# =====================================================================================================================

if __name__ == "__main__":
    setup_logger(ANALYSIS_OUTPUT_DIR / "analysis.log")
    main_wedowind_analysis("Pitch Angle")
    main_wedowind_analysis("Vortex Generator")
