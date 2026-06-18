import time
from queue import Empty, Queue
import threading
import logging
import json
from datetime import datetime

from dependencies import loadConfig
from dependencies.single_result_class import *
from dependencies.mqtt_functions import *
from dependencies.dio_functions import *

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

def calculate_deltatime(starttime: str) -> float:
    start = datetime.strptime(starttime, '%Y-%m-%d %H:%M:%S')
    return (datetime.now() - start).total_seconds()

def set_io_delay(conveyer_speed:float=1, distance_to_end:float=5):
    """ calculates the delay based on the length of the conveyer in m
    and the speed of the conveyer in s
    returns the delay in seconds
    """
    
    delay = distance_to_end/conveyer_speed
    return delay

def main():
    config = MQTTConfig(host=IP, port=PORT)
    client = MQTTClient(config)
    client.connect()

    event_queue: Queue[str] = Queue()
    stop_event = threading.Event()
    subscribe_thread = start_subscribe_thread(
        IP, 
        PORT, 
        POSITION_TOPIC, 
        event_queue, 
        stop_event
        )

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

                raw_io_delay = set_io_delay()
                adjusted_delay = max(0.0, raw_io_delay - delta_time)

                threading.Thread(
                    target=set_digital_io,
                    args=(
                        [int(p) for p in pins], 
                        dio_controller, 
                        adjusted_delay,
                        DIO_COUNT,
                        DIO_BLOCK_SIZE),
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