import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from app.Dependencies.dio_controller import VecowIO


class DummyVecowIO(VecowIO):
    def __init__(self, di1_value=0, di2_value=0):
        # Avoid hardware initialization in the parent constructor.
        pass


def make_dummy_vecowio(di1_value=0, di2_value=0):
    instance = VecowIO.__new__(VecowIO)
    instance.other_dll = True
    instance.initialized_io = True
    instance._lock = threading.Lock()

    def get_di1():
        return di1_value, 0

    def get_di2():
        return 0, di2_value

    instance.get_di1 = get_di1
    instance.get_di2 = get_di2
    instance.last_written = None

    def set_do1(value):
        instance.last_written = ("do1", value)
        return value

    def set_do2(value):
        instance.last_written = ("do2", value)
        return value

    instance.set_do1 = set_do1
    instance.set_do2 = set_do2
    return instance


def test_set_do_pin_bank1_writes_correct_value():
    instance = make_dummy_vecowio(di1_value=0)
    result = instance.set_do_pin(1, 0, 1)

    assert result is True
    assert instance.last_written == ("do1", 1)


def test_set_do_pin_bank2_writes_high_bit():
    instance = make_dummy_vecowio(di2_value=0)
    result = instance.set_do_pin(2, 7, 1)

    assert result is True
    assert instance.last_written == ("do2", 128)


def test_set_do_pin_invalid_bank_returns_false():
    instance = make_dummy_vecowio()

    result = instance.set_do_pin(3, 0, 1)

    assert result is False


def test_set_do_pins_multiple_bits_are_written():
    instance = make_dummy_vecowio(di1_value=0)
    result = instance.set_do_pins(1, [0, 2], 1)

    assert result is True
    assert instance.last_written == ("do1", 0b00000101)


def test_get_do_pin_returns_pin_state_from_current_value():
    instance = make_dummy_vecowio(di1_value=0b00000101)
    pin_state = instance.get_do_pin(1, 2)

    assert pin_state == 1


def test_get_di_pin_returns_pin_state_from_current_value2():
    instance = make_dummy_vecowio(di1_value=0, di2_value=0b00001000)
    pin_state = instance.get_di_pin(2, 3)

    assert pin_state == 1
