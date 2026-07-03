from mqtt_client import MQTTClient, MQTTConfig
from Dependencies import loadConfig
import time
from queue import Empty, Queue
import threading
from Dependencies.dio_controller import VecowIO
import logging
import json
from datetime import datetime
from dataclasses import dataclass

@dataclass
class SingleResult:
    pins: list[int]
    y_location: float
    capture_time: str

#this is a bit gross make it better in future by creating a config class that can be imported and used to get the values 
# instead of having them as global variables. This will also make it easier to test and mock the config values in future.
IP = loadConfig.return_config_value("ip")
PORT = loadConfig.return_config_value("port")
PUBLISH_TOPIC = loadConfig.return_config_value("complete_topic")
POSITION_TOPIC = loadConfig.return_config_value("position_topic")
DIO_COUNT = loadConfig.return_config_value("dio_count")
DIO_RESET_DELAY = loadConfig.return_config_value("dio_reset_delay")
DIO_BLOCK_SIZE = loadConfig.return_config_value("dio_block_size")

if DIO_COUNT % DIO_BLOCK_SIZE != 0:
    raise ValueError(f"Error: dio_count must be a multiple of dio_block_size. Received dio_count={DIO_COUNT} and dio_block_size={DIO_BLOCK_SIZE}.")

logging.basicConfig(
    filename=f'./logs/dio_{time.strftime("%Y%m%d")}.log',
    level=logging.INFO,
    format='%(asctime)s - [PID %(process)d] - %(levelname)s - %(message)s',
    force=True,  # Force configuration even if the logger was previously configured
    filemode='a'  # Append mode instead of overwrite
)

def setup_dio_control():
    try:
        dio = VecowIO()
        logging.info("Digital I/O controller initialized successfully")
        return dio
    except Exception as e:
        raise Exception(f"Failed to initialize Digital I/O controller: {e}")
    
def start_subscribe_thread(ip: str, port: int, topic: str, queue: Queue, stop_event: threading.Event) -> threading.Thread:
    thread = threading.Thread(
        target=subscribe_listener,
        args=(ip, port, topic, queue, stop_event),
        daemon=True,
    )
    thread.start()
    return thread

def subscribe_listener(ip: str, port: int, trigger_topic: str, result_queue: Queue, stop_event: threading.Event):
    config = MQTTConfig(host=ip, port=port)
    client = MQTTClient(config)
    client.connect()
    print("client connected")

    def on_message(topic: str, payload: str) -> None:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode('utf-8')
        result_queue.put(payload)

    client.subscribe(trigger_topic, on_message)
    stop_event.wait()

def reset_io_after_delay(dio_controller: VecowIO, delay: int, dio_values: list):
    # Only resets pins that were activated, so concurrent detections don't
    # clobber each other's outputs.
    time.sleep(delay)
    for i in range(DIO_COUNT):
        if dio_values[i]:
            if i < DIO_BLOCK_SIZE:
                dio_controller.set_do_pin(1, i, 0)
            else:
                dio_controller.set_do_pin(2, i - DIO_BLOCK_SIZE, 0)
    logging.info(f"Digital IO reset to 0 after delay of. {delay} seconds.")
    return

def decode_dio_values(values):
    '''Decode the digital IO values received from the PLC.

    Supports a comma-separated string of integers like "1,0,1,1".
    Also supports a JSON-encoded payload such as a list of detection dicts:
        [{"pins": [...], "y_location": ...}, ...]
    '''
    if isinstance(values, (bytes, bytearray)):
        values = values.decode('utf-8')

    if not isinstance(values, str):
        values = str(values)

    values = values.strip()

    if not values:
        raise ValueError("Digital IO values payload is empty")

    if values[0] in '[{':
        try:
            payload = json.loads(values)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Unable to decode JSON payload: {values}") from exc

        if isinstance(payload, list):
            if not payload:
                return []
            first = payload[0]
            if isinstance(first, dict) and "pins" in first:
                return [int(item) for item in first["pins"]]
            return [int(item) for item in payload]

        if isinstance(payload, dict):
            if "pins" in payload:
                return [int(item) for item in payload["pins"]]
            raise ValueError("JSON payload dict did not contain 'pins'")

        raise ValueError("JSON payload did not contain a valid list of digital IO values")

    parts = [part.strip() for part in values.split(",") if part.strip()]
    try:
        return [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Invalid integer in digital IO values: {values}") from exc

def set_digital_io(dio_values: list, dio_controller: VecowIO, delay: float = 0):
    # This function will set the digital IO on the PLC to trigger the capture of the image.
    # it returns nothing.
    if delay < 0:
        raise ValueError(f"delay of {delay} is not valid and must be above 0")
    time.sleep(delay)
    if len(dio_values) != DIO_COUNT:
        raise ValueError(f"Error: Expected {DIO_COUNT} digital IO values, but received {len(dio_values)}.")     
        
    for i in range(DIO_COUNT):
        # create a function to make this more expansive in future
        dio_state = dio_values[i]
        if i < DIO_BLOCK_SIZE:
            dio_controller.set_do_pin(1, i, dio_state)
        else:
            dio_controller.set_do_pin(2, i - DIO_BLOCK_SIZE, dio_state)

    threading.Thread(
        target=reset_io_after_delay,
        args=(dio_controller, DIO_RESET_DELAY, dio_values),
        daemon=True,
    ).start()

def calculate_deltatime(starttime: str) -> float:
    start = datetime.strptime(starttime, '%Y-%m-%d %H:%M:%S.%f')
    return (datetime.now() - start).total_seconds()


def main():
    config = MQTTConfig(host=IP, port=PORT)
    client = MQTTClient(config)
    client.connect()

    event_queue: Queue[str] = Queue()
    stop_event = threading.Event()
    subscribe_thread = start_subscribe_thread(IP, PORT, POSITION_TOPIC, event_queue, stop_event)

    dio_controller = setup_dio_control()
    try:
        while True:
            try:
                msg = event_queue.get(timeout=1.0)
                start_time = time.time()
            except Empty:
                continue

            if msg is None:
                logging.info("Received invalid trigger payload; ignoring.")
                continue

            try:
                detections: list[dict] = json.loads(msg)
                if not isinstance(detections, list):
                    detections = [detections]
            except (json.JSONDecodeError, TypeError):
                logging.warning("Non-JSON payload; falling back to CSV parse.")
                detections = [{"pins": decode_dio_values(msg), "capture_time": None}]

            for detection in detections:
                pins = detection.get("pins")
                if pins is None:
                    logging.warning("Detection missing 'pins' key; skipping.")
                    continue

                capture_time = detection.get("capture_time")
                delta_time = 0.0
                if capture_time:
                    delta_time = calculate_deltatime(capture_time)
                    logging.info(f"Capture-to-DIO latency: {delta_time:.1f}s")

                travel_delay = float(detection.get("travel_delay", 0.0))
                adjusted_delay = max(0.0, travel_delay - delta_time)

                threading.Thread(
                    target=set_digital_io,
                    args=([int(p) for p in pins], dio_controller, adjusted_delay),
                    daemon=True,
                ).start()
                

            completed = f"dio updated at {time.strftime('%Y-%m-%d %H:%M:%S')}"
            data_bytes = completed.encode('utf-8')
            client.publish(PUBLISH_TOPIC, data_bytes)
            print(f"IO operations took a total of {time.time()-start_time}")

    except KeyboardInterrupt:
        logging.info("Shutting down subscribe listener and exiting.")

    finally:
        stop_event.set()
        if subscribe_thread.is_alive():
            subscribe_thread.join(timeout=2)
if __name__ == "__main__":
    main()