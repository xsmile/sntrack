#!/usr/bin/env python

__pkgname__ = 'sntrack'
__version__ = '0.1'
__description__ = 'Tracks the battery discharge rate during sleep'
__url__ = 'https://github.com/xsmile/sntrack'
__author__ = 'xsmile'
__author_email__ = '<>'
__license__ = 'GPLv3'
__keywords__ = 'notebook laptop power-management sleep suspend energy battery discharge-rate'

import argparse
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

DATABASE = f'/usr/local/share/{__pkgname__}/history.db'
TMPFILE = f'/tmp/{__pkgname__}'
DURATION_THRESHOLD_S = 300
LOGGING_FORMAT = '[%(levelname)s] %(message)s'

sleep_actions = ['suspend', 'hibernate', 'hybrid-sleep', 'suspend-then-hibernate']


def parse_args() -> argparse.Namespace:
    """
    Parses command line arguments.
    :return: Namespace of the argument parser
    """

    parser = argparse.ArgumentParser(description=__description__)
    subparsers = parser.add_subparsers(help='main action', dest='main_action', required=True)

    parser_pre = subparsers.add_parser('pre', help='runs automatically when the system enters sleep')
    parser_pre.add_argument('sleep_action', choices=sleep_actions)

    parser_post = subparsers.add_parser('post', help='runs automatically when the system exits sleep')
    parser_post.add_argument('sleep_action', choices=sleep_actions)

    parser_plot = subparsers.add_parser('plot', help='plot historical data')
    parser_plot.add_argument('-s', '--short', action='store_true',
                             help=f'include short sleep sessions <= {DURATION_THRESHOLD_S:d} s')
    parser_plot.add_argument('-b', '--bios', help='filter by BIOS version, e.g. "R1BET66W(1.35 )"')
    parser_plot.add_argument('-m', '--mode', help='filter by sleep mode: deep, s2idle')
    parser_plot.add_argument('-a', '--action', help=f'filter by sleep action: {", ".join(sleep_actions):s}')

    parser.add_argument('-v', '--verbose', help='increase output verbosity',
                        action='store_const', dest='loglevel', const=logging.DEBUG, default=logging.INFO)
    parser.add_argument('--version', action='version', version=f'{parser.prog:s} {__version__:s}')
    result = parser.parse_args()

    return result


def get_bios_version() -> Optional[str]:
    """
    Gets the BIOS version from sysfs.
    :return: BIOS version
    """
    try:
        with open('/sys/class/dmi/id/bios_version', encoding='utf_8') as f:
            return f.read().strip()
    except Exception as e:
        logger.warning(e)
        return None


def is_on_ac() -> bool:
    """
    Checks if the system is using AC as the power source.
    :return: True if on AC, else False
    """
    for path in Path('/sys/class/power_supply').rglob('AC*'):
        with open(Path(path, 'online'), encoding='utf_8') as f:
            return int(f.read()) == 1
    # Otherwise assume AC
    return True


def get_sleep_mode() -> str:
    """
    Gets the currently active sleep mode, e.g. deep, s2idle
    :return: Sleep mode
    """
    with open('/sys/power/mem_sleep', encoding='utf_8') as f:
        out = f.read()
    return re.findall(r'\[(.*)\]', out)[0]


def get_battery_energy(attribute_suffix: str) -> int:
    """
    Gets an attribute of the battery power supply class via sysfs.
    :return: Attribute value
    """
    for path in Path('/sys/class/power_supply').rglob('BAT*'):
        energy_path = Path(path, f'energy_{attribute_suffix}')
        if energy_path.exists():
            with open(energy_path, encoding='utf_8') as f:
                return int(f.read())

        charge_path = Path(path, f'charge_{attribute_suffix}')
        if charge_path.exists():
            with open(charge_path, encoding='utf_8') as f:
                charge = int(f.read()) / 1000
            with open(Path(path, 'voltage_min_design'), encoding='utf_8') as f:
                voltage = int(f.read()) / 1000
            return charge * voltage

    return 0

def init(db: str = DATABASE, tmpfile: str = TMPFILE) -> sqlite3.Cursor:
    """
    Initializes settings and the database.
    :param db: Filepath of the database
    :param tmpfile: Filepath to store sleep session data
    :return: Cursor for the database
    """
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    Path(tmpfile).parent.mkdir(parents=True, exist_ok=True)

    try:
        con = sqlite3.connect(db)
        cur = con.cursor()
        # Initialize the table
        cur.execute('CREATE TABLE IF NOT EXISTS history ('
                    'id INTEGER PRIMARY KEY,'
                    'bios_version TEXT,'
                    'sleep_mode TEXT,'
                    'sleep_action TEXT,'
                    't0 INTEGER, t1 INTEGER,'
                    'e0 INTEGER, e1 INTEGER)')
        return cur
    except sqlite3.OperationalError:
        logger.critical('unable to open database')
        sys.exit(1)


def cleanup(cur: sqlite3.Cursor) -> None:
    """
    Cleans up the database connection.
    :param cur: Cursor for the database.
    """
    con = cur.connection
    con.commit()
    con.close()


def pre(cur: sqlite3.Cursor, args: argparse.Namespace) -> None:
    """
    Persists time and energy data before entering sleep.
    See the man page for systemd-suspend.service.
    :param cur: Cursor of the database
    :param args: Namespace of the argument parser
    """
    if is_on_ac():
        return

    cur.execute('INSERT INTO history (bios_version, sleep_mode, sleep_action, t0, e0) VALUES (?, ?, ?, ?, ?)',
                (get_bios_version(), get_sleep_mode(), args.sleep_action,
                 round(time.time()), get_battery_energy('now')))

    with open(TMPFILE, 'w', encoding='utf_8') as f:
        f.write(str(cur.lastrowid))


def post(cur: sqlite3.Cursor, args: argparse.Namespace) -> None:
    """
    Persists time and energy data after exiting sleep.
    :param cur: Cursor of the database
    :param args: Namespace of the argument parser
    """
    if is_on_ac():
        return

    if not Path(TMPFILE).exists():
        return

    with open(TMPFILE, encoding='utf_8') as f:
        cur_id = int(f.read())

    cur.execute('UPDATE history SET t1 = ?, e1 = ? WHERE id = ?',
                (round(time.time()), get_battery_energy('now'), cur_id))

    tmpfile = Path(TMPFILE)
    if tmpfile.exists():
        tmpfile.unlink()


def plot(cur: sqlite3.Cursor, args: argparse.Namespace) -> None:
    """
    Plots historical data.
    Results can be filtered by BIOS version and sleep mode, passed via args.
    :param cur: Cursor of the database
    :param args: Namespace of the argument parser
    """
    x_durations = []
    y_discharge_rates = []
    # y_energy_losses = []
    title = []

    if args.bios:
        title.append(f'bios: {args.bios}')
    if args.mode:
        title.append(f'mode: {args.mode}')
    if args.action:
        title.append(f'action: {args.action}')

    rows = cur.execute('SELECT bios_version, sleep_mode, sleep_action, t0, t1, e0, e1 FROM history').fetchall()
    for row in rows:
        # BIOS version
        if args.bios:
            if row[0] != args.bios:
                continue
        # Sleep mode
        if args.mode:
            if row[1] != args.mode:
                continue
        # Sleep action
        if args.action:
            if row[2] != args.action:
                continue
        # Time
        if row[4] is None:
            continue
        td_s = row[4] - row[3]
        td_h = td_s / 3600
        if not args.short:
            if td_s <= DURATION_THRESHOLD_S:
                continue
        # Energy
        ed_w = (row[5] - row[6]) / 1000000
        if ed_w <= 0:
            # Filter invalid results
            continue
        # Rate
        rate = ed_w / td_h

        x_durations.append(td_h)
        y_discharge_rates.append(rate)
        # y_energy_losses.append(ed_w)

    if not y_discharge_rates:
        logger.info('nothing to plot')
        return

    mean_discharge_rate = sum(y_discharge_rates) / len(y_discharge_rates)
    est_duration_d = get_battery_energy('full') / 1000000 / mean_discharge_rate / 24
    total_time_slept_h = sum(x_durations)

    (fig, ax) = plt.subplots()

    ax.scatter(x_durations, y_discharge_rates, label='discharge rate', marker='o')
    ax.hlines(y=mean_discharge_rate, xmin=0, xmax=max(x_durations),
              label=f'mean discharge rate: {mean_discharge_rate:.2f}', linestyle='--')
    # ax.scatter(x_durations, y_energy_losses, label=f'energy loss', marker='x')

    plt.title(', '.join(title))
    ax.set_xlabel('duration (h)')
    ax.set_ylabel('energy (W)')
    (handles, labels) = ax.get_legend_handles_labels()
    handles.extend([
        mpatches.Patch(color='grey', label=f'est. duration (days): {est_duration_d:.1f}'),
        mpatches.Patch(color='grey', label=f'total time (h): {total_time_slept_h:.1f}'),
        mpatches.Patch(color='grey', label=f'total sessions: {len(x_durations):d}')
    ])
    plt.legend(handles=handles, fontsize='x-small')

    plt.show()


fun_map = {
    'pre': pre,
    'post': post,
    'plot': plot,
}


def main() -> None:
    """Entry point."""
    args = parse_args()
    logging.basicConfig(format=LOGGING_FORMAT, level=args.loglevel)

    cur = init()
    fun_map[args.main_action](cur, args)
    cleanup(cur)


if __name__ == '__main__':
    main()
