import pathlib
import sys
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "app"))  # resolves 'from Dependencies import ...'
sys.path.insert(0, str(_ROOT))

# Stub mqtt_client before main.py imports it (may not be installed in test env)
sys.modules.setdefault("mqtt_client", MagicMock())

# Suppress log-file creation during import
with patch("logging.basicConfig"):
    import main


# ─── helpers ─────────────────────────────────────────────────────────────────

def _mock_dio():
    dio = MagicMock()
    dio.set_do_pin.return_value = True
    return dio


# ─── decode_dio_values ────────────────────────────────────────────────────────

class TestDecodeDioValues:
    def test_csv_string(self):
        assert main.decode_dio_values("1,0,1,1") == [1, 0, 1, 1]

    def test_csv_with_spaces(self):
        assert main.decode_dio_values(" 1 , 0 , 1 ") == [1, 0, 1]

    def test_json_list(self):
        assert main.decode_dio_values("[1,0,1,1]") == [1, 0, 1, 1]

    def test_json_detection_dicts_uses_first_item_pins(self):
        payload = '[{"pins": [1,0,1,1], "y_location": 0.5}, {"pins": [0,1,0,0]}]'
        assert main.decode_dio_values(payload) == [1, 0, 1, 1]

    def test_json_dict_with_pins_key(self):
        assert main.decode_dio_values('{"pins": [1,0,1]}') == [1, 0, 1]

    def test_bytes_input_decoded(self):
        assert main.decode_dio_values(b"1,0,1,1") == [1, 0, 1, 1]

    def test_bytearray_input_decoded(self):
        assert main.decode_dio_values(bytearray(b"1,0,1")) == [1, 0, 1]

    def test_empty_json_array_returns_empty_list(self):
        assert main.decode_dio_values("[]") == []

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            main.decode_dio_values("")

    def test_empty_bytes_raises(self):
        with pytest.raises(ValueError, match="empty"):
            main.decode_dio_values(b"")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="JSON"):
            main.decode_dio_values("[1,0,not_valid")

    def test_json_dict_without_pins_raises(self):
        with pytest.raises(ValueError):
            main.decode_dio_values('{"values": [1,0,1]}')

    def test_non_numeric_csv_raises(self):
        with pytest.raises(ValueError):
            main.decode_dio_values("1,a,0")

    def test_python_list_coerced_via_str(self):
        # str([1, 0, 1]) == '[1, 0, 1]' which is valid JSON
        assert main.decode_dio_values([1, 0, 1]) == [1, 0, 1]


# ─── set_digital_io ───────────────────────────────────────────────────────────

class TestSetDigitalIo:
    _N = main.DIO_COUNT
    _B = main.DIO_BLOCK_SIZE

    def test_bank1_pins_written_correctly(self):
        dio = _mock_dio()
        values = [1] * self._B + [0] * (self._N - self._B)
        with patch("time.sleep"), patch("threading.Thread"):
            main.set_digital_io(values, dio, delay=0)
        for i in range(self._B):
            dio.set_do_pin.assert_any_call(1, i, 1)

    def test_bank2_pins_written_correctly(self):
        dio = _mock_dio()
        values = [0] * self._B + [1] * (self._N - self._B)
        with patch("time.sleep"), patch("threading.Thread"):
            main.set_digital_io(values, dio, delay=0)
        for i in range(self._N - self._B):
            dio.set_do_pin.assert_any_call(2, i, 1)

    def test_wrong_pin_count_raises(self):
        dio = _mock_dio()
        with pytest.raises(ValueError, match=str(self._N)):
            main.set_digital_io([1, 0, 1], dio, delay=0)

    def test_negative_delay_raises(self):
        dio = _mock_dio()
        with pytest.raises(ValueError):
            main.set_digital_io([0] * self._N, dio, delay=-1)

    def test_reset_thread_is_started(self):
        dio = _mock_dio()
        values = [0] * self._N
        reset_called = threading.Event()

        def fake_reset(*args, **kwargs):
            reset_called.set()

        with patch("main.reset_io_after_delay", side_effect=fake_reset):
            main.set_digital_io(values, dio, delay=0)

        assert reset_called.wait(timeout=2.0), "reset_io_after_delay was never called by the background thread"

    def test_total_pin_write_count_matches_dio_count(self):
        dio = _mock_dio()
        values = [1 if i % 2 == 0 else 0 for i in range(self._N)]
        with patch("time.sleep"), patch("threading.Thread"):
            main.set_digital_io(values, dio, delay=0)
        assert dio.set_do_pin.call_count == self._N


# ─── calculate_deltatime ─────────────────────────────────────────────────────

class TestCalculateDeltaTime:
    def test_past_time_returns_positive_float(self):
        five_sec_ago = (datetime.now() - timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')
        result = main.calculate_deltatime(five_sec_ago)
        assert result >= 4.9

    def test_current_time_returns_near_zero(self):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        result = main.calculate_deltatime(now)
        assert 0 <= result < 1.5

    def test_returns_float(self):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        assert isinstance(main.calculate_deltatime(ts), float)


# ─── set_io_delay ─────────────────────────────────────────────────────────────

class TestSetIoDelay:
    def test_defaults_return_five(self):
        assert main.set_io_delay() == pytest.approx(5.0)

    def test_double_speed_double_distance_same_delay(self):
        assert main.set_io_delay(conveyer_speed=2.0, distance_to_end=10.0) == pytest.approx(5.0)

    def test_fast_conveyer_short_delay(self):
        assert main.set_io_delay(conveyer_speed=5.0, distance_to_end=5.0) == pytest.approx(1.0)

    def test_slow_conveyer_long_delay(self):
        assert main.set_io_delay(conveyer_speed=0.5, distance_to_end=1.0) == pytest.approx(2.0)

    def test_zero_distance_gives_zero(self):
        assert main.set_io_delay(conveyer_speed=3.0, distance_to_end=0.0) == pytest.approx(0.0)


# ─── reset_io_after_delay ─────────────────────────────────────────────────────

class TestResetIoAfterDelay:
    _N = main.DIO_COUNT
    _B = main.DIO_BLOCK_SIZE

    def test_only_activated_bank1_pins_are_reset(self):
        dio = _mock_dio()
        values = [1, 0, 1] + [0] * (self._N - 3)  # pins 0 and 2 active
        with patch("time.sleep"):
            main.reset_io_after_delay(dio, 0, values)
        dio.set_do_pin.assert_any_call(1, 0, 0)
        dio.set_do_pin.assert_any_call(1, 2, 0)
        assert dio.set_do_pin.call_count == 2

    def test_bank2_pins_reset_with_correct_bank_offset(self):
        dio = _mock_dio()
        # global pin 8 → bank 2 pin 0; global pin 15 → bank 2 pin 7
        values = [0] * self._B + [1, 0, 0, 0, 0, 0, 0, 1]
        with patch("time.sleep"):
            main.reset_io_after_delay(dio, 0, values)
        dio.set_do_pin.assert_any_call(2, 0, 0)
        dio.set_do_pin.assert_any_call(2, 7, 0)
        assert dio.set_do_pin.call_count == 2

    def test_no_active_pins_means_no_reset_calls(self):
        dio = _mock_dio()
        with patch("time.sleep"):
            main.reset_io_after_delay(dio, 0, [0] * self._N)
        dio.set_do_pin.assert_not_called()

    def test_delay_value_is_passed_to_sleep(self):
        dio = _mock_dio()
        with patch("time.sleep") as mock_sleep:
            main.reset_io_after_delay(dio, 3, [0] * self._N)
        mock_sleep.assert_called_once_with(3)

    def test_all_active_pins_across_both_banks(self):
        dio = _mock_dio()
        values = [1] * self._N
        with patch("time.sleep"):
            main.reset_io_after_delay(dio, 0, values)
        assert dio.set_do_pin.call_count == self._N


# ─── setup_dio_control ────────────────────────────────────────────────────────

class TestSetupDioControl:
    def test_returns_vecowio_instance_on_success(self):
        mock_dio = MagicMock()
        with patch("main.VecowIO", return_value=mock_dio):
            result = main.setup_dio_control()
        assert result is mock_dio

    def test_raises_with_message_on_hardware_failure(self):
        with patch("main.VecowIO", side_effect=Exception("DLL not found")):
            with pytest.raises(Exception, match="Failed to initialize"):
                main.setup_dio_control()


# ─── start_subscribe_thread ───────────────────────────────────────────────────

class TestStartSubscribeThread:
    def test_returns_running_daemon_thread(self):
        q: MagicMock = MagicMock()
        stop = threading.Event()
        with patch("main.subscribe_listener"):
            t = main.start_subscribe_thread("localhost", 1883, "topic", q, stop)
        assert isinstance(t, threading.Thread)
        assert t.daemon is True
        stop.set()
