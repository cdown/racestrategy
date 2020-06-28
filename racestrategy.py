#!/usr/bin/env python

"""
This tool assumes RD GT3 rules, ie. mandatory pit stop, with 60 minutes of
racing.

Realistically there are only three reasonable strategies -- see
get_pit_stop_time_for_strategy.

These calculations don't factor in track position, so bear that in mind when
choosing a strategy, eg. it may be better to choose a theoretically slower
strategy on the basis that it will give you better track position.

To get the times for each strategy option, I suggest the following procedure:

1. Do 3 warm up laps on softs and adjust setup as necessary.
2. Do 3 warm up laps on mediums and adjust setup as necessary.
3. Do 5 laps on softs, 50% fuel.
4. Do 5 laps on mediums, 50% fuel.
5. Do 5 laps on mediums, ~99% fuel.

Try to go for consistent laps to get better data.
"""

import argparse
from datetime import datetime, timedelta
from collections import namedtuple, OrderedDict
import math
import statistics

# Non-scientific, calculated frame by frame from recording
TIME_TO_CHANGE_TYRES = timedelta(seconds=20)
TIME_TO_FILL_ONE_LITRE = timedelta(microseconds=190000)

FUEL_SAFETY_BUFFER_LITRES = 1
FUEL_SAFETY_EXTRA_LAP_THRESHOLD = 0.15
STRATEGY_ARGS = ["soft-50", "med-50", "med-99"]
TIME_LOST_AT_RACE_START = timedelta(seconds=3)

# Obviously not scientific. Time is how much deterioration in 30 minutes.
#
# - Soft 50 at Road Atlanta during race: 1.4%
# - Med 99 at Brands Hatch during race (second stint): 0.4%
#
# We also don't really use it scientifically, since it's not biased based on
# --race-time, but it's mostly just to get the order of magnitude.
TYRE_DETERIORATION = {
    "soft_50": 1.4,
    "med_50": 0.4,
    "med_99": 0.4,
}


StrategyResult = namedtuple(
    "StrategyResult",
    [
        "laps_at_zero",
        "total_laps",
        "total_fuel",
        "normalised_lap_time",
        "total_time",
        "pit_stop_time",
    ],
)


def msm_to_tds(td_str):
    t = datetime.strptime(td_str, "%M:%S.%f")
    delta = timedelta(minutes=t.minute, seconds=t.second, microseconds=t.microsecond)
    return delta


def get_pit_stop_time_for_strategy(strategy, litres):
    refill_time = litres * TIME_TO_FILL_ONE_LITRE

    out = {
        # Start: 50% required fuel, softs.
        # Pit stop: 50% required fuel, NEW softs.
        "soft_50": max(refill_time / 2, TIME_TO_CHANGE_TYRES),
        # Start: 50% required fuel, mediums.
        # Pit stop: 50% required fuel, KEEP mediums.
        "med_50": refill_time / 2,
        # Start: 100% required fuel, mediums.
        # Pit stop: 1l fuel, KEEP mediums.
        "med_99": TIME_TO_FILL_ONE_LITRE,
    }

    return out[strategy]


def calculate_laps(race_time, lap_time, full=True):
    res = race_time / lap_time
    if full:
        res = math.ceil(res)
    return res


def calculate_fuel(litres_per_lap, laps):
    return math.ceil((litres_per_lap * laps) + FUEL_SAFETY_BUFFER_LITRES)


def calculate_total_time(laps, lap_time):
    return lap_time * laps


def calculate_lost_time(pit_stop_time, time_lost_driving_through_pits):
    return TIME_LOST_AT_RACE_START + time_lost_driving_through_pits + pit_stop_time


def get_strategies(
    race_time, strategies_to_times, litres_per_lap, time_lost_driving_through_pits
):
    out = {}

    for strat, lap_times in strategies_to_times.items():
        raw_lap_time = timedelta(
            seconds=statistics.mean([x.total_seconds() for x in lap_times])
        )

        # Add however much deterioration there is for this tyre strategy. So if
        # there's 2% deterioration, we bump the average by 1% to get the right
        # value.
        add_time = raw_lap_time / 100 * (TYRE_DETERIORATION[strat] / 2)
        raw_lap_time += add_time

        # This is naive, as it doesn't take into consideration the pit stop
        # time required, yet. However, we need it to get a baseline for the
        # fuel (since fuel time also may eat into the total time), and it will
        # always be more pessimistic than the actual number of laps.
        naive_laps = calculate_laps(race_time, raw_lap_time)
        naive_fuel = calculate_fuel(litres_per_lap, naive_laps)

        # Now that we know how long we'll spend idle in the pit, driving
        # through the pits, and at the race start, etc, account for it in the
        # representative lap time.
        pit_stop_time = get_pit_stop_time_for_strategy(strat, naive_fuel)
        lap_time = raw_lap_time + (
            calculate_lost_time(pit_stop_time, time_lost_driving_through_pits)
            / naive_laps
        )

        laps = calculate_laps(race_time, lap_time)
        laps_at_zero = calculate_laps(race_time, lap_time, full=False)

        fuel_laps = laps
        if math.ceil(laps_at_zero) - laps_at_zero <= FUEL_SAFETY_EXTRA_LAP_THRESHOLD:
            # If someone goes faster we might do one more lap. Bump the fuel to
            # compensate.
            fuel_laps += 1

        fuel = calculate_fuel(litres_per_lap, fuel_laps)
        time = calculate_total_time(laps, lap_time)

        out[strat] = StrategyResult(
            laps_at_zero,
            laps,
            fuel,
            lap_time,
            time,
            pit_stop_time + time_lost_driving_through_pits,
        )

    return OrderedDict(
        sorted(out.items(), key=lambda r: r[1].laps_at_zero, reverse=True)
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-l", "--litres-per-lap", help="litres per lap", type=float, required=True,
    )
    parser.add_argument(
        "-p",
        "--time-lost-driving-through-pits",
        help="how much longer it takes to drive through the pits than be on track",
        type=msm_to_tds,
        default=timedelta(seconds=10),
    )
    parser.add_argument(
        "-t",
        "--race-time",
        help="total race minutes (default: 60)",
        type=lambda minutes: timedelta(minutes=int(minutes)),
        default=timedelta(hours=1),
    )

    for arg in STRATEGY_ARGS:
        parser.add_argument(
            "--{}".format(arg), metavar="TIME", action="append", type=msm_to_tds,
        )

    return parser.parse_args()


def main():
    args = parse_args()

    strategies_to_times = {}

    for arg in STRATEGY_ARGS:
        arg = arg.replace("-", "_")
        times = getattr(args, arg, None)
        if times:
            strategies_to_times[arg] = times

    print("From best to worst strategy for a race lasting {}:\n".format(args.race_time))

    fastest_time = None
    fastest_laps = None

    for strat, res in get_strategies(
        args.race_time,
        strategies_to_times,
        args.litres_per_lap,
        args.time_lost_driving_through_pits,
    ).items():
        print("{}:".format(strat))
        print("Laps at 0 seconds: {:.2f}".format(res.laps_at_zero))

        if not fastest_time:
            fastest_laps = res.laps_at_zero  # might be fewer if slower
            fastest_time = fastest_laps * res.normalised_lap_time
        else:
            our_time = fastest_laps * res.normalised_lap_time
            print("Time difference from fastest: {}".format(our_time - fastest_time))

        print("Total laps: {}".format(res.total_laps))
        print("Total fuel: {}".format(res.total_fuel))
        print("Normalised lap time: {}".format(res.normalised_lap_time))
        print("Total time: {}".format(res.total_time))
        print("Pit stop time: {}\n".format(res.pit_stop_time))


if __name__ == "__main__":
    main()
