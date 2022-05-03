# sntrack

Tracks the battery discharge rate during sleep.

## Description

The script is executed automatically on sleep enter and exit and persists the current timestamp and energy level in a
database.

This data can be later used to calculate and plot the discharge rate.

## Requirements

- `python3` and the following modules
    - `matplotlib` for plotting historical data
- `systemd` for automatic execution on sleep enter and exit

## Installation

- copy the script `main.py` to `/usr/local/bin/sntrack` and mark it as executable
- place a symlink of the script `/usr/local/bin/sntrack` to the directory `/usr/lib/systemd/system-sleep/`

## Usage

While using a battery as the power source, suspend and wake the system. Run `sntrack plot` to plot the recorded data.

Short sleep durations are discarded by default.

For more information see the output of the command `sntrack -h`.
