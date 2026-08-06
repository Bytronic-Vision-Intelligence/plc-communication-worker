"""Vecow digital I/O controller (app_v2).

Loads vendor DLLs + IOConfig from:
  1. ``VECOW_DLL_DIR`` env var
  2. ``Dependencies/Vecow`` next to this module
"""
from __future__ import annotations

import ctypes
from ctypes import byref, c_ubyte, c_ushort
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from filelock import FileLock

logger = logging.getLogger(__name__)

_REQUIRED_DLLS = ("drv.dll", "Vecow.dll", "drv_alim.dll", "Vecow_alim.dll")
_DEPENDENCIES_DIR = Path(__file__).resolve().parent


def default_dll_dir() -> Path:
    """Locate Vecow DLLs (env override, then Dependencies/Vecow)."""
    env = os.environ.get("VECOW_DLL_DIR")
    if env:
        return Path(env).expanduser().resolve()

    candidate = _DEPENDENCIES_DIR / "Vecow"
    if all((candidate / name).is_file() for name in _REQUIRED_DLLS):
        return candidate.resolve()

    # Incomplete/missing tree — return path so errors stay explicit.
    return candidate.resolve()


def _bios_version() -> str | None:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return out or None
    except Exception:
        return None


def _ensure_ioconfig_xml(dll_dir: Path) -> None:
    """Vecow loads ./IOConfig/<BIOS_name>.xml; alias a prefix match if needed."""
    iocfg = dll_dir / "IOConfig"
    if not iocfg.is_dir():
        logger.error("Missing IOConfig folder at %s", iocfg)
        return

    bios = _bios_version()
    if not bios:
        logger.warning("Could not read BIOS version for IOConfig XML lookup")
        return

    target = iocfg / f"{bios}.xml"
    if target.is_file():
        logger.info("IOConfig XML present for BIOS %s", bios)
        return

    xmls = sorted(iocfg.glob("*.xml"))
    match = None
    for xml in sorted(xmls, key=lambda p: len(p.stem), reverse=True):
        stem = xml.stem.strip()
        if stem and bios.startswith(stem):
            match = xml
            break
    if match is None:
        logger.warning(
            "No IOConfig XML prefix-match for BIOS %s (have %s)",
            bios,
            [p.stem for p in xmls[:10]],
        )
        return

    shutil.copy2(match, target)
    logger.info("Aliased %s -> %s for BIOS %s", match.name, target.name, bios)


class VecowIO:
    """Vecow DIO1 controller (flat pins 0..7)."""

    def __init__(self, dll_dir=None):
        self._lock = threading.Lock()
        dll_dir = Path(dll_dir) if dll_dir else default_dll_dir()
        self.dll_dir = dll_dir.resolve()
        self.initialized_io = False
        self.initialized_poe = False
        self.drv_dll = None
        self.other_drv_dll = None
        self.dll = None
        self.other_dll = None

        if not self.dll_dir.is_dir():
            logger.error("DLL dir not found: %s", self.dll_dir)
            return
        missing = [n for n in _REQUIRED_DLLS if not (self.dll_dir / n).is_file()]
        if missing:
            logger.error("Missing DLL(s) in %s: %s", self.dll_dir, ", ".join(missing))
            return

        # DLL resolves ./IOConfig/ from process CWD.
        os.chdir(self.dll_dir)
        _ensure_ioconfig_xml(self.dll_dir)
        try:
            self.drv_dll = ctypes.CDLL(str(self.dll_dir / "drv.dll"))
            self.other_drv_dll = ctypes.CDLL(str(self.dll_dir / "drv_alim.dll"))
            self.dll = ctypes.CDLL(str(self.dll_dir / "Vecow.dll"))
            self.other_dll = ctypes.CDLL(str(self.dll_dir / "Vecow_alim.dll"))

            with FileLock(str(self.dll_dir / "dio_lock.lock")):
                self.initialize_io()
                self.initialize_poe()
        except Exception:
            logger.exception("Failed to load Vecow DLLs from %s", self.dll_dir)
            self.dll = None
            self.drv_dll = None
            self.other_dll = None
            self.other_drv_dll = None
            self.initialized_io = False
            self.initialized_poe = False

    def initialize_io(self):
        if not self.other_dll:
            return False
        try:
            self.other_dll.initial_SIO(c_ubyte(1), c_ubyte(0))
            for i in range(10):
                result = self.set_io_config()
                if result:
                    logger.info("set_io_config succeeded on attempt %s", i)
                    break
                time.sleep(0.1)
            else:
                logger.warning("set_io_config failed after 10 attempts")
            self.initialized_io = True
            self.set_do(0b00000000)
            return True
        except Exception:
            logger.exception("initialize_io failed")
            return False

    def initialize_poe(self):
        if not self.other_dll:
            return False
        try:
            result = self.other_dll.initial_POE(c_ubyte(2), c_ubyte(0))
            logger.info("initial_POE result=%s", result)
            self.get_poe_config()
            result = self.set_poe_config(0, 0b0000, 0b1111)
            logger.info("set_poe_config result=%s", result)
            self.get_poe_config()
            self.initialized_poe = True
            return True
        except Exception:
            logger.exception("initialize_poe failed")
            return False

    def set_io_config(self):
        if not self.other_dll:
            return False
        try:
            return self.other_dll.set_IO1_configuration(
                c_ubyte(1),  # isolated
                c_ubyte(0),  # NPN type
                c_ubyte(1),  # NPN sink
                c_ushort(0b1111111100000000),
            )
        except Exception:
            logger.exception("set_io_config failed")
            return False

    def get_io_config(self):
        if not self.other_dll:
            return False
        try:
            dio_iso, dio_npn, dio_npns, dio_m = c_ubyte(), c_ubyte(), c_ubyte(), c_ushort()
            result = self.other_dll.get_IO1_configuration(
                byref(dio_iso), byref(dio_npn), byref(dio_npns), byref(dio_m)
            )
            logger.info(
                "get_io_config result=%s DIOIso=%s DIONPN=%s DIONPNs=%s DIOM=%s",
                result,
                dio_iso.value,
                dio_npn.value,
                dio_npns.value,
                dio_m.value,
            )
            return result
        except Exception:
            logger.exception("get_io_config failed")
            return False

    def get_poe_config(self):
        if not self.other_dll:
            return False
        try:
            first, second, third = c_ubyte(0), c_ubyte(), c_ubyte()
            result = self.other_dll.get_POE_configuration(
                first, byref(second), byref(third)
            )
            logger.info(
                "get_poe_config result=%s first=%s second=%s third=%s",
                result,
                first.value,
                second.value,
                third.value,
            )
            return result
        except Exception:
            logger.exception("get_poe_config failed")
            return False

    def set_poe_config(self, first_byte=0, second_byte=0, third_byte=0):
        if not self.other_dll:
            return False
        try:
            return self.other_dll.set_POE_configuration(
                c_ubyte(first_byte), c_ubyte(second_byte), c_ushort(third_byte)
            )
        except Exception:
            logger.exception("set_poe_config failed")
            return False

    def _validate_bank(self, bank: int) -> int:
        bank = int(bank)
        if bank not in (1, 2):
            raise ValueError(f"bank must be 1 or 2, got {bank}")
        return bank

    def set_do(self, value, bank: int = 1):
        """Write a full 8-bit DO bank (1 or 2)."""
        if not self.other_dll or not self.initialized_io:
            return False
        try:
            bank = self._validate_bank(bank)
            raw = c_ubyte(value & 0xFF)
            if bank == 1:
                return bool(self.other_dll.set_DIO1(raw))
            return bool(self.other_dll.set_DIO2(raw))
        except Exception:
            logger.exception("set_do failed bank=%s value=%s", bank, value)
            return False

    def get_di(self, bank: int = 1):
        """Read DO mirror + DI for a bank: (do_byte, di_byte) or (None, None)."""
        if not self.other_dll or not self.initialized_io:
            return None, None
        try:
            bank = self._validate_bank(bank)
            do_byte, di_byte = c_ubyte(), c_ubyte()
            if bank == 1:
                result = self.other_dll.get_DIO1(byref(do_byte), byref(di_byte))
            else:
                result = self.other_dll.get_DIO2(byref(do_byte), byref(di_byte))
            if result:
                return do_byte.value, di_byte.value
            return None, None
        except Exception:
            logger.exception("get_di failed bank=%s", bank)
            return None, None

    def set_poe(self, value):
        if not self.other_dll or not self.initialized_poe:
            return False
        try:
            return self.other_dll.set_POE(c_ubyte(0), c_ubyte(value & 0xFF))
        except Exception:
            logger.exception("set_poe failed value=%s", value)
            return False

    def get_poe(self):
        if not self.other_dll or not self.initialized_poe:
            return None
        try:
            poe_0, poe_1 = c_ubyte(0), c_ubyte()
            result = self.other_dll.get_POE(poe_0, byref(poe_1))
            if result:
                return poe_1.value
            return None
        except Exception:
            logger.exception("get_poe failed")
            return None

    def set_do_pin(self, pin, value, bank: int = 1):
        """Set one DO pin (0..7) on bank 1 or 2."""
        if not self.other_dll or not self.initialized_io:
            return False
        try:
            bank = self._validate_bank(bank)
            pin = int(pin)
            if not 0 <= pin <= 7:
                raise ValueError(f"pin must be 0..7, got {pin}")
            with self._lock:
                current_value, _ = self.get_di(bank)
                if current_value is None:
                    return False
                bits = [int(b) for b in bin(current_value)[2:].zfill(8)][::-1]
                bits[pin] = int(value)
                return bool(self.set_do(int("".join(map(str, bits[::-1])), 2), bank=bank))
        except Exception:
            logger.exception(
                "set_do_pin failed bank=%s pin=%s value=%s", bank, pin, value
            )
            return False

    def set_do_pins(self, pins, value, bank: int = 1):
        """Set multiple DO pins (0..7) on a bank to the same value."""
        if not self.other_dll or not self.initialized_io:
            return False
        try:
            bank = self._validate_bank(bank)
            with self._lock:
                current_value, _ = self.get_di(bank)
                if current_value is None:
                    return False
                bits = [int(b) for b in bin(current_value)[2:].zfill(8)][::-1]
                for pin in pins:
                    pin = int(pin)
                    if not 0 <= pin <= 7:
                        logger.warning("Ignoring out-of-range DO pin %s", pin)
                        continue
                    bits[pin] = int(value)
                return bool(self.set_do(int("".join(map(str, bits[::-1])), 2), bank=bank))
        except Exception:
            logger.exception(
                "set_do_pins failed bank=%s pins=%s value=%s", bank, pins, value
            )
            return False

    def get_di_pin(self, pin, bank: int = 1):
        """Read one DI pin (0..7) on bank 1 or 2."""
        if not self.other_dll or not self.initialized_io:
            return None
        try:
            bank = self._validate_bank(bank)
            pin = int(pin)
            if not 0 <= pin <= 7:
                raise ValueError(f"pin must be 0..7, got {pin}")
            _, current_value2 = self.get_di(bank)
            if current_value2 is None:
                return None
            bits = [int(b) for b in bin(current_value2)[2:].zfill(8)][::-1]
            return bits[pin]
        except Exception:
            logger.exception("get_di_pin failed bank=%s pin=%s", bank, pin)
            return None

    def get_di_pins(self, pins, bank: int = 1):
        """Read multiple DI pins (0..7) on a bank."""
        if not self.other_dll or not self.initialized_io:
            return None
        try:
            bank = self._validate_bank(bank)
            _, current_value2 = self.get_di(bank)
            if current_value2 is None:
                return None
            bits = [int(b) for b in bin(current_value2)[2:].zfill(8)][::-1]
            out: list[int] = []
            for pin in pins:
                pin = int(pin)
                if not 0 <= pin <= 7:
                    logger.warning("Ignoring out-of-range DI pin %s", pin)
                    continue
                out.append(bits[pin])
            return out
        except Exception:
            logger.exception("get_di_pins failed bank=%s pins=%s", bank, pins)
            return None

    def get_di_vector(self, bank: int = 1, count: int = 8) -> list[int] | None:
        """Read DI pins 0..count-1 as a 0/1 vector."""
        count = max(0, min(8, int(count)))
        if count == 0:
            return []
        vals = self.get_di_pins(list(range(count)), bank=bank)
        if vals is None:
            return None
        return [1 if int(v) else 0 for v in vals]

    def get_do_pin(self, pin, bank: int = 1):
        """Read mirrored DO pin state (0..7) on a bank."""
        if not self.other_dll or not self.initialized_io:
            return None
        try:
            bank = self._validate_bank(bank)
            pin = int(pin)
            if not 0 <= pin <= 7:
                raise ValueError(f"pin must be 0..7, got {pin}")
            current_value, _ = self.get_di(bank)
            if current_value is None:
                return None
            bits = [int(b) for b in bin(current_value)[2:].zfill(8)][::-1]
            return bits[pin]
        except Exception:
            logger.exception("get_do_pin failed bank=%s pin=%s", bank, pin)
            return None

    def close(self):
        """Release Vecow handles; force all DO pins low first."""
        try:
            if self.initialized_io and self.other_dll is not None:
                self.set_do(0b00000000, bank=1)
                try:
                    self.set_do(0b00000000, bank=2)
                except Exception:
                    pass
                logger.info("Vecow DO cleared (all low) on close")
        except Exception:
            logger.exception("Failed clearing Vecow DO on close")
        self.initialized_io = False
        self.initialized_poe = False
        self.drv_dll = None
        self.other_drv_dll = None
        self.dll = None
        self.other_dll = None
