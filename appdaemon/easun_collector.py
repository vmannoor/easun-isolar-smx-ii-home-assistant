import appdaemon.plugins.hass.hassapi as hass

import json
import os
import socket
import struct
import threading
from datetime import datetime


class EASUNCollector(hass.Hass):
    def initialize(self):

        # =========================================================
        # Configuration
        # =========================================================

        # Mandatory settings
        required_settings = [
            "logger_ip",
            "local_ip",
            "output_file",
        ]
        missing_settings = [
            setting
            for setting in required_settings
            if not self.args.get(setting)
        ]
        if missing_settings:
            for setting in missing_settings:
                self.logger.error(
                    f"Required configuration '{setting}' "
                    f"is missing from apps.yaml"
                )

            raise ValueError(
                "Missing required EASUN configuration: "
                + ", ".join(missing_settings)
            )

        self.logger_ip = self.args["logger_ip"]
        self.local_ip = self.args["local_ip"]
        self.output_file = self.args["output_file"]
        self.tcp_port = int(
            self.args.get("tcp_port", 8899)
        )
        self.udp_port = int(
            self.args.get("udp_port", 58899)
        )
        self.poll_interval = int(
            self.args.get("poll_interval", 10)
        )
        self.timeout = int(
            self.args.get("timeout", 5)
        )

        # Prevent overlapping queries
        self.lock = threading.Lock()
        # =========================================================
        # Parameters to read
        # =========================================================
        self.parameters = [
            {
                "name": "MachineState",
                "address": 0x0210,
                "rate": 1,
                "format": 0,
                "unit": [
                    "Power on",
                    "Stand by",
                    "Initialization",
                    "Soft start",
                    "Running in line",
                    "Running in inverter",
                    "Invert to line",
                    "Line to invert",
                    "remain",
                    "remain",
                    "Shutdown",
                    "Fault",
                ],
            },
            {
                "name": "LineVoltage",
                "address": 0x0213,
                "rate": 0.1,
                "format": 1,
            },
            {
                "name": "BatteryVoltage",
                "address": 0x0101,
                "rate": 0.1,
                "format": 1,
            },
            {
                "name": "BatterySoc",
                "address": 0x0100,
                "rate": 1,
                "format": 0,
            },
            {
                "name": "LoadActivePower",
                "address": 0x021B,
                "rate": 1,
                "format": 0,
            },
            {
                "name": "LoadRatio",
                "address": 0x021F,
                "rate": 1,
                "format": 0,
            },
        ]

        self.logger.info(
            "EASUN collector initialized"
        )

        self.logger.info(
            f"Logger: {self.logger_ip}"
        )

        self.logger.info(
            f"TCP callback: {self.local_ip}:{self.tcp_port}"
        )

        self.logger.info(
            f"Output: {self.output_file}"
        )

        self.run_in(
            self.get_device_info,
            5
        )

        # Poll every 10 seconds
        self.run_every(
            self.poll,
            "now",
            self.poll_interval
        )

    # =========================================================
    # Main polling
    # =========================================================

    def poll(self, kwargs):

        # Don't allow overlapping polls
        if not self.lock.acquire(blocking=False):
            self.logger.warning(
                "Previous EASUN query still running - skipping"
            )
            return

        try:

            data = self.query_inverter()

            if data:
                self.write_output(data)

        except Exception as err:

            self.logger.error(
                f"EASUN query failed: {err}"
            )

        finally:

            self.lock.release()

    # =========================================================
    # Query inverter
    # =========================================================

    def query_inverter(self):

        results = {}

        # ---------------------------------------------------------
        # Create TCP server
        # ---------------------------------------------------------

        server = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        server.settimeout(self.timeout)

        try:

            server.bind(
                ("0.0.0.0", self.tcp_port)
            )

            server.listen(1)

            # -----------------------------------------------------
            # Tell datalogger to connect to our TCP server
            # -----------------------------------------------------

            self.send_logger_command()

            self.logger.debug(
                "Waiting for EASUN logger TCP connection..."
            )

            connection, address = server.accept()

            self.logger.debug(
                f"EASUN logger connected from {address}"
            )

            connection.settimeout(
                self.timeout
            )

            # -----------------------------------------------------
            # Query six parameters
            # -----------------------------------------------------

            transaction = 1

            for parameter in self.parameters:

                request = self.build_request(
                    parameter["address"],
                    transaction
                )

                self.logger.debug(
                    f"TX {parameter['name']}: "
                    f"{request.hex(' ')}"
                )

                connection.sendall(request)

                response = self.receive_response(
                    connection
                )

                if response is None:

                    raise RuntimeError(
                        f"No response for "
                        f"{parameter['name']}"
                    )

                value = self.decode_response(
                    response,
                    parameter
                )

                results[
                    parameter["name"]
                ] = value

                # Add MachineState_text
                if parameter["name"] == "MachineState":

                    states = parameter["unit"]

                    if (
                        isinstance(value, int)
                        and 0 <= value < len(states)
                    ):
                        results[
                            "MachineState_text"
                        ] = states[value]

            connection.close()

            return results

        finally:

            try:
                server.close()
            except Exception:
                pass

    # =========================================================
    # UDP command to datalogger
    # =========================================================

    def send_logger_command(self):

        command = (
            f"set>server={self.local_ip}:{self.tcp_port};"
        ).encode()

        udp = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        udp.settimeout(2)

        try:

            self.logger.debug(
                f"UDP → {self.logger_ip}:{self.udp_port}: "
                f"{command.decode()}"
            )

            udp.sendto(
                command,
                (
                    self.logger_ip,
                    self.udp_port
                )
            )

            # The logger normally responds:
            #
            # rsp>server=1;

            try:

                response, address = udp.recvfrom(1024)

                self.logger.debug(
                    f"UDP ← {address}: "
                    f"{response.decode(errors='replace')}"
                )

            except socket.timeout:

                # The original application does not require
                # the UDP response to continue.
                self.logger.debug(
                    "No UDP acknowledgement received"
                )

        finally:

            udp.close()

    # =========================================================
    # Build EASUN request
    # =========================================================

    def build_request(
        self,
        address,
        transaction
    ):

        # ---------------------------------------------------------
        # Modbus RTU portion
        #
        # FF = inverter unit
        # 03 = Read Holding Registers
        # address = register
        # 0001 = one register
        #
        # CRC is calculated over:
        #
        # FF 03 address 00 01
        # ---------------------------------------------------------

        rtu_without_crc = struct.pack(
            ">BBHH",
            0xFF,
            0x03,
            address,
            1
        )

        crc = self.crc16_modbus(
            rtu_without_crc
        )

        # EASUN expects CRC low byte first.
        crc_bytes = struct.pack(
            "<H",
            crc
        )

        rtu = (
            rtu_without_crc +
            crc_bytes
        )

        # ---------------------------------------------------------
        # EASUN gateway payload
        #
        # FF 04 = gateway
        # FF 03 = inverter Modbus request
        # ---------------------------------------------------------

        payload = (
            bytes([
                0xFF,
                0x04
            ])
            + rtu
        )

        # Payload is always 10 bytes:
        #
        # FF 04 FF 03 AA AA 00 01 CRC CRC
        #
        # Therefore length = 000A
        # ---------------------------------------------------------

        packet = struct.pack(
            ">HHH",
            transaction,
            0,
            len(payload)
        ) + payload

        return packet

    # =========================================================
    # Receive complete response
    # =========================================================

    def receive_response(
        self,
        connection
    ):

        # EASUN TCP header:
        #
        # transaction 2 bytes
        # protocol    2 bytes
        # length      2 bytes
        #

        header = self.recv_exact(
            connection,
            6
        )

        if not header:
            return None

        transaction, protocol, length = struct.unpack(
            ">HHH",
            header
        )

        if length <= 0 or length > 1024:

            raise RuntimeError(
                f"Invalid response length: {length}"
            )

        payload = self.recv_exact(
            connection,
            length
        )

        if payload is None:
            return None

        response = header + payload

        self.logger.debug(
            f"RX: {response.hex(' ')}"
        )

        return response

    # =========================================================
    # Receive exact number of bytes
    # =========================================================

    @staticmethod
    def recv_exact(
        connection,
        length
    ):

        data = b""

        while len(data) < length:

            chunk = connection.recv(
                length - len(data)
            )

            if not chunk:
                return None

            data += chunk

        return data

    # =========================================================
    # Decode response
    # =========================================================

    def decode_response(
        self,
        response,
        parameter
    ):

        # Response structure:
        #
        # 0-1   transaction
        # 2-3   protocol
        # 4-5   length
        # 6     FF
        # 7     04
        # 8     FF
        # 9     03
        # 10    byte count
        # 11..  data
        # last  CRC
        #

        if len(response) < 13:

            raise RuntimeError(
                "Response too short"
            )

        byte_count = response[10]

        data_start = 11
        data_end = data_start + byte_count

        if data_end + 2 > len(response):

            raise RuntimeError(
                "Invalid response data length"
            )

        data = response[
            data_start:data_end
        ]

        # ---------------------------------------------------------
        # CRC
        #
        # Original Node implementation calculates CRC over:
        #
        # response[8:-2]
        #
        # which starts at FF 03.
        # ---------------------------------------------------------

        crc_data = response[8:-2]

        calculated_crc = self.crc16_modbus(
            crc_data
        )

        received_crc = response[-2:]

        expected_crc = struct.pack(
            "<H",
            calculated_crc
        )

        if received_crc != expected_crc:

            raise RuntimeError(
                f"CRC error for "
                f"{parameter['name']}: "
                f"received={received_crc.hex()} "
                f"expected={expected_crc.hex()}"
            )

        # ---------------------------------------------------------
        # UInt16BE
        # ---------------------------------------------------------

        if len(data) < 2:

            raise RuntimeError(
                f"No register data for "
                f"{parameter['name']}"
            )

        value = int.from_bytes(
            data[:2],
            byteorder="big",
            signed=False
        )

        # Apply rate
        value *= parameter["rate"]

        # Apply format
        value = round(
            value,
            parameter["format"]
        )

        # Keep integer values as integers
        if (
            isinstance(value, float)
            and value.is_integer()
        ):
            value = int(value)

        return value

    # =========================================================
    # CRC16 / MODBUS
    # =========================================================

    @staticmethod
    def crc16_modbus(data):

        crc = 0xFFFF

        for byte in data:

            crc ^= byte

            for _ in range(8):

                if crc & 0x0001:

                    crc >>= 1
                    crc ^= 0xA001

                else:

                    crc >>= 1

        return crc

    # =========================================================
    # Write JSON
    # =========================================================

    def write_output(
        self,
        data
    ):

        # Timestamp represents the LAST SUCCESSFUL
        # complete inverter query.

        data["updated_at"] = (
            datetime.now()
            .astimezone()
            .isoformat()
        )

        temp_file = (
            self.output_file + ".tmp"
        )

        try:

            with open(
                temp_file,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    data,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

                file.write("\n")

            # Atomic replacement.
            os.replace(
                temp_file,
                self.output_file
            )

            self.logger.info(
                "EASUN updated: "
                f"State={data.get('MachineState')} "
                f"Grid={data.get('LineVoltage')} V "
                f"Battery={data.get('BatteryVoltage')} V "
                f"SOC={data.get('BatterySoc')}% "
                f"Load={data.get('LoadActivePower')} W "
                f"LoadRatio={data.get('LoadRatio')}%"
            )

        except Exception as err:

            self.logger.error(
                f"Failed writing "
                f"{self.output_file}: {err}"
            )

            try:

                if os.path.exists(temp_file):
                    os.remove(temp_file)

            except Exception:
                pass

    def get_device_info(self, kwargs):
        self.logger.info(
            "Querying EASUN WiFi logger device information..."
        )
        try:
            info = self.query_device_info()
            if info:
                self.logger.info(
                    f"EASUN WiFi logger information: {info}"
                )
            else:
                self.logger.warning(
                    "EASUN WiFi logger returned no device information"
                )
        except Exception as err:
            self.logger.error(
                f"Failed to query EASUN WiFi logger information: {err}"
            )

    def query_device_info(self):

        server = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )
        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )
        server.settimeout(self.timeout)
        try:
            server.bind(
                ("0.0.0.0", self.tcp_port)
            )
            server.listen(1)
            # Tell logger to connect back to us.
            self.send_logger_command()
            self.logger.debug(
                "Waiting for EASUN logger TCP connection "
                "for device-info query..."
            )
            connection, address = server.accept()
            self.logger.debug(
                f"EASUN logger connected from {address}"
            )
            connection.settimeout(
                self.timeout
            )
            # -----------------------------------------------------
            # 1. Get WiFi device ID
            # -----------------------------------------------------
            request = self.build_device_info_request(
                "id",
                1
            )
            self.logger.debug(
                f"TX get_wifi_device_id: "
                f"{request.hex(' ')}"
            )
            connection.sendall(request)
            response_id = self.receive_response(
                connection
            )
            device_id = self.decode_device_id(
                response_id
            )
            # -----------------------------------------------------
            # 2. Get WiFi device info
            # -----------------------------------------------------
            request = self.build_device_info_request(
                "info",
                2
            )
            self.logger.debug(
                f"TX get_wifi_device_info: "
                f"{request.hex(' ')}"
            )
            connection.sendall(request)
            response_info = self.receive_response(
                connection
            )
            device_info = self.decode_device_info(
                response_info
            )
            connection.close()
            return {
                "device_id": device_id,
                "device_info": device_info,
            }
        finally:
            try:
                server.close()
            except Exception:
                pass

    def build_device_info_request(
        self,
        command,
        transaction
    ):
        if command == "id":

            payload = bytes.fromhex(
                "ff01160b0a16102d012c"
            )
        elif command == "info":

            payload = bytes.fromhex(
                "ff0205"
            )
        else:

            raise ValueError(
                f"Unknown device-info command: {command}"
            )
        return (
            struct.pack(
                ">HHH",
                transaction,
                0,
                len(payload)
            )
            + payload
        )

    def decode_device_id(self, response):
        if not response:
            return None
        try:
            device_id = response[8:].decode(
                "ascii",
                errors="ignore"
            ).strip("\x00")
            return device_id
        except Exception as err:

            self.logger.error(
                f"Failed to decode device ID: {err}"
            )
            return None

    def decode_device_info(self, response):
        if not response:
            return None
        try:
            firmware = response[10:].decode(
                "ascii",
                errors="ignore"
            ).strip("\x00")
            return firmware
        except Exception as err:
            self.logger.error(
                f"Failed to decode device info: {err}"
            )
            return None   
