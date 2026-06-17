# ============================================================================
# Bytronic Inspect - Industrial Vision Inspection System
# Copyright (c) 2023-2024 Bytronic Ltd. All rights reserved.
#
# Version: A.0.0
# Last Updated: 2025-10-20
# 
# GitHub: https://github.com/bytronic/inspect
# Branch: main
# Commit: 9c1623d3
# 
# PROPRIETARY NOTICE
# This software is the property of Bytronic Ltd. and contains proprietary
# and confidential information. Unauthorized use, reproduction, distribution,
# or disclosure of this software or any portion thereof is strictly prohibited.
# 
# ============================================================================
import ctypes
from ctypes import *
import time
import os
import logging
import threading
from filelock import FileLock

logging.basicConfig(
    filename=f'./logs/dio_{time.strftime("%Y%m%d")}.log',
    level=logging.INFO,
    format='%(asctime)s - [PID %(process)d] - %(levelname)s - %(message)s',
    force=True,  # Force configuration even if the logger was previously configured
    filemode='a'  # Append mode instead of overwrite
)

class VecowIO:
    def __init__(self):
        self._lock = threading.Lock()
        # Load the DLLs
        try:
            drv_path = "./drv.dll"
            vecow_path = "./Vecow.dll"
            other_drv_path = "./drv_alim.dll"
            other_vecow_path = "./Vecow_alim.dll"
            # openhwmon_path = "./OpenHardwareMonitorLib.dll"

            # Load dependencies first
            self.drv_dll = ctypes.CDLL(drv_path) ## for poe
            self.other_drv_dll = ctypes.CDLL(other_drv_path) ## for dio
            # self.openhwmon_dll = ctypes.CDLL(openhwmon_path)
            self.dll = ctypes.CDLL(vecow_path) ## for power
            self.other_dll = ctypes.CDLL(other_vecow_path) ## for dio
            
            self.initialized_io = False
            self.initialize_io()

            # self.get_io_config()

            self.initialized_poe = False
            self.initialize_poe()

            # self.get_poe_config()
            
        except Exception as e:
            logging.info(f"Failed to load DLLs: {e}")
            self.dll = None
            self.drv_dll = None
            self.other_dll = None
            self.other_drv_dll = None
            
    def initialize_io(self):
        """Initialize the Vecow I/O system"""
        if not self.other_dll:
            return False
            
        try:
            # # Initialize SIO
            result = self.other_dll.initial_SIO(c_ubyte(1), c_ubyte(0))
            logging.info("result initial_SIO: ", result)

            # # Configure I/O
            for i in range(10):
                result1, result2 = self.set_io_config()
                logging.info("result1 set_io_config: ", result1, i)
                logging.info("result2 set_io_config: ", result2, i)
                self.get_io_config()
                if result1 and result2:
                    break
                time.sleep(0.1)
            
            self.initialized_io = True
            return True
        except Exception as e:
            logging.info(f"Initialization of io failed: {e}")
            return False
        
    def initialize_poe(self):
        """Initialize the Vecow I/O system"""
        if not self.dll:
            return False
            
        try:
            result = self.dll.Initial_POE(c_ubyte(1), c_ubyte(0))
            logging.info("result initial_POE: ", result)

            result = self.set_poe_config(0, 0b0000, 0b1111)
            logging.info("result set_poe_config: ", result)

            self.initialized_poe = True
            return True
        except Exception as e:
            logging.info(f"Initialization of poe failed: {e}")
            return False

    def set_io_config(self):
        """Configure digital outputs"""
        if not self.other_dll:
            return False
            
        try:
            DIOIso = c_ubyte(1)    # Isolated ##should be 1 ?????? 
            DIONPN = c_ubyte(0)    # NPN type
            DIONPNs = c_ubyte(1)   # NPN sink
            DIOM = c_ushort(0b1111111100000000)  # 65280 in binary 0b1111111100000000
            with FileLock('./dio_lock.lock'):
                result1 = self.other_dll.set_IO1_configuration(DIOIso, DIONPN, DIONPNs, DIOM)
                result2 = self.other_dll.set_IO2_configuration(DIOIso, DIONPN, DIONPNs, DIOM)
            return result1, result2
        except Exception as e:
            logging.info(f"Failed to configure DO: {e}")
            return False
        
    def get_io_config(self):
        """Configure digital inputs"""
        if not self.other_dll:
            return False
            
        try:
            DIOIso = c_ubyte()
            DIONPN = c_ubyte()   
            DIONPNs = c_ubyte()  
            DIOM = c_ushort()
            
            result1 = self.other_dll.get_IO1_configuration(byref(DIOIso), byref(DIONPN), byref(DIONPNs), byref(DIOM))
            result2 = self.other_dll.get_IO2_configuration(byref(DIOIso), byref(DIONPN), byref(DIONPNs), byref(DIOM))
            logging.info("result1 get_io_config: ", result1)
            logging.info("result2 get_io_config: ", result2)
            logging.info("DIOIso: ", DIOIso.value)
            logging.info("DIONPN: ", DIONPN.value)
            logging.info("DIONPNs: ", DIONPNs.value)
            logging.info("DIOM: ", DIOM.value)
            return result1, result2
        except Exception as e:
            logging.info(f"Failed to configure DI: {e}")
            return False
        
    def get_poe_config(self):
        """Configure digital inputs"""
        if not self.dll:
            return False
            
        try:
            first_byte = c_ubyte()
            second_byte = c_ubyte()   

            
            result = self.dll.GetPOEConfig(first_byte, byref(second_byte))
            logging.info("result get_poe_config: ", result)
            logging.info("first_byte: ", first_byte.value)
            logging.info("second_byte: ", second_byte.value)

            return result, second_byte.value
        except Exception as e:
            logging.info(f"Failed to get poe config: {e}")
            return False
        
    def set_poe_config(self, first_byte = 0, second_byte = 0, third_byte = 0):
        """Configure POE settings
        
        Sets up POE ports with:
        - Auto mode disabled (manual control)
        - All ports enabled for DC power
        - Third parameter appears required despite documentation
        """
        if not self.dll:
            return False
            
        try:
            auto = c_ubyte(first_byte)    # All ports in manual mode
            mask = c_ubyte(second_byte)    # Enable DC power on all ports (0b1111)
            other = c_ushort(third_byte)
            result = self.dll.SetPOEConfig(auto, mask, other)
            return result
        except Exception as e:
            logging.info(f"Failed to configure POE: {e}")
            return False

    def set_do1(self, value):
        """Set digital output value"""
        if not self.other_dll or not self.initialized_io:
            return False
            
        try:
            # Convert input to byte and apply mask
            do_value = c_ubyte(value & 0xFF)
            result = self.other_dll.set_DIO1(do_value)
            # logging.info("result set_dio1: ", result)
            # result = self.other_dll.set_GPIO1_(do_value)
            # logging.info("result set_gpio1: ", result)
            return result
        except Exception as e:
            logging.info(f"Failed to set DO: {e}")
            return False

    def set_do2(self, value):
        """Set digital output value"""
        if not self.other_dll or not self.initialized_io:
            return False
            
        try:
            # Convert input to byte and apply mask
            do_value = c_ubyte(value & 0xFF)
            result = self.other_dll.set_DIO2(do_value)
            # logging.info("result set_dio1: ", result)
            # result = self.other_dll.set_GPIO1_(do_value)
            # logging.info("result set_gpio1: ", result)
            return result
        except Exception as e:
            logging.info(f"Failed to set DO: {e}")
            return False

    def get_di1(self):
        """Read digital input value"""
        if not self.other_dll or not self.initialized_io:
            return None
            
        try:
            di = c_ubyte()
            di2 = c_ubyte()
            gpio = c_ushort()
            result = self.other_dll.get_DIO1(byref(di), byref(di2))
            # logging.info("result get_dio1: ", result, "di: ", di.value, "di2: ", di2.value)
            # result = self.other_dll.get_GPIO1(byref(gpio))
            # logging.info("result get_gpio1: ", result, "gpio: ", gpio.value)
            
            if result:
                return di.value, di2.value
            return None, None
        except Exception as e:
            logging.info(f"Failed to read DI: {e}")
            return None, None
        

    def get_di2(self):
        """Read digital input value"""
        if not self.other_dll or not self.initialized_io:
            return None
            
        try:
            di = c_ubyte()
            di2 = c_ubyte()
            gpio = c_ushort()
            result = self.other_dll.get_DIO2(byref(di), byref(di2))
            # logging.info("result get_dio1: ", result, "di: ", di.value, "di2: ", di2.value)
            # result = self.other_dll.get_GPIO1(byref(gpio))
            # logging.info("result get_gpio1: ", result, "gpio: ", gpio.value)
            
            if result:
                return di.value, di2.value
            return None, None
        except Exception as e:
            logging.info(f"Failed to read DI: {e}")
            return None, None


    def set_poe(self, value):
        """Set digital output value"""
        if not self.dll or not self.initialized_poe:
            return False
            
        try:
            # Convert input to byte and apply mask
            poe_value = c_ubyte(value & 0xFF)
            result = self.dll.SetPOE(c_ubyte(0), poe_value)
            logging.info("result set POE: ", result)
            return result
        except Exception as e:
            logging.info(f"Failed to set POE: {e}")
            return False
        
    def get_poe(self):
        """Read digital poe value"""
        if not self.dll or not self.initialized_poe:
            return None
            
        try:
            poe_0 = c_ubyte()
            poe_1 = c_ubyte()
            result = self.dll.GetPOE(byref(poe_0), byref(poe_1))
            logging.info("result get POE: ", result)
            if result:
                logging.info("poe_0.value: ", poe_0.value)
                logging.info("poe_1.value: ", poe_1.value)
                return poe_0.value
            return None
        except Exception as e:
            logging.info(f"Failed to read POE: {e}")
            return None
        
    def set_do_pin(self, bank, pin, value):
        """Set digital output value"""
        if not self.other_dll or not self.initialized_io:
            return False
        try:
            with self._lock:
                if bank == 1:
                    current_value, current_value2 = self.get_di1()
                elif bank == 2:
                    current_value, current_value2 = self.get_di2()
                else:
                    raise ValueError(f"bank must be either 1 or 2 got {bank}")
                current_value_bin = [int(bit) for bit in bin(current_value)[2:].zfill(8)][::-1] # Reverse for LSB first
                current_value_bin[pin] = value
                new_value = int("".join(map(str, current_value_bin[::-1])), 2) # Reverse back for int conversion
                if bank == 1:
                    self.set_do1(new_value)
                elif bank == 2:
                    self.set_do2(new_value)
                else:
                    raise ValueError(f"bank must be either 1 or 2 got {bank}")
            return True
        except Exception as e:
            logging.info(f"Failed to set DO independent: {e}")
            return False
        
    def set_do_pins(self, bank, pins, value):
        """Set digital output value"""
        if not self.other_dll or not self.initialized_io:
            return False

        try:
            with self._lock:
                if bank == 1:
                    current_value, current_value2 = self.get_di1()
                elif bank == 2:
                    current_value, current_value2 = self.get_di2()
                else:
                    raise ValueError(f"bank must be 1 or 2 got {bank}")
                current_value_bin = [int(bit) for bit in bin(current_value)[2:].zfill(8)][::-1] # Reverse for LSB first
                for pin in pins:
                    current_value_bin[pin] = value
                new_value = int("".join(map(str, current_value_bin[::-1])), 2) # Reverse back for int conversion
                if bank == 1:
                    self.set_do1(new_value)
                elif bank == 2:
                    self.set_do2(new_value)
                else:
                    raise ValueError(f"bank must be 1 or 2 got {bank}")
            return True
        except Exception as e:
            logging.info(f"Failed to set DO independent: {e}")
            return False

    def get_di_pin(self, bank, pin):
        """Read a single digital input pin state"""
        if not self.other_dll or not self.initialized_io:
            return False
        try:
            if bank == 1:
                current_value, current_value2 = self.get_di1()
            elif bank == 2:
                current_value, current_value2 = self.get_di2()
            else:
                raise ValueError(f"bank must be either 1 or 2 got {bank}")
            current_value2_bin = [int(bit) for bit in bin(current_value2)[2:].zfill(8)][::-1]
            return current_value2_bin[pin]
        except Exception as e:
            logging.info(f"Failed to get DI pin: {e}")
            return None, None
        
    def get_di_pins(self, bank, pins):
        """Read multiple digital input pin states"""
        if not self.other_dll or not self.initialized_io:
            return False
        try:
            if bank == 1:
                current_value, current_value2 = self.get_di1()
            elif bank == 2:
                current_value, current_value2 = self.get_di2()
            else:
                raise ValueError(f"bank must be either 1 or 2 got {bank}")
            current_value2_bin = [int(bit) for bit in bin(current_value2)[2:].zfill(8)][::-1]
            return [current_value2_bin[pin] for pin in pins]
        except Exception as e:
            logging.info(f"Failed to get DI pin: {e}")
            return None, None
        
    def get_do_pin(self, bank, pin):
        """Read a single digital output pin state"""
        if not self.other_dll or not self.initialized_io:
            return False
        try:
            if bank == 1:
                current_value, current_value2 = self.get_di1()
            elif bank == 2:
                current_value, current_value2 = self.get_di2()
            else:
                raise ValueError(f"bank must be either 1 or 2 got {bank}")
            current_value_bin = [int(bit) for bit in bin(current_value)[2:].zfill(8)][::-1]
            return current_value_bin[pin]
        except Exception as e:
            logging.info(f"Failed to get DI pin: {e}")
            return None, None

    def close(self):
        """Clean up resources"""
        self.initialized_io = False
        self.initialized_poe = False
        self.drv_dll = None
        self.other_drv_dll = None
        self.dll = None
        self.other_dll = None

if __name__ == "__main__":
    io = VecowIO()
    while True:
        for i in range(2):
            for j in range(8):
                logging.info(f"setting pin {i+1},{j} to 1")
                io.set_do_pin(i+1,j,1)
                time.sleep(0.05)
                io.set_do_pin(i+1,j,0)
                time.sleep(0.05)

    io.close()