# Smart Heating Optimizer

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant integration for smart heating optimization based on electricity spot prices.

## Features

- Automatic thermostat control based on spot prices
- ML-powered temperature predictions
- Per-room/zone configuration
- Energy savings tracking

## Installation

### HACS (Recommended)

1. Open HACS
2. Click three dots menu → Custom repositories
3. Add: `https://github.com/jtecio/smart-heating-optimizer`
4. Category: Integration
5. Install "Smart Heating Optimizer"
6. Restart Home Assistant

### Manual

Copy `custom_components/smart_heating_optimizer` to your HA config folder.

## Configuration

1. Go to Settings → Devices & Services
2. Add Integration → "Smart Heating Optimizer"
3. Enter API credentials
4. Add zones with temperature sensors and thermostats

## Requirements

- IoT Platform account (iot.jtec.io)
- API key with HA permissions

## License

MIT
