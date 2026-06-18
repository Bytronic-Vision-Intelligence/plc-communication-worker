import logging
from dependencies.dio_controller import VecowIO
from time import sleep
from threading import Thread
from json import loads, JSONDecodeError

def setup_dio_control() -> VecowIO:
    """ sets the dio controller and returns an instance if successful"""
    try:
        dio = VecowIO()
        logging.info("Digital I/O controller initialized successfully")
        return dio
    except Exception as e:
        raise Exception(f"Failed to initialize Digital I/O controller: {e}")
    
def reset_io_after_delay(
        dio_controller: VecowIO,
        delay: int, 
        dio_values: list,
        dio_count:int=0,
        dio_block_size:int = 0
        )-> None:
    """Resets the io of a given vecow instance after a set delay"""

    sleep(delay)
    for i in range(dio_count):
        if dio_values[i]:
            if i < dio_block_size:
                dio_controller.set_do_pin(1, i, 0)
            else:
                dio_controller.set_do_pin(2, i - dio_block_size, 0)

    logging.info(f"Digital IO reset to 0 after delay of. {delay} seconds.")

def set_digital_io(
        dio_values: list[bool], 
        dio_controller: VecowIO, 
        delay:int = 0,
        dio_count:int = 8,
        dio_block_size:int = 2,
        does_reset_io:bool=True
        )-> None:
    """ This function will set the digital IO on the Vecow based on a list of boolean values after a dely has been processed
    once set the function will call another thread to handle the resetting of the io if needed"""

    if delay < 0:
        raise ValueError(f"delay of {delay} is not valid and must be above 0")
    sleep(delay)
    if len(dio_values) != dio_count:
        raise ValueError(f"Error: Expected {dio_count} digital IO values, but received {len(dio_values)}.")     
        
    for i in range(dio_count):
        # create a function to make this more expansive in future
        dio_state = dio_values[i]
        if i < dio_block_size:
            dio_controller.set_do_pin(1, i, dio_state)
        else:
            dio_controller.set_do_pin(2, i - dio_block_size, dio_state)

    if not does_reset_io:
        return
    
    Thread(
        target=reset_io_after_delay,
        args=(dio_controller, dio_block_size, dio_values),
        daemon=True,
    ).start()

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
            payload = loads(values)
        except JSONDecodeError as exc:
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