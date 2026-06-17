from mqtt_client import MQTTClient, MQTTConfig
from Dependencies import loadConfig
import time
from queue import Empty, Queue
import threading
from Dependencies.dio_controller import VecowIO
import logging
from sys import getsizeof
import json

#this is a bit gross make it better in future by creating a config class that can be imported and used to get the values 
# instead of having them as global variables. This will also make it easier to test and mock the config values in future.
IP = loadConfig.return_config_value("ip")
PORT = loadConfig.return_config_value("port")
PUBLISH_TOPIC = loadConfig.return_config_value("complete_topic")
POSITION_TOPIC = loadConfig.return_config_value("position_topic")
DIO_COUNT = loadConfig.return_config_value("dio_count")
DIO_RESET_DELAY = loadConfig.return_config_value("dio_reset_delay")
DIO_BLOCK_SIZE = loadConfig.return_config_value("dio_block_size")

DIO_BANKS = DIO_COUNT // DIO_BLOCK_SIZE

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
    config = MQTTConfig(host=IP, port=PORT)
    client = MQTTClient(config)
    client.connect()
    print("client connected")

    def on_message(topic: str, payload: str) -> None:
        # Handler signature used by mqtt_client.MQTTClient.subscribe
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode('utf-8')
        result_queue.put(payload)

    client.subscribe(trigger_topic, on_message)

def reset_io_after_delay(dio_controller: VecowIO, delay: int):
    # This function will reset all digital IO to 0.
    # this will be better as a loop to make it more expanisve
    time.sleep(delay)
    for i in range(DIO_COUNT):
        if i < DIO_COUNT/DIO_BANKS:
            dio_controller.set_do_pin(1, i, 0)
        else:
            dio_controller.set_do_pin(2, i - int(DIO_COUNT/DIO_BANKS), 0)
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


async def listen_for_data(mqtt_client):
    payload = await mqtt_client.ListenForMessage()
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode('utf-8')
    return str(payload)


def set_digital_io(dio_values: list, dio_controller: VecowIO, delay:int = 0):
    # This function will set the digital IO on the PLC to trigger the capture of the image.
    # it returns nothing.
    time.sleep(delay)
    if len(dio_values) != DIO_COUNT:
        raise ValueError(f"Error: Expected {DIO_COUNT} digital IO values, but received {len(dio_values)}.")     
        
    for i in range(DIO_COUNT):
        # create a function to make this more expansive in future
        dio_state = dio_values[i]
        if i < DIO_COUNT/2:
            dio_controller.set_do_pin(1, i, dio_state)
        else:
            dio_controller.set_do_pin(2, i - int(DIO_COUNT/DIO_BANKS), dio_state)
    pass

def main():
    config = MQTTConfig(host=IP, port=PORT)
    client = MQTTClient(config)
    client.connect()

    event_queue = Queue()
    stop_event = threading.Event()
    subscribe_thread = start_subscribe_thread(IP, PORT, POSITION_TOPIC, event_queue, stop_event)

    dio_controller = setup_dio_control()
    try:
        while True:
            time.sleep(0.1)

            try:
                msg = event_queue.get_nowait()
                timestamp = 0
                #msg,timestamp = event_queue.get_nowait()
            except Empty:
                continue

            if msg is None:
                logging.info("Received invalid trigger payload; ignoring.")
                continue

            if msg is not None:
                set_digital_io(decode_dio_values(msg), dio_controller)
                start_time = int(timestamp)
                #end_time = int(time.strftime('%Y-%m-%d %H:%M:%S'))
                #delta_time = end_time-start_time
                #print(delta_time)

                completed = f"dio updated at{time.strftime('%Y-%m-%d %H:%M:%S')}"
                data_bytes = completed.encode('utf-8') 
                client.publish(PUBLISH_TOPIC, data_bytes)

    except KeyboardInterrupt:
        logging.info("Shutting down subscribe listener and exiting.")

    finally:
        stop_event.set()
        if subscribe_thread.is_alive():
            subscribe_thread.join(timeout=2)
if __name__ == "__main__":
    main()