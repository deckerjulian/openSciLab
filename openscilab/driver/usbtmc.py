# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""USB Test and Measurement Class (USBTMC): SCPI over USB.

Instruments with a USB device port (oscilloscopes, generators, multimeters) present an interface of
class 0xFE, subclass 0x03. Every message goes in a bulk transfer with a 12 byte header
(USBTMC 1.0, 3.2): ``DEV_DEP_MSG_OUT`` carries a command; an answer is asked for with
``REQUEST_DEV_DEP_MSG_IN`` and comes back on the bulk-in endpoint, as many transfers as it takes,
the last one marked end of message (EOM). After a timeout the request for the answer is taken back
(``INITIATE_ABORT_BULK_IN``); an answer that still comes late carries the tag of an earlier request
and is dropped, or it comes with the next request and the SCPI layer (``ScpiConnection``) drops it
when it gets back in step. Not ``INITIATE_CLEAR``: on a DHO924S (modified firmware NK v0.5.2) it stops the
instrument from reading commands until it restarts.

:class:`UsbTmcLink` speaks the protocol over a :class:`UsbTmcPort` (pyusb, with the libusb of
``libusb-package`` as for the DSLogic); the tests replace the port with a fake instrument.
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

INTERFACE_CLASS = 0xFE
INTERFACE_SUBCLASS = 0x03

DEV_DEP_MSG_OUT = 1
REQUEST_DEV_DEP_MSG_IN = 2
DEV_DEP_MSG_IN = 2
HEADER_SIZE = 12
EOM = 0x01

# class requests (USBTMC 1.0, table 15)
INITIATE_ABORT_BULK_OUT = 1
INITIATE_ABORT_BULK_IN = 3
CHECK_ABORT_BULK_IN_STATUS = 4
INITIATE_CLEAR = 5
CHECK_CLEAR_STATUS = 6
STATUS_SUCCESS = 0x01
STATUS_PENDING = 0x02
STATUS_TRANSFER_NOT_IN_PROGRESS = 0x81

#: largest answer asked for with one REQUEST_DEV_DEP_MSG_IN (a longer one comes in more transfers)
READ_SIZE = 1 << 20
#: largest command data per DEV_DEP_MSG_OUT
WRITE_SIZE = 1 << 16


class UsbTmcError(OSError):
    """A USB operation failed (instrument gone, access denied, a broken answer)."""


class UsbTmcTimeout(UsbTmcError):
    """No answer in time."""


@dataclass(frozen=True)
class UsbTmcInfo:
    """An instrument with a USBTMC interface on the bus."""

    bus: int
    address: int
    vendor: int
    product: int
    manufacturer: str = ""
    name: str = ""
    serial_number: str = ""

    @property
    def location(self) -> str:
        return f"{self.bus}:{self.address}"

    @property
    def label(self) -> str:
        what = " ".join(part for part in (self.manufacturer, self.name) if part) or \
            f"USB instrument {self.vendor:04x}:{self.product:04x}"
        return f"{what} (USB{', ' + self.serial_number if self.serial_number else ''})"


def header(message: int, tag: int, size: int, attributes: int = 0, term_char: int = 0) -> bytes:
    """The 12 byte header of a bulk transfer."""
    return struct.pack("<BBBxIBBxx", message, tag, (~tag) & 0xFF, size, attributes, term_char)


def message_out(tag: int, data: bytes, end: bool = True) -> bytes:
    """``DEV_DEP_MSG_OUT``: header, data, padding to a multiple of 4 bytes."""
    padding = (-len(data)) % 4
    return header(DEV_DEP_MSG_OUT, tag, len(data), EOM if end else 0) + data + b"\x00" * padding


def request_in(tag: int, size: int = READ_SIZE) -> bytes:
    """``REQUEST_DEV_DEP_MSG_IN``: the instrument may send up to ``size`` bytes of its answer."""
    return header(REQUEST_DEV_DEP_MSG_IN, tag, size)


def parse_in(transfer: bytes, tag: int) -> tuple[bytes, bool]:
    """The data of a ``DEV_DEP_MSG_IN`` transfer and whether it ends the message."""
    if len(transfer) < HEADER_SIZE:
        raise UsbTmcError(f"an answer of {len(transfer)} bytes has no USBTMC header")
    message, got_tag, inverse, size, attributes = struct.unpack_from("<BBBxIB", transfer)
    if message != DEV_DEP_MSG_IN or got_tag != tag or inverse != (~tag) & 0xFF:
        raise UsbTmcError(f"an answer that does not belong to the request (message {message}, tag {got_tag})")
    if len(transfer) < HEADER_SIZE + size:
        raise UsbTmcError(f"an answer shorter than it says ({len(transfer) - HEADER_SIZE} of {size} bytes)")
    return bytes(transfer[HEADER_SIZE:HEADER_SIZE + size]), bool(attributes & EOM)


class UsbTmcPort:
    """The USB operations of one USBTMC interface (replaced by a fake in the tests)."""

    def write(self, data: bytes, timeout: float) -> None:
        raise NotImplementedError

    def read(self, size: int, timeout: float) -> bytes:
        """One bulk-in transfer; raises :class:`UsbTmcTimeout` when nothing came."""
        raise NotImplementedError

    def control_in(self, request: int, value: int, length: int, timeout: float) -> bytes:
        """A class request to the interface (recipient interface, direction in)."""
        raise NotImplementedError

    def control_in_endpoint(self, request: int, value: int, length: int, timeout: float) -> bytes:
        """A class request to the bulk-in endpoint (recipient endpoint, direction in)."""
        raise NotImplementedError

    def clear_halt(self) -> None:
        """Clears the halt of the bulk-out endpoint (after INITIATE_CLEAR)."""

    def close(self) -> None:
        pass


class UsbTmcLink:
    """Messages to and answers from one instrument (see the module)."""

    def __init__(self, port: UsbTmcPort, name: str = "USB instrument") -> None:
        self.port = port
        self.name = name
        self._tag = 0

    def _next_tag(self) -> int:
        self._tag = self._tag % 255 + 1  # 1 ... 255, never 0
        return self._tag

    def write(self, data: bytes, timeout: float) -> None:
        """A whole message (in pieces of at most :data:`WRITE_SIZE`, the last one with EOM)."""
        for start in range(0, max(len(data), 1), WRITE_SIZE):
            piece = data[start:start + WRITE_SIZE]
            self.port.write(message_out(self._next_tag(), piece, start + WRITE_SIZE >= len(data)), timeout)

    def read(self, timeout: float) -> bytes:
        """The next part of an answer (up to the end of the message or :data:`READ_SIZE`)."""
        tag = self._next_tag()
        self.port.write(request_in(tag), timeout)
        deadline = time.monotonic() + timeout
        while True:
            try:
                transfer = self._transfer(deadline)
            except UsbTmcTimeout:
                self.abort_in(tag)  # (the request is still open: its late answer would come with the next one)
                raise
            message, got_tag = struct.unpack_from("<BB", transfer)
            if message == DEV_DEP_MSG_IN and got_tag != tag:
                log.debug("%s: dropped a late answer (tag %d, waiting for %d)", self.name, got_tag, tag)
                continue
            data, _end = parse_in(transfer, tag)  # (no EOM: the rest comes with the next request)
            return data

    def _transfer(self, deadline: float) -> bytes:
        """One transfer: the header says how much data follows (libusb may hand it over in parts)."""
        transfer = bytearray()
        while len(transfer) < HEADER_SIZE or len(transfer) < HEADER_SIZE + struct.unpack_from("<I", transfer, 4)[0]:
            remaining = max(deadline - time.monotonic(), 0.001)
            transfer += self.port.read(HEADER_SIZE + READ_SIZE + 3, remaining)
        return bytes(transfer)

    def abort_in(self, tag: int) -> None:
        """``INITIATE_ABORT_BULK_IN``: the instrument drops the request ``tag`` (and what it was sending)."""
        try:
            status = self.port.control_in_endpoint(INITIATE_ABORT_BULK_IN, tag, 2, 1.0)
            if status[:1] != bytes([STATUS_SUCCESS]):
                return  # (STATUS_TRANSFER_NOT_IN_PROGRESS: nothing to drop)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                self.drain()
                answer = self.port.control_in_endpoint(CHECK_ABORT_BULK_IN_STATUS, 0, 8, 1.0)
                if answer[:1] != bytes([STATUS_PENDING]):
                    break
                time.sleep(0.01)
        except UsbTmcError as error:
            log.debug("Taking back the request of %s failed: %s", self.name, error)

    def drain(self, timeout: float = 0.05) -> int:
        """Reads and drops what the instrument still sends (an answer to an earlier program); bytes."""
        dropped = 0
        while True:
            try:
                dropped += len(self.port.read(HEADER_SIZE + READ_SIZE + 3, timeout))
            except UsbTmcTimeout:
                return dropped
            except UsbTmcError as error:
                log.debug("Draining %s failed: %s", self.name, error)
                return dropped

    def clear(self) -> None:
        """``INITIATE_CLEAR``: the instrument empties its input and output buffers. Not used on its
        own: the DHO900 stops reading commands after it (see the module)."""
        try:
            status = self.port.control_in(INITIATE_CLEAR, 0, 1, 1.0)
            if status[:1] == bytes([STATUS_SUCCESS]):
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    answer = self.port.control_in(CHECK_CLEAR_STATUS, 0, 2, 1.0)
                    if answer[:1] != bytes([STATUS_PENDING]):
                        break
                    time.sleep(0.01)
            self.port.clear_halt()
        except UsbTmcError as error:
            log.debug("Clearing %s failed: %s", self.name, error)

    def close(self) -> None:
        self.port.close()


# ------------------------------------------------------------------- pyusb
def _backend():
    from .dslogic.usb import _backend as libusb_backend  # (the libusb of libusb-package, as for the DSLogic)

    return libusb_backend()


def _string(device, index: int) -> str:
    import usb.util

    try:
        return (usb.util.get_string(device, index) or "").strip() if index else ""
    except Exception:  # noqa: BLE001 - no permission or no string
        return ""


def _tmc_interface(device):
    """The USBTMC interface of ``device`` (``None`` when it has none)."""
    try:
        for configuration in device:
            for interface in configuration:
                if (interface.bInterfaceClass, interface.bInterfaceSubClass) == (INTERFACE_CLASS, INTERFACE_SUBCLASS):
                    return configuration, interface
    except Exception:  # noqa: BLE001 - descriptors that cannot be read
        log.debug("The descriptors of %s could not be read", device, exc_info=True)
    return None


def list_instruments(vendor: Optional[int] = None) -> list[UsbTmcInfo]:
    """The instruments with a USBTMC interface (of ``vendor``); empty without libusb."""
    backend = _backend()
    if backend is None:
        return []
    try:
        import usb.core

        options = {"idVendor": vendor} if vendor is not None else {}
        devices = list(usb.core.find(find_all=True, backend=backend, **options))
    except Exception as error:  # noqa: BLE001 - the bus could not be enumerated
        log.debug("USB enumeration failed: %s", error)
        return []
    found = []
    for device in devices:
        if _tmc_interface(device) is None:
            continue
        found.append(UsbTmcInfo(device.bus, device.address, device.idVendor, device.idProduct,
                                _string(device, device.iManufacturer), _string(device, device.iProduct),
                                _string(device, device.iSerialNumber)))
    return found


class PyUsbTmcPort(UsbTmcPort):
    """The USBTMC interface of an instrument opened through pyusb."""

    def __init__(self, info: UsbTmcInfo) -> None:
        import usb.core
        import usb.util

        backend = _backend()
        if backend is None:
            raise UsbTmcError("No USB library (libusb) found. Install the Python package 'libusb-package' "
                              "or the libusb library of your system.")
        device = usb.core.find(idVendor=info.vendor, idProduct=info.product, backend=backend,
                               custom_match=lambda d: d.bus == info.bus and d.address == info.address)
        if device is None:
            raise UsbTmcError(f"{info.label} is no longer connected.")
        found = _tmc_interface(device)
        if found is None:
            raise UsbTmcError(f"{info.label} has no USBTMC interface.")
        configuration, interface = found
        self._device, self._number = device, interface.bInterfaceNumber
        try:
            try:
                if device.is_kernel_driver_active(self._number):
                    device.detach_kernel_driver(self._number)  # (the usbtmc module of Linux)
            except (NotImplementedError, Exception):  # noqa: BLE001 - not supported everywhere
                pass
            try:
                if device.get_active_configuration().bConfigurationValue != configuration.bConfigurationValue:
                    device.set_configuration(configuration.bConfigurationValue)
            except Exception:  # noqa: BLE001 - not configured yet
                device.set_configuration(configuration.bConfigurationValue)
            usb.util.claim_interface(device, self._number)
        except Exception as error:  # noqa: BLE001 - normalised
            raise UsbTmcError(self._access_text(error)) from error
        endpoints = list(interface)
        direction = usb.util.endpoint_direction
        kind = usb.util.endpoint_type
        bulk = [endpoint for endpoint in endpoints if kind(endpoint.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK]
        self._out = next((e for e in bulk if direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT), None)
        self._in = next((e for e in bulk if direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN), None)
        if self._out is None or self._in is None:
            raise UsbTmcError(f"{info.label}: the USBTMC interface has no bulk endpoints.")

    @staticmethod
    def _access_text(error: Exception) -> str:
        text = str(error)
        if getattr(error, "errno", None) == 13 or "access" in text.lower():
            return (f"Access to the instrument was denied ({text}). On Linux add a udev rule for it; on "
                    "Windows it needs the WinUSB driver (e.g. installed with Zadig) instead of the VISA one.")
        return f"The instrument could not be opened ({text}). Is it used by another program?"

    @staticmethod
    def _wrap(error: Exception) -> UsbTmcError:
        import usb.core

        if isinstance(error, usb.core.USBTimeoutError):
            return UsbTmcTimeout(str(error))
        return UsbTmcError(str(error))

    def write(self, data: bytes, timeout: float) -> None:
        try:
            self._out.write(data, int(timeout * 1000))
        except Exception as error:  # noqa: BLE001 - normalised
            raise self._wrap(error) from error

    def read(self, size: int, timeout: float) -> bytes:
        try:
            return bytes(self._in.read(size, max(int(timeout * 1000), 1)))
        except Exception as error:  # noqa: BLE001 - normalised
            raise self._wrap(error) from error

    def control_in(self, request: int, value: int, length: int, timeout: float) -> bytes:
        try:
            # class request, recipient interface, device to host
            return bytes(self._device.ctrl_transfer(0xA1, request, value, self._number, length,
                                                    int(timeout * 1000)))
        except Exception as error:  # noqa: BLE001 - normalised
            raise self._wrap(error) from error

    def control_in_endpoint(self, request: int, value: int, length: int, timeout: float) -> bytes:
        try:
            # class request, recipient endpoint (the bulk-in one), device to host
            return bytes(self._device.ctrl_transfer(0xA2, request, value, self._in.bEndpointAddress, length,
                                                    int(timeout * 1000)))
        except Exception as error:  # noqa: BLE001 - normalised
            raise self._wrap(error) from error

    def clear_halt(self) -> None:
        try:
            self._out.clear_halt()
        except Exception:  # noqa: BLE001 - not halted
            log.debug("clear_halt of the bulk-out endpoint failed", exc_info=True)

    def close(self) -> None:
        import usb.util

        try:
            usb.util.release_interface(self._device, self._number)
            usb.util.dispose_resources(self._device)
        except Exception:  # noqa: BLE001 - best effort
            log.debug("Releasing the USBTMC interface failed", exc_info=True)


def open_link(info: UsbTmcInfo) -> UsbTmcLink:
    link = UsbTmcLink(PyUsbTmcPort(info), info.label)
    link.drain()  # (what an earlier program left unread would be taken for the first answer)
    return link
