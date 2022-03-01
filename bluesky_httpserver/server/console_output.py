import json
import logging
import queue
from starlette.responses import StreamingResponse

from bluesky_queueserver import ReceiveConsoleOutputAsync

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CollectPublishedConsoleOutput:
    """
    The class implements the code for collecting messages with console output data published by
    RE Manager and adding the messages to the queues in the set. The code is executed in a
    separate thread.

    Examples
    --------
    .. code-block:: python
        # Instantiate the class and start the thread
        gen_data = CollectPublishedConsoleOutput()
        gen_data.start()
        ...
        q = queue.Queue(maxsize=queue_maxsize)
        gen_data.queues_set.add(self._local_queue)
        ...
        get_data.stop()

    Parameters
    ----------
    zmq_addr : str or None
        0MQ address of the Queue Server socket where the console output messages are published.
        E.g. ``tcp://localhost:60625``. If ``None``, then default socket address is used.
    """

    def __init__(self, *, zmq_addr=None):
        self._queues_set = set()
        self._rco = ReceiveConsoleOutputAsync(zmq_subscribe_addr=zmq_addr)
        self._rco.set_callback(self._add_message)

        self._text_buffer_max = 2000
        self._text_buffer = []

    @property
    def queues_set(self):
        """
        Get reference to the set of queues. Received messages are added to each queue in
        in the set. Each independent consumer of messages is expected to add queue to the set.
        This class does not distinguish between consumers and treat all queues identically.
        """
        return self._queues_set

    @property
    def get_text_buffer(self, n_lines):
        return "".join(self._text_buffer[-n_lines:])

    def _add_to_text_buffer(self, msg):
        msg = msg["msg"]
        ends_with_new_line = msg.endswith("\n")
        line_list = msg.split("\n")
        if ends_with_new_line and len(line_list):
            if not line_list[-1]:
                line_list = line_list[:-1]
                line_list = [_ + "\n" for _ in line_list]
            else:
                line_list = [_ + "\n" for _ in line_list]
                line_list[-1] = line_list[-1].replace("\n", "")

        self._text_buffer.extend(line_list)

    def _add_message(self, msg):
        try:
            for q in self._queues_set.copy():
                # Consume one message if the queue is full. Setting the maximum
                #   queue size may save from memory leaks in case queue is not
                #   removed from the set due to a bug.
                if q.full():
                    q.get()

                q.put(msg)

            # Always add to text buffer
            self._add_to_text_buffer(msg)

        except Exception as ex:
            logger.exception("Exception occurred while adding console output message to queues: %s", str(ex))

    def start(self):
        """
        Start collection of messages. Must be called from the loop!!!
        """
        self._rco.start()

    def stop(self):
        """
        Stop collection of messages
        """
        self._rco.stop()


class ConsoleOutputEventStream:
    def __init__(self, *, queues_set, queue_maxsize=1000):
        self._queues_set = queues_set

        self._local_queue = queue.Queue(maxsize=queue_maxsize)
        self._queues_set.add(self._local_queue)

    def __call__(self):
        while True:
            try:
                message = self._local_queue.get(timeout=0.1)
                yield json.dumps(message)
            except queue.Empty:
                pass

    def __del__(self):
        self._queues_set.remove(self._local_queue)


class StreamingResponseFromClass(StreamingResponse):
    def __init__(self, content_class, *args, **kwargs):
        self._content = content_class
        super().__init__(content=content_class(), *args, **kwargs)

    def __del__(self):
        del self._content
