from threading import Thread, Event
from mqtt_client import MQTTClient, MQTTConfig
from queue import Empty, Queue

def start_subscribe_thread(
        ip: str, 
        port: int, 
        topic: str, 
        queue: Queue, 
        stop_event: Event
        ) -> Thread:
    
    thread = Thread(
        target=subscribe_listener,
        args=(ip, port, topic, queue, stop_event),
        daemon=True,
    )
    thread.start()
    return thread

def subscribe_listener(
        ip: str, 
        port: int, 
        trigger_topic: str, 
        result_queue: Queue, 
        stop_event: Event
        )->None:
    
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
    return