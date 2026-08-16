# EASUN iSolar SMX-II → Home Assistant

A Python/AppDaemon collector for retrieving data from an
EASUN iSolar SMX-II inverter through the original EASUN WiFi logger.

## Features

- Uses the original EASUN WiFi logger
- Keeps SmartESS functionality
- No additional hardware required
- Python/AppDaemon based
- Reads inverter data every 10 seconds
- Retrieves only the required inverter parameters
- Retrieves WiFi logger information once at startup
- Outputs data as JSON for Home Assistant

## Retrieved parameters

- Machine State
- Line Voltage
- Battery Voltage
- Battery SOC
- Load Active Power
- Load Ratio

## Requirements

- EASUN iSolar SMX-II
- EASUN WiFi logger
- Home Assistant
- AppDaemon
- Docker, if using a Docker-based installation

## Installation

See the complete installation instructions in the Medium article:
[[Integrating an EASUN iSolar SMX-II with Home Assistant and AppDaemon]](https://medium.com/@vmannoor/integrating-easun-isolar-smx-ii-inverter-with-home-assistant-and-appdaemon-using-the-existing-wifi-8ebdbc2484aa?postPublishedType=initial)
